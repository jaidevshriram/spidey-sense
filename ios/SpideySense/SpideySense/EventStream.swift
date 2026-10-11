import Foundation
import Observation

/// WebSocket client: connects, receives hello/event/rules/devices frames, sends app messages,
/// auto-reconnects with backoff. Until the engine says hello, rules and devices are sample data
/// (see MockData) so the UI still demos; views label them as such.
@MainActor @Observable
final class EventStream {
    enum Status { case disconnected, connecting, connected }

    private(set) var events: [HapticEvent] = []   // newest first
    private(set) var status: Status = .disconnected
    private(set) var lastError: String?

    // Engine state mirrored from the Mac (sample data until the first hello).
    private(set) var rules: [SenseRule] = MockData.rules
    private(set) var devices: [DeviceInfo] = MockData.devices
    private(set) var settings = EngineSettings()
    /// True until a hello with rules arrives from a real engine.
    private(set) var rulesAreSample = true
    private(set) var devicesAreSample = true
    private(set) var devicesUpdatedAt: Date?
    /// Last ack / error from the engine, shown as a toast-ish line in Settings.
    private(set) var lastAck: String?
    /// Local record of Useful/Noisy swipes, keyed by HapticEvent.key.
    private(set) var verdicts: [String: Verdict] = [:]

    private var url: URL?
    private var task: URLSessionWebSocketTask?
    private var loop: Task<Void, Never>?
    private var seen = Set<String>()
    private let maxEvents = 500

    var isLive: Bool { status == .connected }

    func connect(to urlString: String) {
        loop?.cancel()
        task?.cancel(with: .goingAway, reason: nil)
        url = URL(string: urlString.trimmingCharacters(in: .whitespaces))
        guard let url else { status = .disconnected; lastError = "Invalid URL"; return }
        loop = Task { await run(url) }
    }

    private func run(_ url: URL) async {
        var backoff = 1.0
        while !Task.isCancelled {
            status = .connecting
            let ws = URLSession.shared.webSocketTask(with: url)
            task = ws
            ws.resume()
            do {
                while !Task.isCancelled {
                    let msg = try await ws.receive()
                    if status != .connected { status = .connected; lastError = nil; backoff = 1.0 }
                    switch msg {
                    case .string(let s): handle(Data(s.utf8))
                    case .data(let d): handle(d)
                    @unknown default: break
                    }
                }
            } catch {
                lastError = error.localizedDescription
            }
            ws.cancel(with: .goingAway, reason: nil)
            if task === ws { task = nil }
            if Task.isCancelled { break }
            status = .disconnected
            devicesAreSample = true   // device cards fall back to sample data while offline
            try? await Task.sleep(for: .seconds(backoff))
            backoff = min(backoff * 2, 5)
        }
    }

    private func handle(_ data: Data) {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        let dec = JSONDecoder()
        switch obj["type"] as? String {
        case "hello":
            for e in obj["history"] as? [[String: Any]] ?? [] { add(e) }
            if let h = try? dec.decode(HelloMessage.self, from: data) {
                if let r = h.rules { rules = r; rulesAreSample = false }
                if let d = h.devices { setDevices(d) }
                if let s = h.settings { settings = s }
            }
        case "event":
            add(obj)
        case "rules.list":
            if let m = try? dec.decode(RulesListMessage.self, from: data) { rules = m.rules; rulesAreSample = false }
        case "device.status":
            if let m = try? dec.decode(DeviceStatusMessage.self, from: data) { setDevices(m.devices) }
        case "settings":
            if let s = try? dec.decode(EngineSettings.self, from: data) { settings = s }
        case "ack":
            if let a = try? dec.decode(AckMessage.self, from: data) {
                let what = [a.for, a.name].compactMap { $0 }.joined(separator: " ")
                lastAck = (a.ok ?? false) ? "\(what): \(a.detail ?? "ok")" : "\(what) failed: \(a.error ?? "?")"
            }
        case "error":
            lastAck = "error: \(obj["error"] as? String ?? "?")"
        default:
            break
        }
    }

    private func setDevices(_ d: [DeviceInfo]) {
        devices = d; devicesAreSample = false; devicesUpdatedAt = Date()
    }

    private func add(_ dict: [String: Any]) {
        guard let e = HapticEvent(dict), seen.insert(e.key).inserted else { return }
        // Insert keeping newest-first order (history may arrive oldest-first).
        let idx = events.firstIndex { $0.ts < e.ts || ($0.ts == e.ts && $0.id < e.id) } ?? events.count
        events.insert(e, at: idx)
        if events.count > maxEvents {
            for old in events[maxEvents...] { seen.remove(old.key) }
            events.removeLast(events.count - maxEvents)
        }
    }

    func clear() { events.removeAll(); seen.removeAll() }

    // MARK: - Sending

    /// Sends when connected. Returns false (and notes it) when offline.
    @discardableResult
    private func send<T: Encodable>(_ msg: T) -> Bool {
        guard status == .connected, let task,
              let data = try? JSONEncoder().encode(msg), let s = String(data: data, encoding: .utf8) else {
            lastAck = "offline: change kept on this phone only (sample)"
            return false
        }
        task.send(.string(s)) { [weak self] err in
            guard let err else { return }
            Task { @MainActor in self?.lastAck = "send failed: \(err.localizedDescription)" }
        }
        return true
    }

    /// Add or replace a rule. Optimistic local update; the engine answers with rules.list.
    func upsert(_ rule: SenseRule) {
        if let i = rules.firstIndex(where: { $0.id == rule.id }) { rules[i] = rule } else { rules.append(rule) }
        send(RulesUpsert(rule: rule))
    }

    func setEnabled(_ rule: SenseRule, _ on: Bool) {
        var r = rule; r.enabled = on; upsert(r)
    }

    func deleteRule(id: String) {
        rules.removeAll { $0.id == id }
        send(RulesDelete(id: id))
    }

    func feedback(_ e: HapticEvent, _ v: Verdict) {
        verdicts[e.key] = v
        if !e.isSample { send(FeedbackMessage(eventId: e.id, ts: e.ts, verdict: v)) }
    }

    func command(_ c: CommandMessage) {
        switch c.name {   // mirror locally so the UI reacts even before the engine echoes settings
        case .quiet: if case .bool(let on)? = c.args["on"] { settings.quiet = on }
        case .setIntensity: if case .number(let v)? = c.args["value"] { settings.intensity = v }
        default: break
        }
        send(c)
    }
}
