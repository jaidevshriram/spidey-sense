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
    /// v3 `status` from the engine (nil until one arrives, and while offline).
    private(set) var engineStatus: StatusMessage?
    private(set) var engineStatusAt: Date?
    /// Engine pause (v3): mirrored locally as soon as the switch is flipped, then from `status.paused`.
    private(set) var paused = false
    /// True until a hello with rules arrives from a real engine.
    private(set) var rulesAreSample = true
    /// When the engine last sent its rule list (hello or rules.list).
    private(set) var rulesEchoedAt: Date?
    private(set) var devicesAreSample = true
    private(set) var devicesUpdatedAt: Date?
    /// Last ack / error from the engine, shown as a toast-ish line in Settings.
    private(set) var lastAck: String?
    /// Local record of Useful/Noisy swipes, keyed by HapticEvent.key.
    private(set) var verdicts: [String: Verdict] = [:]

    // System rows for the Log (engine `system` messages + local WebSocket drops/reconnects), newest first.
    private(set) var systemRows: [SystemRow] = []
    /// Successful reconnects since the last connect(to:) call.
    private(set) var reconnects = 0

    // Haptics tab state.
    private(set) var telemetry: HapticTelemetry?
    private(set) var telemetryHistory: [HapticTelemetry] = []   // last 120 s, oldest first
    private(set) var link: LinkInfo?                             // latest Mac -> El Jefe -> Dot link check
    private(set) var lastPing: PingResult?                       // periodic (flash: false)
    private(set) var lastFullPing: PingResult?                   // "Ping all" (flash: true)
    private(set) var ackLog: [AckEntry] = []                     // newest first, max 30
    private(set) var lastThermalAck: AckMessage?

    // Log tab "Live decisions": one `trace` per Haiku call while subscribed, newest first.
    private(set) var traces: [Trace] = []
    private let maxTraces = 50
    private var pingSeq = 0
    private var flashPings = Set<Int>()

    private var url: URL?
    /// The URL last passed to connect(to:), for banners.
    private(set) var urlString: String?
    private var task: URLSessionWebSocketTask?
    private var loop: Task<Void, Never>?
    private var seen = Set<String>()
    private let maxEvents = 500

    var isLive: Bool { status == .connected }

    /// Called before each reconnect attempt; returns the URL to use (e.g. after a fresh Bonjour browse).
    var resolveURL: (() async -> String?)?

    func connect(to urlString: String) {
        loop?.cancel()
        task?.cancel(with: .goingAway, reason: nil)
        self.urlString = urlString.trimmingCharacters(in: .whitespaces)
        url = URL(string: urlString.trimmingCharacters(in: .whitespaces))
        reconnects = 0
        guard let url else { status = .disconnected; lastError = "Invalid URL"; return }
        loop = Task { await run(url) }
    }

    private func run(_ startURL: URL) async {
        var url = startURL
        var backoff = 1.0
        var everConnected = false
        var droppedLogged = false   // one "unreachable" row per offline streak
        while !Task.isCancelled {
            status = .connecting
            let ws = URLSession.shared.webSocketTask(with: url)
            task = ws
            ws.resume()
            var wasConnected = false
            do {
                while !Task.isCancelled {
                    let msg = try await ws.receive()
                    if status != .connected {
                        status = .connected; lastError = nil; backoff = 1.0; wasConnected = true
                        if everConnected {
                            reconnects += 1
                            logSystem(.info, "Reconnected to the Mac (\(url.host() ?? "?")), reconnect #\(reconnects)")
                        } else if droppedLogged {
                            logSystem(.info, "Connected to the Mac (\(url.host() ?? "?"))")
                        }
                        everConnected = true; droppedLogged = false
                    }
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
            if wasConnected {
                logSystem(.error, "WebSocket to the Mac dropped: \(lastError ?? "closed"). Reconnecting…")
                droppedLogged = true
            } else if !droppedLogged {
                logSystem(.warning, "Can't reach the Mac at \(url.absoluteString): \(lastError ?? "no answer"). Retrying…")
                droppedLogged = true
            }
            status = .disconnected
            telemetry = nil
            engineStatus = nil
            devicesAreSample = true   // device cards fall back to sample data while offline
            try? await Task.sleep(for: .seconds(backoff))
            backoff = min(backoff * 2, 5)
            if let resolveURL, let s = await resolveURL(), !Task.isCancelled,
               let u = URL(string: s), u != url {
                url = u; self.url = u; urlString = s
                logSystem(.info, "Trying the engine at \(s)")
            }
        }
    }

    private func handle(_ data: Data) {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        let dec = JSONDecoder()
        switch obj["type"] as? String {
        case "hello":
            for e in obj["history"] as? [[String: Any]] ?? [] { add(e) }
            if let h = try? dec.decode(HelloMessage.self, from: data) {
                if let r = h.rules { applyEcho(r) }
                if let d = h.devices { setDevices(d) }
                if let s = h.settings { settings = s }
                if let st = h.status {
                    engineStatus = st; engineStatusAt = Date()
                    if let p = st.paused { paused = p }
                }
            }
        case "event":
            add(obj)
        case "trace":
            // Decoded leniently; a trace that still fails is dropped without touching anything else.
            if let t = try? dec.decode(Trace.self, from: data) {
                traces.insert(t, at: 0)
                if traces.count > maxTraces { traces.removeLast(traces.count - maxTraces) }
            }
        case "rules.list":
            if let m = try? dec.decode(RulesListMessage.self, from: data) { applyEcho(m.rules) }
        case "device.status":
            if let m = try? dec.decode(DeviceStatusMessage.self, from: data) { setDevices(m.devices) }
        case "status":
            if let m = try? dec.decode(StatusMessage.self, from: data) {
                engineStatus = m; engineStatusAt = Date()
                if let p = m.paused { paused = p }
            }
        case "settings":
            if let s = try? dec.decode(EngineSettings.self, from: data) { settings = s }
        case "ack":
            if let a = try? dec.decode(AckMessage.self, from: data) { handleAck(a) }
        case "error":
            let err = obj["error"] as? String ?? "?"
            lastAck = "error: \(err)"
            logAck(kind: obj["for"] as? String ?? "error", ok: false, ms: nil, note: err)
        case "haptic.telemetry":
            if let t = try? dec.decode(HapticTelemetry.self, from: data) { addTelemetry(t) }
        case "link.pong":
            if let p = try? dec.decode(LinkPong.self, from: data) { handlePong(p) }
        case "system":
            if let m = try? dec.decode(SystemMessage.self, from: data) {
                logSystem(SystemRow.Level(rawValue: m.level ?? "info") ?? .info, m.text ?? "(no text)",
                          ts: m.ts, local: false)
            }
        default:
            break
        }
    }

    /// The engine's rule list is the source of truth: it replaces any optimistic local edits.
    /// Older engines drop kind/describe/intensity/rgb from their echo; for those (no rule in the echo
    /// carries any trigger field) keep the extras this phone last sent for the same id, so a custom
    /// trigger doesn't turn into a blank built-in. Newer engines' echoes are taken as-is.
    private func applyEcho(_ echoed: [SenseRule]) {
        let previous = rules, wasSample = rulesAreSample
        let engineKnowsTriggers = echoed.contains { $0.hasTriggerExtras }
        if engineKnowsTriggers || rulesAreSample {
            rules = echoed
        } else {
            let mine = Dictionary(rules.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
            rules = echoed.map { r in
                guard let old = mine[r.id], old.hasTriggerExtras else { return r }
                var m = r
                m.kind = old.kind; m.describe = old.describe; m.intensity = old.intensity; m.rgb = old.rgb
                return m
            }
        }
        // Same for the v3 fields (gaze_mode, notice_memory_s, temporal, trend) on engines without v3.
        if !wasSample && !echoed.contains(where: { $0.hasV3Extras }) {
            let mine = Dictionary(previous.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
            rules = rules.map { r in
                guard let old = mine[r.id], old.hasV3Extras, !r.hasV3Extras else { return r }
                var m = r
                m.gazeMode = old.gazeMode; m.noticeMemoryS = old.noticeMemoryS; m.temporal = old.temporal; m.trend = old.trend
                return m
            }
        }
        rulesAreSample = false
        rulesEchoedAt = Date()
    }

    private func setDevices(_ d: [DeviceInfo]) {
        devices = d; devicesAreSample = false; devicesUpdatedAt = Date()
        if let l = d.first(where: { $0.id == "el_jefe" })?.link, l.transport != nil { link = l }
    }

    private func handleAck(_ a: AckMessage) {
        let kind = a.for ?? "?"
        if kind == "haptic.subscribe" || kind == "trace.subscribe" { return }   // every 2 s: not interesting
        let what = [a.for, a.name].compactMap { $0 }.joined(separator: " ")
        lastAck = (a.ok ?? false) ? "\(what): \(a.detail ?? "ok")" : "\(what) failed: \(a.error ?? "?")"
        var note = a.ok == false ? a.error : a.detail
        if kind == "haptic.thermal" {
            lastThermalAck = a
            if a.capped == true { note = "capped at \(a.value.map { String(format: "%.0f", $0) } ?? "40") °C" }
            else if let m = a.mode, let v = a.value { note = "\(m) \(String(format: "%g", v))" }
        }
        // An engine without v3 rejects `pause`: undo the optimistic local switch.
        if kind == "command", a.name == "pause", a.ok == false { paused = engineStatus?.paused ?? false }
        logAck(kind: what, ok: a.ok ?? false, ms: a.ms, note: note)
    }

    private func logAck(kind: String, ok: Bool, ms: Double?, note: String?) {
        ackLog.insert(AckEntry(date: Date(), kind: kind, ok: ok, ms: ms, note: note), at: 0)
        if ackLog.count > 30 { ackLog.removeLast(ackLog.count - 30) }
    }

    private func addTelemetry(_ t: HapticTelemetry) {
        telemetry = t
        telemetryHistory.append(t)
        let cutoff = t.ts - 120
        if let i = telemetryHistory.firstIndex(where: { $0.ts >= cutoff }), i > 0 { telemetryHistory.removeFirst(i) }
    }

    private func handlePong(_ p: LinkPong) {
        if let l = p.link { link = l }
        guard let tApp = p.tApp else { return }
        let r = PingResult(date: Date(), flash: p.id.map { flashPings.contains($0) } ?? false,
                           rttMs: (Date().timeIntervalSince1970 - tApp) * 1000,
                           macMs: p.macMs, bleMs: p.bleMs, flashMs: p.flashMs)
        if r.flash {
            lastFullPing = r
            if let id = p.id { flashPings.remove(id) }
            logAck(kind: "link.ping (all)", ok: p.link?.ok ?? true, ms: r.rttMs, note: p.link?.reason)
        } else {
            lastPing = r
        }
    }

    func logSystem(_ level: SystemRow.Level, _ text: String, ts: Double? = nil, local: Bool = true) {
        systemRows.insert(SystemRow(ts: ts ?? Date().timeIntervalSince1970, level: level, text: text, local: local), at: 0)
        if systemRows.count > 200 { systemRows.removeLast() }
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

    func clear() { events.removeAll(); seen.removeAll(); systemRows.removeAll() }

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

    /// Add or replace a rule. Optimistic local update only once the message actually went out
    /// (offline nothing changes, so a toggle can never flip on this phone without reaching the engine);
    /// the engine answers with rules.list.
    func upsert(_ rule: SenseRule) {
        guard sendRuleChange(RulesUpsert(rule: rule)) else { return }
        if let i = rules.firstIndex(where: { $0.id == rule.id }) { rules[i] = rule } else { rules.append(rule) }
    }

    func setEnabled(_ rule: SenseRule, _ on: Bool) {
        var r = rule; r.enabled = on; upsert(r)
    }

    func deleteRule(id: String) {
        guard sendRuleChange(RulesDelete(id: id)) else { return }
        rules.removeAll { $0.id == id }
    }

    /// Restore the engine's default triggers (the engine answers with rules.list). Offline: nothing changes.
    func resetRules() {
        sendRuleChange(RulesReset())
    }

    /// Sends a rule change only while live; offline it is refused (and noted) so the UI never shows
    /// a change the engine didn't get.
    @discardableResult
    private func sendRuleChange<T: Encodable>(_ msg: T) -> Bool {
        guard isLive, send(msg) else {
            lastAck = "not connected: trigger change not sent"
            logSystem(.warning, "Not connected: trigger change not sent to the engine")
            return false
        }
        return true
    }

    func feedback(_ e: HapticEvent, _ v: Verdict) {
        verdicts[e.key] = v
        if !e.isSample { send(FeedbackMessage(eventId: e.id, ts: e.ts, verdict: v)) }
    }

    // MARK: - Haptics tab

    /// Sends a haptics-tab message. Offline: nothing is sent and the command log says so.
    @discardableResult
    func haptic<T: Encodable>(_ msg: T, label: String) -> Bool {
        let ok = send(msg)
        if !ok { logAck(kind: label, ok: false, ms: nil, note: "not connected: nothing sent") }
        return ok
    }

    /// Keeps telemetry flowing (and the engine's "thermal off when the app goes away" timer fed).
    func subscribeHaptics() {
        guard isLive else { return }
        send(HapticSubscribe(on: true))
    }

    /// Live decisions: the engine streams `trace` while it got `on: true` within the last 5 s, so the
    /// Log resends this every ~2 s; `on: false` stops it at once. Offline: nothing is sent.
    func subscribeTrace(_ on: Bool) {
        guard isLive else { return }
        send(TraceSubscribe(on: on))
    }

    func clearTraces() { traces.removeAll() }

    /// `flash: false` = silent health check every 2 s; `flash: true` = "Ping all" (Dot flashes green).
    func ping(flash: Bool) {
        guard isLive else {
            if flash { logAck(kind: "link.ping (all)", ok: false, ms: nil, note: "not connected: nothing sent") }
            return
        }
        pingSeq += 1
        if flash { flashPings.insert(pingSeq) }
        send(LinkPing(id: pingSeq, tApp: Date().timeIntervalSince1970, flash: flash))
    }

    func command(_ c: CommandMessage) {
        switch c.name {   // mirror locally so the UI reacts even before the engine echoes settings
        case .quiet: if case .bool(let on)? = c.args["on"] { settings.quiet = on }
        case .setIntensity: if case .number(let v)? = c.args["value"] { settings.intensity = v }
        case .pause: if case .bool(let on)? = c.args["on"] { paused = on }
        default: break
        }
        send(c)
    }
}
