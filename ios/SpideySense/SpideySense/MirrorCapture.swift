import CoreImage
import Foundation
import ImageIO
import Observation
import os
import ReplayKit
import UIKit
import UniformTypeIdentifiers

/// v3.6 demo mirroring: ReplayKit in-app capture (no broadcast extension, so only this app is shared,
/// and only while it is in the foreground). Frames are throttled (~8 fps), downscaled (~540 px wide),
/// JPEG-encoded and sent over the existing engine WebSocket as `mirror.frame`; `mirror.state` brackets a session.
///
/// Runs while: the Settings toggle is on AND the app is active AND the WebSocket is connected.
/// It stops itself when any of those goes false and restarts when they all hold again.
@MainActor @Observable
final class MirrorCapture {
    static let shared = MirrorCapture()
    /// @AppStorage key of the Settings toggle (default off).
    static let enabledKey = "mirrorScreen"

    // Tunables.
    @ObservationIgnored var maxFPS: Double = 8 { didSet { worker.setMaxFPS(maxFPS) } }

    /// Capture is live and frames are flowing out.
    private(set) var running = false
    /// Waiting on startCapture (the one-time consent alert may be up).
    private(set) var starting = false
    /// Frames actually sent in the last second.
    private(set) var fps = 0
    /// Average wire size (JSON text) of the frames sent in the last second, KB.
    private(set) var frameKB = 0
    /// Friendly problem line for Settings (nil when fine).
    private(set) var message: String?

    @ObservationIgnored private weak var stream: EventStream?
    @ObservationIgnored private let worker = MirrorFrameWorker()
    @ObservationIgnored private var observers: [NSObjectProtocol] = []
    @ObservationIgnored private var appActive = UIApplication.shared.applicationState != .background
    @ObservationIgnored private var statsTask: Task<Void, Never>?
    @ObservationIgnored private var sentThisSecond = 0
    @ObservationIgnored private var bytesThisSecond = 0

    private var enabled: Bool { UserDefaults.standard.bool(forKey: Self.enabledKey) }

    private init() {}

    /// Called by EventStream.connect(to:). Idempotent; wires app lifecycle + connection status.
    func attach(_ stream: EventStream) {
        let first = self.stream == nil
        self.stream = stream
        guard first else { reconcile(); return }
        worker.setMaxFPS(maxFPS)
        worker.onEncoded = { [weak self] text, wire in
            Task { @MainActor in self?.deliver(text, wire: wire) }
        }
        let nc = NotificationCenter.default
        observers.append(nc.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.appActive = false; self?.reconcile() }
        })
        observers.append(nc.addObserver(forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.appActive = true; self?.reconcile() }
        })
        watchStatus()
        reconcile()
    }

    /// Settings toggle changed.
    func setEnabled(_ on: Bool) {
        if on { message = nil }
        reconcile()
    }

    // MARK: - State machine

    private func watchStatus() {
        guard let stream else { return }
        withObservationTracking { _ = stream.status } onChange: { [weak self] in
            Task { @MainActor in self?.reconcile(); self?.watchStatus() }
        }
    }

    private func reconcile() {
        let want = enabled && appActive && (stream?.isLive ?? false)
        if want, !running, !starting { start() }
        else if !want, running || starting { stop() }
    }

    private func start() {
        let recorder = RPScreenRecorder.shared()
        guard recorder.isAvailable else {
            fail("Screen sharing isn't available right now (it may be in use by another app or turned off in Screen Time).")
            return
        }
        recorder.isMicrophoneEnabled = false
        recorder.isCameraEnabled = false
        starting = true
        worker.reset()
        let worker = self.worker
        recorder.startCapture(handler: { sample, type, error in
            guard error == nil, type == .video else { return }
            worker.offer(sample)
        }, completionHandler: { [weak self] error in
            Task { @MainActor in self?.didStart(error) }
        })
    }

    private func didStart(_ error: Error?) {
        starting = false
        if let error {
            let code = (error as NSError).domain == RPRecordingErrorDomain ? RPRecordingErrorCode(rawValue: (error as NSError).code) : nil
            switch code {
            case .userDeclined?:
                fail("Screen sharing wasn't allowed. Turn this on again and tap \u{201C}Allow\u{201D} to show the app on the Mac.")
            case .disabled?:
                fail("Screen sharing is turned off on this iPhone (Screen Time or a profile).")
            default:
                fail("Couldn't start sharing the screen: \(error.localizedDescription)")
            }
            return
        }
        // Conditions may have changed while the consent alert was up.
        guard enabled, appActive, stream?.isLive == true else { stopCapture(); return }
        running = true
        message = nil
        stream?.sendMirrorState(true)
        startStats()
    }

    private func stop() {
        let wasRunning = running
        running = false
        starting = false
        stopCapture()
        if wasRunning { stream?.sendMirrorState(false) }
        statsTask?.cancel(); statsTask = nil
        fps = 0; frameKB = 0
    }

    private func stopCapture() {
        worker.reset()
        let recorder = RPScreenRecorder.shared()
        if recorder.isRecording { recorder.stopCapture { _ in } }
    }

    /// Turns the toggle off (so it doesn't loop on retries) and shows a friendly line.
    private func fail(_ text: String) {
        message = text
        UserDefaults.standard.set(false, forKey: Self.enabledKey)
        stop()
    }

    // MARK: - Sending

    private func deliver(_ text: String, wire: Int) {
        guard running, let stream else { worker.sent(ok: false); return }
        let worker = self.worker
        let ok = stream.sendMirrorFrame(text) { ok in worker.sent(ok: ok) }
        guard ok else { worker.sent(ok: false); return }
        sentThisSecond += 1
        bytesThisSecond += wire
    }

    private func startStats() {
        statsTask?.cancel()
        sentThisSecond = 0; bytesThisSecond = 0
        statsTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard let self, !Task.isCancelled else { return }
                self.fps = self.sentThisSecond
                self.frameKB = self.sentThisSecond > 0 ? self.bytesThisSecond / self.sentThisSecond / 1024 : 0
                self.sentThisSecond = 0; self.bytesThisSecond = 0
            }
        }
    }
}

/// Off-main frame pipeline: throttle, latest-wins (one frame in flight, never queued), downscale, JPEG, JSON.
/// ReplayKit calls `offer` on its own queue; encoding happens on `queue` with one reused CIContext.
final class MirrorFrameWorker: @unchecked Sendable {
    /// Output width in pixels (aspect kept). ~540 px reads well on the Mac's demo page.
    static let targetWidth: CGFloat = 540
    /// Wire budget per frame (the JSON text, base64 included).
    static let budgetBytes = 60 * 1024

    var onEncoded: (@Sendable (String, Int) -> Void)?

    private let lock = NSLock()
    private var busy = false
    private var lastAccepted: CFTimeInterval = 0
    private var minInterval: CFTimeInterval = 1.0 / 8
    private var generation = 0
    private var quality: CGFloat = 0.55          // adapted between 0.35 and 0.55 to stay under budget
    private var logCount = 0

    private let queue = DispatchQueue(label: "spidey.mirror.encode", qos: .userInitiated)
    private let context = CIContext(options: [.cacheIntermediates: false])
    private let encoder: JSONEncoder = { let e = JSONEncoder(); e.outputFormatting = [.withoutEscapingSlashes]; return e }()
    private let log = Logger(subsystem: "com.jaidev.spideysense", category: "mirror")

    func setMaxFPS(_ fps: Double) { lock.withLock { minInterval = 1.0 / max(1, fps) } }

    /// Drops any in-flight frame's bookkeeping (start / stop).
    func reset() { lock.withLock { busy = false; lastAccepted = 0; generation += 1 } }

    /// Send finished (or failed): the next frame may go.
    func sent(ok: Bool) { lock.withLock { busy = false } }

    func offer(_ sample: CMSampleBuffer) {
        let now = CACurrentMediaTime()
        let gen: Int? = lock.withLock {
            guard !busy, now - lastAccepted >= minInterval else { return nil }
            busy = true; lastAccepted = now
            return generation
        }
        guard let gen, let pixels = CMSampleBufferGetImageBuffer(sample) else {
            if gen != nil { sent(ok: false) }
            return
        }
        var orientation = CGImagePropertyOrientation.up
        if let n = CMGetAttachment(sample, key: RPVideoSampleOrientationKey as CFString, attachmentModeOut: nil) as? NSNumber,
           let o = CGImagePropertyOrientation(rawValue: n.uint32Value) {
            orientation = o
        }
        queue.async { [self] in encode(pixels, orientation: orientation, gen: gen) }
    }

    private func encode(_ pixels: CVPixelBuffer, orientation: CGImagePropertyOrientation, gen: Int) {
        let t0 = CACurrentMediaTime()
        var image = CIImage(cvPixelBuffer: pixels).oriented(orientation)
        let scale = min(1, Self.targetWidth / image.extent.width)
        image = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        let rect = image.extent.integral
        let q = lock.withLock { quality }
        guard let cg = context.createCGImage(image, from: rect),
              let jpeg = Self.jpeg(cg, quality: q) else { sent(ok: false); return }
        let frame = MirrorFrame(jpegB64: jpeg.base64EncodedString(), w: cg.width, h: cg.height,
                                ts: Date().timeIntervalSince1970)
        guard let data = try? encoder.encode(frame), let text = String(data: data, encoding: .utf8) else {
            sent(ok: false); return
        }
        let wire = data.count
        let (stale, shouldLog): (Bool, Bool) = lock.withLock {
            // Keep each frame under budget: step quality down when over, creep back up when well under.
            if wire > Self.budgetBytes { quality = max(0.35, quality - 0.05) }
            else if wire < Self.budgetBytes * 6 / 10 { quality = min(0.55, quality + 0.02) }
            logCount += 1
            return (gen != generation, logCount % 40 == 1)
        }
        if shouldLog {
            log.debug("mirror frame \(cg.width)x\(cg.height) jpeg \(jpeg.count / 1024) KB, wire \(wire / 1024) KB (budget 60), q \(String(format: "%.2f", q)), encode \(Int((CACurrentMediaTime() - t0) * 1000)) ms")
        }
        guard !stale else { return }   // capture restarted meanwhile; reset() already cleared busy
        onEncoded?(text, wire)
    }

    private static func jpeg(_ cg: CGImage, quality: CGFloat) -> Data? {
        let out = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(out, UTType.jpeg.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(dest, cg, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { return nil }
        return out as Data
    }
}
