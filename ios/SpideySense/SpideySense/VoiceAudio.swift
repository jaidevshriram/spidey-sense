import AVFoundation
import Foundation
import Observation

// v3.5 audio: Spidey's spoken explanations (playback) and "Hold to tell Spidey" (recording).
//
// One shared AVAudioSession, activated only while something is playing or recording and deactivated right after
// with .notifyOthersOnDeactivation, so the user's music / podcast comes back at full volume.
// - Playback: .playback + .voicePrompt, ducking other audio (spoken audio such as podcasts is paused instead of
//   talked over). The .playback category routes to AirPods / Bluetooth headphones like any media app.
// - Recording: .playAndRecord with Bluetooth HFP allowed (the AirPods mic works) and the speaker as the default
//   output. Any line Spidey is speaking is stopped first; new lines are dropped while the button is held.

@MainActor
enum SpideyAudioSession {
    /// True while "Hold to tell Spidey" owns the session.
    static private(set) var recording = false

    static func activateForPlayback() -> Bool {
        guard !recording else { return false }
        let s = AVAudioSession.sharedInstance()
        do {
            try s.setCategory(.playback, mode: .voicePrompt,
                              options: [.duckOthers, .interruptSpokenAudioAndMixWithOthers])
            try s.setActive(true)
            return true
        } catch {
            #if DEBUG
            print("[voice] playback session failed: \(error)")
            #endif
            return false
        }
    }

    static func activateForRecording() throws {
        let s = AVAudioSession.sharedInstance()
        try s.setCategory(.playAndRecord, mode: .default, options: [.allowBluetoothHFP, .defaultToSpeaker, .duckOthers])
        try s.setActive(true)
        recording = true
    }

    /// Hand the audio back to other apps.
    static func deactivate() {
        recording = false
        do { try AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
        catch {
            #if DEBUG
            print("[voice] deactivate: \(error)")
            #endif
        }
    }
}

// MARK: - Playback

/// Plays `speech` lines. Newest wins: a new line stops the one still playing.
@MainActor @Observable
final class SpeechPlayer: NSObject, AVAudioPlayerDelegate {
    /// The event the current line is about (`speech.event_id`), for the "speaking" glyph on its card.
    private(set) var speakingEventID: String?
    private(set) var isSpeaking = false
    private(set) var text: String?

    @ObservationIgnored private var player: AVAudioPlayer?
    @ObservationIgnored private var watchdog: Task<Void, Never>?

    func play(_ audio: Data, eventID: String?, text: String?, format: String?) {
        player?.stop()          // newest wins (the session stays active for the new line)
        player = nil
        watchdog?.cancel()
        let hint = (format ?? "mp3").lowercased() == "mp3" ? AVFileType.mp3.rawValue : nil
        guard let p = try? AVAudioPlayer(data: audio, fileTypeHint: hint), SpideyAudioSession.activateForPlayback() else {
            finish(); return
        }
        p.delegate = self
        p.prepareToPlay()
        guard p.play() else { finish(); return }
        player = p
        speakingEventID = eventID
        self.text = text
        isSpeaking = true
        // Safety net: an interruption (a call, Siri) pauses the player without a delegate callback.
        let id = ObjectIdentifier(p)
        let limit = p.duration + 1.5
        watchdog = Task { [weak self] in
            try? await Task.sleep(for: .seconds(limit))
            guard let self, !Task.isCancelled, let cur = self.player, ObjectIdentifier(cur) == id else { return }
            if !cur.isPlaying { self.stop() }
        }
    }

    /// Stop now (a new recording, or the user muted Spidey).
    func stop() {
        guard player != nil || isSpeaking else { return }
        player?.stop()
        finish()
    }

    private func finish() {
        player = nil
        watchdog?.cancel(); watchdog = nil
        isSpeaking = false
        speakingEventID = nil
        text = nil
        if !SpideyAudioSession.recording { SpideyAudioSession.deactivate() }
    }

    nonisolated func audioPlayerDidFinishPlaying(_ p: AVAudioPlayer, successfully flag: Bool) {
        let id = ObjectIdentifier(p)
        Task { @MainActor in self.finished(id) }
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(_ p: AVAudioPlayer, error: Error?) {
        let id = ObjectIdentifier(p)
        Task { @MainActor in self.finished(id) }
    }

    private func finished(_ id: ObjectIdentifier) {
        guard let cur = player, ObjectIdentifier(cur) == id else { return }   // already replaced by a newer line
        finish()
    }
}

// MARK: - Recording

/// "Hold to tell Spidey": AAC m4a, 16 kHz mono, at most 15 s, with a live level for the meter.
@MainActor @Observable
final class VoiceRecorder: NSObject, AVAudioRecorderDelegate {
    enum State: Equatable { case idle, starting, recording }

    static let maxDuration: TimeInterval = 15
    /// Shorter than this is treated as a tap, not a request.
    static let minDuration: TimeInterval = 0.6

    private(set) var state: State = .idle
    /// Smoothed input level, 0...1.
    private(set) var level: Double = 0
    private(set) var elapsed: TimeInterval = 0
    /// The user said no to the microphone (Settings > Spidey Sense > Microphone).
    private(set) var denied = false

    /// Called with the recording (nil on failure) and its length in seconds.
    @ObservationIgnored var onFinish: ((Data?, TimeInterval) -> Void)?
    /// Called just before the microphone opens (stop any line Spidey is speaking).
    @ObservationIgnored var onWillRecord: (() -> Void)?

    @ObservationIgnored private var recorder: AVAudioRecorder?
    @ObservationIgnored private var meter: Timer?
    @ObservationIgnored private var stopRequested = false

    private var fileURL: URL { FileManager.default.temporaryDirectory.appendingPathComponent("spidey-voice.m4a") }

    var isRecording: Bool { state == .recording }
    var remaining: TimeInterval { max(0, Self.maxDuration - elapsed) }

    func start() async {
        guard state == .idle else { return }
        state = .starting
        stopRequested = false
        let granted = await AVAudioApplication.requestRecordPermission()
        denied = !granted
        guard granted, !stopRequested else { state = .idle; return }   // released while the prompt was up
        onWillRecord?()
        do {
            try SpideyAudioSession.activateForRecording()
            try? FileManager.default.removeItem(at: fileURL)
            let r = try AVAudioRecorder(url: fileURL, settings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: 16_000,
                AVNumberOfChannelsKey: 1,
                AVEncoderBitRateKey: 32_000,
                AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
            ])
            r.delegate = self
            r.isMeteringEnabled = true
            guard r.record(forDuration: Self.maxDuration) else { throw CocoaError(.fileWriteUnknown) }
            recorder = r
            elapsed = 0; level = 0
            state = .recording
            startMeter()
        } catch {
            #if DEBUG
            print("[voice] can't record: \(error)")
            #endif
            teardown()
            onFinish?(nil, 0)
        }
    }

    /// Finger lifted: stop and hand the audio over.
    func stop() {
        if state == .starting { stopRequested = true; return }
        guard let r = recorder else { return }
        let secs = r.currentTime
        r.stop()
        deliver(secs)
    }

    /// Screenshot hook: show the recording state without a microphone.
    func showDemo(level: Double = 0.62, elapsed: TimeInterval = 4.2) {
        state = .recording; self.level = level; self.elapsed = elapsed
    }

    private func deliver(_ secs: TimeInterval) {
        recorder = nil
        teardown()
        let data = try? Data(contentsOf: fileURL)
        try? FileManager.default.removeItem(at: fileURL)
        onFinish?(data, secs)
    }

    private func teardown() {
        meter?.invalidate(); meter = nil
        state = .idle
        level = 0
        if SpideyAudioSession.recording { SpideyAudioSession.deactivate() }
    }

    private func startMeter() {
        let t = Timer(timeInterval: 1.0 / 30, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        RunLoop.main.add(t, forMode: .common)   // keeps metering while a scroll view is tracking
        meter = t
    }

    private func tick() {
        guard let r = recorder else { return }
        r.updateMeters()
        let db = Double(r.averagePower(forChannel: 0))          // -160 (silence) ... 0 dBFS
        let lin = max(0, min(1, (db + 50) / 45))
        level = level * 0.55 + lin * 0.45
        elapsed = r.currentTime
    }

    /// Fires when the 15 s limit stops the recorder by itself.
    nonisolated func audioRecorderDidFinishRecording(_ r: AVAudioRecorder, successfully flag: Bool) {
        let id = ObjectIdentifier(r)
        Task { @MainActor in
            guard let cur = self.recorder, ObjectIdentifier(cur) == id else { return }   // stop() already delivered
            self.deliver(Self.maxDuration)
        }
    }
}
