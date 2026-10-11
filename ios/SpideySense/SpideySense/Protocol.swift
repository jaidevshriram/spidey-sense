import Foundation
import UIKit

// Wire protocol between the app and the Mac engine. Keep in sync with docs/protocol.md
// and spidey/server.py + spidey/engine.py (Controller).
//
// server -> app: hello{history, rules, devices, settings}, status{paused, source, radar, about} (v3),
//                event{..., trigger?, trigger_name?, why?, call_id?, raw?}, rules.list{rules},
//                device.status{devices}, settings{quiet, intensity}, ack{for, ok, ms, ...}, error{error},
//                haptic.telemetry{...}, link.pong{...}, system{ts, level, text},
//                trace{id, ts, t, hazard, urgency, reason, latency_ms, n_images, paused, stale, error, raw,
//                      thumb?, near_miss?, decisions[{id, name, fired, matched, why, gaze_mode, haiku_reason}]}
//                      (only while subscribed)
// app -> server: rules.upsert{rule}, rules.delete{id}, rules.reset{}, feedback{event_id, ts, verdict},
//                command{name, args}, haptic.subscribe/led/vibe.sequence/vibe.manual/preset/thermal/stop_all,
//                link.ping{id, t_app, flash}, trace.subscribe{on} (resent every ~2 s; engine stops after 5 s)
//
// Events are still parsed loosely into `HapticEvent` (Model.swift) so the live log keeps working
// with older engines and the test server.

// MARK: - Rules ("senses")

/// Hazard kinds a rule covers: the string "any" or a list like ["bike", "car"].
enum RuleWhen: Codable, Hashable {
    case any
    case kinds([String])

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let list = try? c.decode([String].self) { self = list.isEmpty ? .any : .kinds(list); return }
        self = .any   // "any" or anything unexpected
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .any: try c.encode("any")
        case .kinds(let k): try c.encode(k)
        }
    }

    var label: String {
        switch self { case .any: "any hazard"; case .kinds(let k): k.joined(separator: ", ") }
    }
}

/// The rule's `if` block. Vision keys are applied by the engine today; heart-rate and
/// metric keys are stored and round-tripped only (TODO in spidey/rules.py).
///
/// Round-trip guarantee: every `if` key the app doesn't model is kept verbatim in `extra` and written back on
/// encode, so editing a trigger never drops keys a newer engine added. Editors that build a fresh condition must
/// call `preserving(_:)` with the original so the unmanaged keys (v3.4 and `extra`) carry over.
struct RuleCondition: Codable, Hashable {
    var unseen: Bool?
    var seen: Bool?
    var minUrgency: Double?
    var maxUrgency: Double?
    var hrBandBpm: Double?      // heart rate outside 2-min median ± this
    var hrWindowS: Double?      // median window, default 120 s
    var metric: String?         // Mac metric name, e.g. "cpu_temp_c"
    var valueGt: Double?        // fire when metric > this
    var maxDistanceM: Double?   // radar: target within this many metres
    var minSpeedMps: Double?    // radar: approaching faster than this
    var minDeltaBpm: Double?    // heart_rate: bpm >= 2-min median + this ...
    var minDeltaPct: Double?    // ... or >= median * (1 + this %)
    var sustainS: Double?       // heart_rate: for at least this many seconds
    // v3.4 (docs/protocol.md "v3.4"). Older engines ignore them.
    var onlyWhileWalking: Bool? // fire only while the glasses' IMU says the wearer is walking
    var maxTtcS: Double?        // fire only if the wearer would reach it within this many seconds
    var maxAgeS: Double?        // per-trigger result staleness limit (overrides config max_result_age_s)
    var keepOnViewChange: Bool? // static things (obstacles) stay valid after the view changes
    /// Every other `if` key, kept verbatim so it survives an edit.
    var extra: [String: JSONValue] = [:]

    enum CodingKeys: String, CodingKey, CaseIterable {
        case unseen, seen, metric
        case minUrgency = "min_urgency", maxUrgency = "max_urgency"
        case hrBandBpm = "hr_band_bpm", hrWindowS = "hr_window_s", valueGt = "value_gt"
        case maxDistanceM = "max_distance_m", minSpeedMps = "min_speed_mps"
        case minDeltaBpm = "min_delta_bpm", minDeltaPct = "min_delta_pct", sustainS = "sustain_s"
        case onlyWhileWalking = "only_while_walking", maxTtcS = "max_ttc_s"
        case maxAgeS = "max_age_s", keepOnViewChange = "keep_on_view_change"
    }
    private static let knownKeys = Set(CodingKeys.allCases.map(\.rawValue))

    /// Copies the keys no editor manages (v3.4 gates + unknown keys) from `old` where this condition has none.
    func preserving(_ old: RuleCondition?) -> RuleCondition {
        guard let old else { return self }
        var c = self
        c.onlyWhileWalking = c.onlyWhileWalking ?? old.onlyWhileWalking
        c.maxTtcS = c.maxTtcS ?? old.maxTtcS
        c.maxAgeS = c.maxAgeS ?? old.maxAgeS
        c.keepOnViewChange = c.keepOnViewChange ?? old.keepOnViewChange
        c.extra = old.extra.merging(c.extra) { _, new in new }
        return c
    }

    init(unseen: Bool? = nil, seen: Bool? = nil, minUrgency: Double? = nil, maxUrgency: Double? = nil,
         hrBandBpm: Double? = nil, hrWindowS: Double? = nil, metric: String? = nil, valueGt: Double? = nil,
         maxDistanceM: Double? = nil, minSpeedMps: Double? = nil,
         minDeltaBpm: Double? = nil, minDeltaPct: Double? = nil, sustainS: Double? = nil) {
        self.unseen = unseen; self.seen = seen; self.minUrgency = minUrgency; self.maxUrgency = maxUrgency
        self.hrBandBpm = hrBandBpm; self.hrWindowS = hrWindowS; self.metric = metric; self.valueGt = valueGt
        self.maxDistanceM = maxDistanceM; self.minSpeedMps = minSpeedMps
        self.minDeltaBpm = minDeltaBpm; self.minDeltaPct = minDeltaPct; self.sustainS = sustainS
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        unseen = c.lenient(Bool.self, .unseen); seen = c.lenient(Bool.self, .seen)
        minUrgency = c.lenient(Double.self, .minUrgency); maxUrgency = c.lenient(Double.self, .maxUrgency)
        hrBandBpm = c.lenient(Double.self, .hrBandBpm); hrWindowS = c.lenient(Double.self, .hrWindowS)
        metric = c.lenient(String.self, .metric); valueGt = c.lenient(Double.self, .valueGt)
        maxDistanceM = c.lenient(Double.self, .maxDistanceM); minSpeedMps = c.lenient(Double.self, .minSpeedMps)
        minDeltaBpm = c.lenient(Double.self, .minDeltaBpm); minDeltaPct = c.lenient(Double.self, .minDeltaPct)
        sustainS = c.lenient(Double.self, .sustainS)
        onlyWhileWalking = c.lenient(Bool.self, .onlyWhileWalking); maxTtcS = c.lenient(Double.self, .maxTtcS)
        maxAgeS = c.lenient(Double.self, .maxAgeS); keepOnViewChange = c.lenient(Bool.self, .keepOnViewChange)
        // Unknown keys are stored raw. A known key whose value was mistyped (decoded to nil above) is kept raw
        // too, so the engine gets back exactly what it sent.
        let all = try decoder.container(keyedBy: AnyCodingKey.self)
        for k in all.allKeys {
            guard let v = try? all.decode(JSONValue.self, forKey: k) else { continue }
            if !Self.knownKeys.contains(k.stringValue) || !hasTypedValue(k.stringValue) { extra[k.stringValue] = v }
        }
    }

    /// True when the typed property for a known key holds a value (i.e. it decoded fine or was set).
    private func hasTypedValue(_ key: String) -> Bool {
        switch CodingKeys(rawValue: key) {
        case .unseen: unseen != nil; case .seen: seen != nil; case .metric: metric != nil
        case .minUrgency: minUrgency != nil; case .maxUrgency: maxUrgency != nil
        case .hrBandBpm: hrBandBpm != nil; case .hrWindowS: hrWindowS != nil; case .valueGt: valueGt != nil
        case .maxDistanceM: maxDistanceM != nil; case .minSpeedMps: minSpeedMps != nil
        case .minDeltaBpm: minDeltaBpm != nil; case .minDeltaPct: minDeltaPct != nil; case .sustainS: sustainS != nil
        case .onlyWhileWalking: onlyWhileWalking != nil; case .maxTtcS: maxTtcS != nil
        case .maxAgeS: maxAgeS != nil; case .keepOnViewChange: keepOnViewChange != nil
        case nil: false
        }
    }

    func encode(to encoder: Encoder) throws {
        // Unmodelled keys first; a typed value for the same key (set by an editor) wins.
        var raw = encoder.container(keyedBy: AnyCodingKey.self)
        for (k, v) in extra where !hasTypedValue(k) { try raw.encode(v, forKey: AnyCodingKey(k)) }
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(unseen, forKey: .unseen); try c.encodeIfPresent(seen, forKey: .seen)
        try c.encodeIfPresent(minUrgency, forKey: .minUrgency); try c.encodeIfPresent(maxUrgency, forKey: .maxUrgency)
        try c.encodeIfPresent(hrBandBpm, forKey: .hrBandBpm); try c.encodeIfPresent(hrWindowS, forKey: .hrWindowS)
        try c.encodeIfPresent(metric, forKey: .metric); try c.encodeIfPresent(valueGt, forKey: .valueGt)
        try c.encodeIfPresent(maxDistanceM, forKey: .maxDistanceM); try c.encodeIfPresent(minSpeedMps, forKey: .minSpeedMps)
        try c.encodeIfPresent(minDeltaBpm, forKey: .minDeltaBpm); try c.encodeIfPresent(minDeltaPct, forKey: .minDeltaPct)
        try c.encodeIfPresent(sustainS, forKey: .sustainS)
        try c.encodeIfPresent(onlyWhileWalking, forKey: .onlyWhileWalking); try c.encodeIfPresent(maxTtcS, forKey: .maxTtcS)
        try c.encodeIfPresent(maxAgeS, forKey: .maxAgeS); try c.encodeIfPresent(keepOnViewChange, forKey: .keepOnViewChange)
    }
}

/// v3 gaze mode: when a camera trigger may fire relative to where the wearer is looking.
enum GazeMode: String, CaseIterable, Identifiable, Codable {
    case unseen, lookingAt = "looking_at", any
    var id: String { rawValue }
    var label: String {
        switch self { case .unseen: "Haven't looked at it"; case .lookingAt: "Looking at it"; case .any: "Either" }
    }
    var short: String {
        switch self { case .unseen: "unseen"; case .lookingAt: "looking"; case .any: "either" }
    }
}

/// v3.3 temporal trend settings: fire on a consistent trend for `ticks` ticks; high sensitivity = smaller changes count.
struct TrendConfig: Codable, Hashable {
    var ticks: Int = 3
    var sensitivity: String = "medium"   // low | medium | high

    init(ticks: Int = 3, sensitivity: String = "medium") { self.ticks = ticks; self.sensitivity = sensitivity }

    enum CodingKeys: String, CodingKey { case ticks, sensitivity }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ticks = c.lenient(Int.self, .ticks) ?? c.lenient(Double.self, .ticks).map { Int($0) } ?? 3
        sensitivity = c.lenient(String.self, .sensitivity) ?? "medium"
    }
}

enum SenseSource: String, CaseIterable, Identifiable, Codable {
    case neon, radar, heartRate = "heart_rate", macMetric = "mac_metric", manual
    var id: String { rawValue }
    var label: String {
        switch self {
        case .neon: "Neon vision"; case .radar: "Radar"; case .heartRate: "Heart rate"
        case .macMetric: "Mac metric"; case .manual: "Manual"
        }
    }
    var icon: String {
        switch self {
        case .neon: "eye"; case .radar: "dot.radiowaves.right"; case .heartRate: "heart"
        case .macMetric: "desktopcomputer"; case .manual: "hand.tap"
        }
    }
    /// Neon vision, radar and heart rate feed the engine; the rest are stubs on the Mac side.
    var isWired: Bool { self == .neon || self == .radar || self == .heartRate }
    /// Sources offered by the built-in condition builder (radar and heart rate have their own editors).
    static var builtinCases: [SenseSource] { allCases.filter { $0 != .radar && $0 != .heartRate } }
}

struct SenseRule: Codable, Identifiable, Hashable {
    var id: String
    var name: String
    var enabled: Bool = true
    var source: String = SenseSource.neon.rawValue
    var when: RuleWhen = .any
    var cond: RuleCondition = RuleCondition()
    var feel: String = "urgent"
    var dot: Int?               // 1-based; nil = all dots. 1 = left, 2 = right (TODO: confirm layout)
    var cooldownS: Double = 3
    // Triggers (docs/protocol.md "plain-English custom triggers"). All optional: older engines omit them.
    var kind: String?           // "builtin" | "custom"; nil = builtin (older engine)
    var describe: String?       // custom only: plain-English description judged by Haiku
    var intensity: Double?      // 0...1 per-trigger vibration strength; nil = 100 %
    var rgb: [Int]?             // LED colour override [r, g, b]
    // v3 (docs/protocol.md "v3" / "v3.3"). All optional: older engines omit or drop them.
    var gazeMode: String?       // unseen | looking_at | any; nil = derive from if.unseen
    var noticeMemoryS: Double?  // unseen: stay quiet this long after the gaze leaves it
    var temporal: Bool?         // compare the last ~1.4 s of frames
    var trend: TrendConfig?     // temporal only

    enum CodingKeys: String, CodingKey {
        case id, name, enabled, source, when, feel, dot, kind, describe, intensity, rgb, temporal, trend
        case cond = "if", cooldownS = "cooldown_s", gazeMode = "gaze_mode", noticeMemoryS = "notice_memory_s"
    }

    init(id: String, name: String, enabled: Bool = true, source: String = "neon", when: RuleWhen = .any,
         cond: RuleCondition = RuleCondition(), feel: String = "urgent", dot: Int? = nil, cooldownS: Double = 3,
         kind: String? = nil, describe: String? = nil, intensity: Double? = nil, rgb: [Int]? = nil) {
        self.id = id; self.name = name; self.enabled = enabled; self.source = source; self.when = when
        self.cond = cond; self.feel = feel; self.dot = dot; self.cooldownS = cooldownS
        self.kind = kind; self.describe = describe; self.intensity = intensity; self.rgb = rgb
    }

    // Tolerant decoding: everything but id/name has a default; new trigger fields are lenient.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? "rule"
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? name
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled) ?? true
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? "neon"
        when = try c.decodeIfPresent(RuleWhen.self, forKey: .when) ?? .any
        cond = try c.decodeIfPresent(RuleCondition.self, forKey: .cond) ?? RuleCondition()
        feel = try c.decodeIfPresent(String.self, forKey: .feel) ?? "urgent"
        dot = try c.decodeIfPresent(Int.self, forKey: .dot)
        cooldownS = try c.decodeIfPresent(Double.self, forKey: .cooldownS) ?? 3
        kind = c.lenient(String.self, .kind)
        describe = c.lenient(String.self, .describe)
        intensity = c.lenient(Double.self, .intensity)
        rgb = c.lenient([Int].self, .rgb).flatMap { $0.count == 3 ? $0 : nil }
        gazeMode = c.lenient(String.self, .gazeMode)
        noticeMemoryS = c.lenient(Double.self, .noticeMemoryS)
        temporal = c.lenient(Bool.self, .temporal)
        trend = c.lenient(TrendConfig.self, .trend)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id); try c.encode(name, forKey: .name)
        try c.encode(enabled, forKey: .enabled); try c.encode(source, forKey: .source)
        try c.encode(when, forKey: .when); try c.encode(cond, forKey: .cond)
        try c.encode(feel, forKey: .feel); try c.encode(dot, forKey: .dot)   // null = all dots
        try c.encode(cooldownS, forKey: .cooldownS)
        try c.encodeIfPresent(kind, forKey: .kind)
        try c.encodeIfPresent(describe, forKey: .describe)
        try c.encodeIfPresent(intensity, forKey: .intensity)
        try c.encodeIfPresent(rgb, forKey: .rgb)
        try c.encodeIfPresent(gazeMode, forKey: .gazeMode)
        try c.encodeIfPresent(noticeMemoryS, forKey: .noticeMemoryS)
        try c.encodeIfPresent(temporal, forKey: .temporal)
        try c.encodeIfPresent(trend, forKey: .trend)
    }

    var senseSource: SenseSource? { SenseSource(rawValue: source) }
    /// Custom = plain-English trigger judged by Haiku. Missing kind (older engine) = built-in,
    /// unless a description is present.
    var isCustom: Bool { kind == "custom" || (kind == nil && !(describe ?? "").isEmpty) }
    /// The extra trigger fields an older engine may drop from its echo.
    var hasTriggerExtras: Bool { kind != nil || describe != nil || intensity != nil || rgb != nil }
    /// The v3 fields an engine without v3 support drops from its echo.
    var hasV3Extras: Bool { gazeMode != nil || noticeMemoryS != nil || temporal != nil || trend != nil }

    var isRadar: Bool { source == SenseSource.radar.rawValue }
    var isHeart: Bool { source == SenseSource.heartRate.rawValue }
    /// Heart thresholds with the engine's defaults (15 bpm / 20 % / 8 s); legacy hr_band_bpm = min_delta_bpm.
    var heartDeltaBpm: Double { cond.minDeltaBpm ?? cond.hrBandBpm ?? 15 }
    var heartDeltaPct: Double { cond.minDeltaPct ?? 20 }
    var heartSustainS: Double { cond.sustainS ?? 8 }
    /// Gaze mode with back-compat: missing => if.unseen ? unseen : any. Radar is always unseen.
    var effectiveGazeMode: GazeMode {
        if isRadar { return .unseen }
        if isHeart { return .any }
        if let g = gazeMode.flatMap(GazeMode.init(rawValue:)) { return g }
        return cond.unseen == true ? .unseen : .any
    }
    var isTemporal: Bool { temporal ?? false }
}

// MARK: - Devices and settings

struct DeviceInfo: Codable, Identifiable, Hashable {
    var id: String
    var name: String
    var kind: String
    var status: String          // connected | disconnected | simulated | coming_soon
    var detail: String?
    var transport: String?
    var sink: String?
    var lastSeen: Double?       // unix seconds
    var link: LinkInfo? = nil   // el_jefe only: live link check from the engine
    // v3 card extras (neon: fps/gaze Hz/worn/battery/delay; radar: url/mode/last/rate). All lenient.
    var source: SourceStatus? = nil
    var radar: RadarStatus? = nil
    var heart: HeartCard? = nil  // oura: backend / last bpm / 2-min median / since last (docs/protocol.md "Heart rate")

    enum CodingKeys: String, CodingKey {
        case id, name, kind, status, detail, transport, sink, link, stats
        case lastSeen = "last_seen"
    }

    init(id: String, name: String, kind: String, status: String, detail: String? = nil, transport: String? = nil,
         sink: String? = nil, lastSeen: Double? = nil, link: LinkInfo? = nil,
         source: SourceStatus? = nil, radar: RadarStatus? = nil, heart: HeartCard? = nil) {
        self.id = id; self.name = name; self.kind = kind; self.status = status; self.detail = detail
        self.transport = transport; self.sink = sink; self.lastSeen = lastSeen; self.link = link
        self.source = source; self.radar = radar; self.heart = heart
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.lenient(String.self, .id) ?? "device"
        name = c.lenient(String.self, .name) ?? id
        kind = c.lenient(String.self, .kind) ?? ""
        status = c.lenient(String.self, .status) ?? "disconnected"
        detail = c.lenient(String.self, .detail); transport = c.lenient(String.self, .transport)
        sink = c.lenient(String.self, .sink); lastSeen = c.lenient(Double.self, .lastSeen)
        link = c.lenient(LinkInfo.self, .link)
        // The card's own keys may carry the v3 numbers (flat), so decode them from the same object.
        if id == "neon" { source = c.lenient(SourceStatus.self, .stats) ?? (try? SourceStatus(from: decoder)) }
        if id == "radar" { radar = try? RadarStatus(from: decoder) }
        if id == "oura" { heart = (try? HeartCard(from: decoder)).flatMap { $0.hasData ? $0 : nil } }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id); try c.encode(name, forKey: .name); try c.encode(kind, forKey: .kind)
        try c.encode(status, forKey: .status); try c.encodeIfPresent(detail, forKey: .detail)
        try c.encodeIfPresent(transport, forKey: .transport); try c.encodeIfPresent(sink, forKey: .sink)
        try c.encodeIfPresent(lastSeen, forKey: .lastSeen); try c.encodeIfPresent(link, forKey: .link)
    }
}

/// The `oura` device card's heart-rate fields (flat on the card).
struct HeartCard: Decodable, Hashable {
    var backend: String?        // open_oura | replay | fake
    var lastBpm: Double?
    var medianBpm: Double?      // rolling 2-min median
    var sinceLastS: Double?
    var rateHz: Double?
    var connected: Bool?

    enum CodingKeys: String, CodingKey {
        case backend, connected
        case lastBpm = "last_bpm", medianBpm = "median_bpm", sinceLastS = "since_last_s", rateHz = "rate_hz"
    }

    init(backend: String? = nil, lastBpm: Double? = nil, medianBpm: Double? = nil, sinceLastS: Double? = nil,
         rateHz: Double? = nil, connected: Bool? = nil) {
        self.backend = backend; self.lastBpm = lastBpm; self.medianBpm = medianBpm; self.sinceLastS = sinceLastS
        self.rateHz = rateHz; self.connected = connected
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        backend = c.lenient(String.self, .backend); lastBpm = c.lenient(Double.self, .lastBpm)
        medianBpm = c.lenient(Double.self, .medianBpm); sinceLastS = c.lenient(Double.self, .sinceLastS)
        rateHz = c.lenient(Double.self, .rateHz); connected = c.lenient(Bool.self, .connected)
    }

    /// An engine with the heart sense sends at least the backend; the old stub card sends none of these.
    var hasData: Bool { backend != nil || lastBpm != nil || medianBpm != nil }
    var isSimulated: Bool { backend == "replay" || backend == "fake" }
}

// MARK: - v3 status (server -> app `status`, on connect, every 2 s and on change)

/// `status.source`: the camera feed.
struct SourceStatus: Decodable, Hashable {
    var type: String?           // neon | fake
    var live: Bool?
    var fps: Double?
    var gazeHz: Double?
    var worn: Bool?
    var battery: Double?        // %
    var delayMs: Double?
    // v3.4: the glasses' IMU (null while unknown, e.g. no motion samples yet).
    var walking: Bool?
    var cadenceSpm: Double?     // steps per minute, while walking
    var turning: Bool?

    enum CodingKeys: String, CodingKey {
        case type, live, fps, worn, battery, walking, turning
        case gazeHz = "gaze_hz", delayMs = "delay_ms", cadenceSpm = "cadence_spm"
    }

    init(type: String? = nil, live: Bool? = nil, fps: Double? = nil, gazeHz: Double? = nil, worn: Bool? = nil,
         battery: Double? = nil, delayMs: Double? = nil) {
        self.type = type; self.live = live; self.fps = fps; self.gazeHz = gazeHz; self.worn = worn
        self.battery = battery; self.delayMs = delayMs
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = c.lenient(String.self, .type); live = c.lenient(Bool.self, .live); fps = c.lenient(Double.self, .fps)
        gazeHz = c.lenient(Double.self, .gazeHz); worn = c.lenient(Bool.self, .worn)
        battery = c.lenient(Double.self, .battery); delayMs = c.lenient(Double.self, .delayMs)
        walking = c.lenient(Bool.self, .walking); cadenceSpm = c.lenient(Double.self, .cadenceSpm)
        turning = c.lenient(Bool.self, .turning)
    }

    /// "walking · 112 steps/min · turning" / "still"; nil when the engine sends no motion state.
    var motionSummary: String? {
        guard walking != nil || turning == true else { return nil }
        var bits: [String] = []
        if let w = walking {
            bits.append(w ? "walking" : "still")
            if w, let c = cadenceSpm, c > 0 { bits.append(String(format: "%.0f steps/min", c)) }
        }
        if turning == true { bits.append("turning") }
        return bits.joined(separator: " · ")
    }

    var isFake: Bool { type == "fake" }
}

/// One radar reading: `status.radar.last` and an event's `radar`.
struct RadarReading: Decodable, Hashable {
    var distanceM: Double?
    var speedMps: Double?
    var angleDeg: Double?
    var side: String?           // left | right | center
    var ts: Double?

    enum CodingKeys: String, CodingKey {
        case side, ts
        case distanceM = "distance_m", speedMps = "speed_mps", angleDeg = "angle_deg"
    }

    init(distanceM: Double? = nil, speedMps: Double? = nil, angleDeg: Double? = nil, side: String? = nil, ts: Double? = nil) {
        self.distanceM = distanceM; self.speedMps = speedMps; self.angleDeg = angleDeg; self.side = side; self.ts = ts
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        distanceM = c.lenient(Double.self, .distanceM); speedMps = c.lenient(Double.self, .speedMps)
        angleDeg = c.lenient(Double.self, .angleDeg); side = c.lenient(String.self, .side); ts = c.lenient(Double.self, .ts)
    }

    init?(_ d: [String: Any]?) {
        guard let d else { return nil }
        func num(_ k: String) -> Double? { (d[k] as? NSNumber)?.doubleValue }
        self.init(distanceM: num("distance_m"), speedMps: num("speed_mps"), angleDeg: num("angle_deg"),
                  side: d["side"] as? String, ts: num("ts"))
    }

    /// "1.4 m · 0.6 m/s · left"
    var summary: String {
        [distanceM.map { String(format: "%.1f m", $0) }, speedMps.map { String(format: "%.1f m/s", $0) },
         side ?? angleDeg.map { String(format: "%.0f°", $0) }].compactMap { $0 }.joined(separator: " · ")
    }
}

/// `status.radar` (null when no radar is configured).
struct RadarStatus: Decodable, Hashable {
    var connected: Bool?
    var url: String?
    var mode: String?           // board | sim
    var last: RadarReading?
    var rateHz: Double?
    var lastTs: Double?         // unix s of the last reading, if the engine sends it
    var sinceLastS: Double?

    enum CodingKeys: String, CodingKey {
        case connected, url, mode, last
        case rateHz = "rate_hz", lastTs = "last_ts", sinceLastS = "since_last_s"
    }

    init(connected: Bool? = nil, url: String? = nil, mode: String? = nil, last: RadarReading? = nil,
         rateHz: Double? = nil, lastTs: Double? = nil, sinceLastS: Double? = nil) {
        self.connected = connected; self.url = url; self.mode = mode; self.last = last
        self.rateHz = rateHz; self.lastTs = lastTs; self.sinceLastS = sinceLastS
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        connected = c.lenient(Bool.self, .connected); url = c.lenient(String.self, .url)
        mode = c.lenient(String.self, .mode); last = c.lenient(RadarReading.self, .last)
        rateHz = c.lenient(Double.self, .rateHz); lastTs = c.lenient(Double.self, .lastTs)
        sinceLastS = c.lenient(Double.self, .sinceLastS)
    }
}

/// `status.about`: what the engine is running.
struct AboutInfo: Decodable, Hashable {
    var config: String?
    var model: String?
    var commit: String?
    var sink: String?
}

struct StatusMessage: Decodable, Hashable {
    var paused: Bool?
    var source: SourceStatus?
    var radar: RadarStatus?
    var about: AboutInfo?

    enum CodingKeys: String, CodingKey { case paused, source, radar, about }
    init(paused: Bool? = nil, source: SourceStatus? = nil, radar: RadarStatus? = nil, about: AboutInfo? = nil) {
        self.paused = paused; self.source = source; self.radar = radar; self.about = about
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        paused = c.lenient(Bool.self, .paused); source = c.lenient(SourceStatus.self, .source)
        radar = c.lenient(RadarStatus.self, .radar); about = c.lenient(AboutInfo.self, .about)
    }
}

/// Lenient field decoding: a missing, null or mistyped field becomes nil instead of failing the message.
extension KeyedDecodingContainer {
    func lenient<T: Decodable>(_ type: T.Type, _ key: Key) -> T? {
        (try? decodeIfPresent(T.self, forKey: key)) ?? nil
    }
}

/// Mac -> El Jefe -> Dot link health (`link.pong.link` and `device.status` el_jefe.link).
struct LinkInfo: Codable, Hashable {
    var transport: String?      // ble | usb | mock
    var device: String?         // e.g. DataFeelCE
    var rssi: Double?           // dBm at connect, or nil
    var ok: Bool?               // nil = not checked yet
    var ms: Double?             // register-read round trip on every dot
    var lastSeen: Double?       // unix seconds of the last answer
    var dots: [Int]?            // answering addresses
    var addresses: [Int]?       // expected addresses
    var linkUp: Bool?
    var reason: String?         // human sentence; "ok" when healthy

    enum CodingKeys: String, CodingKey {
        case transport, device, rssi, ok, ms, dots, addresses, reason
        case lastSeen = "last_seen", linkUp = "link_up"
    }

    init(transport: String? = nil, device: String? = nil, rssi: Double? = nil, ok: Bool? = nil, ms: Double? = nil,
         lastSeen: Double? = nil, dots: [Int]? = nil, addresses: [Int]? = nil, linkUp: Bool? = nil, reason: String? = nil) {
        self.transport = transport; self.device = device; self.rssi = rssi; self.ok = ok; self.ms = ms
        self.lastSeen = lastSeen; self.dots = dots; self.addresses = addresses; self.linkUp = linkUp; self.reason = reason
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        transport = c.lenient(String.self, .transport); device = c.lenient(String.self, .device)
        rssi = c.lenient(Double.self, .rssi); ok = c.lenient(Bool.self, .ok); ms = c.lenient(Double.self, .ms)
        lastSeen = c.lenient(Double.self, .lastSeen); dots = c.lenient([Int].self, .dots)
        addresses = c.lenient([Int].self, .addresses); linkUp = c.lenient(Bool.self, .linkUp)
        reason = c.lenient(String.self, .reason)
    }
}

struct EngineSettings: Codable, Hashable {
    var quiet: Bool = false
    var intensity: Double = 1.0  // 0...1. Engine stores + broadcasts it; hardware scaling is TODO.
}

// MARK: - Server -> app (everything except events, which use HapticEvent)

struct HelloMessage: Decodable {
    var rules: [SenseRule]?     // nil from engines older than this protocol
    var devices: [DeviceInfo]?
    var settings: EngineSettings?
    var status: StatusMessage?  // v3: the first `status`, inlined
}
struct RulesListMessage: Decodable { var rules: [SenseRule] }
struct DeviceStatusMessage: Decodable { var devices: [DeviceInfo] }
struct AckMessage: Decodable {
    var `for`: String?
    var ok: Bool?
    var name: String?
    var detail: String?
    var error: String?
    var ms: Double?             // device time for haptic.* acks
    var capped: Bool?           // haptic.thermal: target was above the 40 °C cap
    var mode: String?           // haptic.thermal: manual | target
    var value: Double?          // haptic.thermal: applied intensity or target °C
    var reason: String?

    enum CodingKeys: String, CodingKey { case `for`, ok, name, detail, error, ms, capped, mode, value, reason }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.for = c.lenient(String.self, .for); ok = c.lenient(Bool.self, .ok)
        name = c.lenient(String.self, .name); detail = c.lenient(String.self, .detail)
        error = c.lenient(String.self, .error); ms = c.lenient(Double.self, .ms)
        capped = c.lenient(Bool.self, .capped); mode = c.lenient(String.self, .mode)
        value = c.lenient(Double.self, .value); reason = c.lenient(String.self, .reason)
    }
}

// MARK: - Haptics tab (server -> app)

struct ThermalSession: Decodable, Hashable {
    var mode: String?           // manual | target
    var value: Double?          // intensity -1...1 or target °C
    var baseC: Double?
    var reached: [String: Double]?   // "+2C" / "+4C" / "-2C" / "target" -> seconds
    var elapsedS: Double?
    var remainingS: Double?     // until auto-off
    var rateCPerS: Double?

    enum CodingKeys: String, CodingKey {
        case mode, value, reached
        case baseC = "base_c", elapsedS = "elapsed_s", remainingS = "remaining_s", rateCPerS = "rate_c_per_s"
    }

    init(mode: String? = nil, value: Double? = nil, baseC: Double? = nil, reached: [String: Double]? = nil,
         elapsedS: Double? = nil, remainingS: Double? = nil, rateCPerS: Double? = nil) {
        self.mode = mode; self.value = value; self.baseC = baseC; self.reached = reached
        self.elapsedS = elapsedS; self.remainingS = remainingS; self.rateCPerS = rateCPerS
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mode = c.lenient(String.self, .mode); value = c.lenient(Double.self, .value)
        baseC = c.lenient(Double.self, .baseC); reached = c.lenient([String: Double].self, .reached)
        elapsedS = c.lenient(Double.self, .elapsedS); remainingS = c.lenient(Double.self, .remainingS)
        rateCPerS = c.lenient(Double.self, .rateCPerS)
    }
}

struct ThermalLimits: Decodable, Hashable {
    var targetCapC: Double = 40
    var skinCutoffC: Double = 41
    var thermalMaxS: Double = 30

    enum CodingKeys: String, CodingKey {
        case targetCapC = "target_cap_c", skinCutoffC = "skin_cutoff_c", thermalMaxS = "thermal_max_s"
    }

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        targetCapC = c.lenient(Double.self, .targetCapC) ?? 40
        skinCutoffC = c.lenient(Double.self, .skinCutoffC) ?? 41
        thermalMaxS = c.lenient(Double.self, .thermalMaxS) ?? 30
    }
}

/// `haptic.telemetry`, 2 Hz while the app sends `haptic.subscribe`.
struct HapticTelemetry: Decodable, Hashable {
    var ts: Double
    var skinC: Double?
    var sinkC: Double?
    var thermalPower: Double?
    var lastCmdMs: Double?
    var intensity: Double?
    var thermal: ThermalSession?
    var limits: ThermalLimits?

    enum CodingKeys: String, CodingKey {
        case ts, thermal, limits, intensity
        case skinC = "skin_c", sinkC = "sink_c", thermalPower = "thermal_power", lastCmdMs = "last_cmd_ms"
    }

    init(ts: Double, skinC: Double?, sinkC: Double?, thermalPower: Double? = nil, lastCmdMs: Double? = nil,
         intensity: Double? = nil, thermal: ThermalSession? = nil, limits: ThermalLimits? = nil) {
        self.ts = ts; self.skinC = skinC; self.sinkC = sinkC; self.thermalPower = thermalPower
        self.lastCmdMs = lastCmdMs; self.intensity = intensity; self.thermal = thermal; self.limits = limits
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ts = c.lenient(Double.self, .ts) ?? Date().timeIntervalSince1970
        skinC = c.lenient(Double.self, .skinC); sinkC = c.lenient(Double.self, .sinkC)
        thermalPower = c.lenient(Double.self, .thermalPower); lastCmdMs = c.lenient(Double.self, .lastCmdMs)
        intensity = c.lenient(Double.self, .intensity); thermal = c.lenient(ThermalSession.self, .thermal)
        limits = c.lenient(ThermalLimits.self, .limits)
    }
}

/// Reply to `link.ping`. App RTT = now - t_app; app<->Mac = RTT - mac_ms; Mac<->Dot = ble_ms.
struct LinkPong: Decodable {
    var id: Int?
    var tApp: Double?
    var macMs: Double?
    var bleMs: Double?
    var flashMs: Double?
    var link: LinkInfo?

    enum CodingKeys: String, CodingKey {
        case id, link
        case tApp = "t_app", macMs = "mac_ms", bleMs = "ble_ms", flashMs = "flash_ms"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.lenient(Int.self, .id); tApp = c.lenient(Double.self, .tApp)
        macMs = c.lenient(Double.self, .macMs); bleMs = c.lenient(Double.self, .bleMs)
        flashMs = c.lenient(Double.self, .flashMs); link = c.lenient(LinkInfo.self, .link)
    }
}

/// `system`: link lost/restored, thermal safety cut-offs. Shown as system rows in the Log.
struct SystemMessage: Decodable {
    var ts: Double?
    var level: String?          // info | warning | error
    var text: String?
}

/// `trace`: one per full Haiku call while the app is subscribed (`trace.subscribe`). Every field is
/// optional and decoded leniently; a malformed decision is dropped rather than failing the trace.
struct Trace: Decodable, Identifiable {
    let uid = UUID()            // local identity (the engine's `id` can repeat after a restart)
    var callID: Int?            // `id`: the Haiku call id (matches event.call_id)
    var ts: Double?             // frame capture time (unix s)
    var t: Double?              // result time (unix s)
    var hazard: String?
    var urgency: Double?
    var reason: String?
    var latencyMs: Double?
    var nImages: Int?
    var paused: Bool?
    var stale: Bool?
    var error: String?
    var raw: String?            // Haiku's raw reply (≤2000 chars)
    var thumb: UIImage?         // decoded `thumb` (base64 JPEG ~240 px, gaze dot + bbox drawn)
    var nearMiss = false        // `near_miss`, else derived: a decision matched but didn't fire
    var decisions: [TraceDecision] = []

    var id: UUID { uid }
    var date: Date { Date(timeIntervalSince1970: t ?? ts ?? Date().timeIntervalSince1970) }

    enum CodingKeys: String, CodingKey {
        case id, ts, t, hazard, urgency, reason, paused, stale, error, raw, decisions, thumb
        case latencyMs = "latency_ms", nImages = "n_images", nearMiss = "near_miss"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        callID = c.lenient(Int.self, .id); ts = c.lenient(Double.self, .ts); t = c.lenient(Double.self, .t)
        hazard = c.lenient(String.self, .hazard); urgency = c.lenient(Double.self, .urgency)
        reason = c.lenient(String.self, .reason); latencyMs = c.lenient(Double.self, .latencyMs)
        nImages = c.lenient(Int.self, .nImages); paused = c.lenient(Bool.self, .paused)
        stale = c.lenient(Bool.self, .stale); error = c.lenient(String.self, .error)
        raw = c.lenient(String.self, .raw)
        decisions = (c.lenient([Lossy<TraceDecision>].self, .decisions) ?? []).compactMap(\.value)
        if let b64 = c.lenient(String.self, .thumb), !b64.isEmpty,
           let data = Data(base64Encoded: b64, options: .ignoreUnknownCharacters) {
            thumb = UIImage(data: data)
        }
        nearMiss = c.lenient(Bool.self, .nearMiss)
            ?? decisions.contains { $0.matched == true && $0.fired != true }
    }
}

/// One enabled camera trigger's verdict for a traced Haiku call.
struct TraceDecision: Decodable, Identifiable {
    let uid = UUID()
    var triggerID: String?
    var name: String?
    var fired: Bool?
    var matched: Bool?
    var why: String?
    var gazeMode: String?
    var haikuReason: String?

    var id: UUID { uid }
    var label: String { name ?? triggerID ?? "?" }

    enum CodingKeys: String, CodingKey {
        case name, fired, matched, why
        case triggerID = "id", gazeMode = "gaze_mode", haikuReason = "haiku_reason"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        triggerID = c.lenient(String.self, .triggerID); name = c.lenient(String.self, .name)
        fired = c.lenient(Bool.self, .fired); matched = c.lenient(Bool.self, .matched)
        why = c.lenient(String.self, .why); gazeMode = c.lenient(String.self, .gazeMode)
        haikuReason = c.lenient(String.self, .haikuReason)
    }
}

/// Decodes one array element, or nil if it is malformed, so one bad element can't sink the array.
struct Lossy<T: Decodable>: Decodable {
    var value: T?
    init(from decoder: Decoder) throws { value = try? T(from: decoder) }
}

// MARK: - App -> server

enum Verdict: String, Codable { case useful, noisy }

struct RulesUpsert: Encodable { let type = "rules.upsert"; var rule: SenseRule }
struct RulesDelete: Encodable { let type = "rules.delete"; var id: String }
/// Restore the engine's default triggers (blindspot + heads_up); it replies with rules.list.
struct RulesReset: Encodable { let type = "rules.reset" }
struct FeedbackMessage: Encodable {
    let type = "feedback"
    var eventId: Int
    var ts: Double
    var verdict: Verdict
    enum CodingKeys: String, CodingKey { case type, ts, verdict, eventId = "event_id" }
}

enum CommandName: String, Codable { case testPattern = "test_pattern", stopAll = "stop_all", resetDot = "reset_dot", setIntensity = "set_intensity", quiet, pause }

/// Small JSON value for command args, and for round-tripping rule keys the app doesn't model (RuleCondition.extra).
indirect enum JSONValue: Codable, Hashable {
    case string(String), number(Double), bool(Bool), null
    case array([JSONValue]), object([String: JSONValue])

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }   // before Double: JSON true must not become 1
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else if let o = try? c.decode([String: JSONValue].self) { self = .object(o) }
        else { self = .null }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        case .bool(let b): try c.encode(b)
        case .null: try c.encodeNil()
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }
}

/// Any string key, for dictionaries with keys not known at compile time.
struct AnyCodingKey: CodingKey, Hashable {
    var stringValue: String
    var intValue: Int? { nil }
    init(_ s: String) { stringValue = s }
    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { stringValue = String(intValue) }
}

struct CommandMessage: Encodable {
    let type = "command"
    var name: CommandName
    var args: [String: JSONValue] = [:]

    static func testPattern(_ p: String) -> Self { .init(name: .testPattern, args: ["pattern": .string(p)]) }
    static let stopAll = Self(name: .stopAll)
    /// Back to baseline: LED off, vibration stopped, thermal off.
    static let resetDot = Self(name: .resetDot)
    static func setIntensity(_ v: Double) -> Self { .init(name: .setIntensity, args: ["value": .number(v)]) }
    static func quiet(_ on: Bool) -> Self { .init(name: .quiet, args: ["on": .bool(on)]) }
    /// v3: while paused, detection keeps running for the Log but nothing reaches the Dot.
    static func pause(_ on: Bool) -> Self { .init(name: .pause, args: ["on": .bool(on)]) }
}

// MARK: - Haptics tab (app -> server). Each gets an `ack` with `for`, `ok`, `ms`.

struct HapticSubscribe: Encodable { let type = "haptic.subscribe"; var on: Bool }
/// Live decisions: engine streams `trace` while it got one of these within the last 5 s.
struct TraceSubscribe: Encodable { let type = "trace.subscribe"; var on: Bool }

struct HapticLED: Encodable {
    let type = "haptic.led"
    var mode: String            // off | global | individual | breathe | track_thermal
    var rgb: [Int]
    var index: Int? = nil       // 0-7: set just this LED (individual mode)
}

/// One sequence step: a DataFeel waveform name or a rest in seconds (0-1.27).
enum VibeStep: Encodable, Hashable {
    case wave(String), rest(Double)
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .wave(let n): try c.encode(n)
        case .rest(let s): try c.encode(s)
        }
    }
}

struct HapticVibeSequence: Encodable { let type = "haptic.vibe.sequence"; var steps: [VibeStep] }
struct HapticVibeManual: Encodable { let type = "haptic.vibe.manual"; var hz: Double; var intensity: Double; var on: Bool }
struct HapticPreset: Encodable { let type = "haptic.preset"; var name: String }

struct HapticThermal: Encodable {
    let type = "haptic.thermal"
    var on: Bool
    var intensity: Double? = nil    // -1 (cool) ... +1 (heat)
    var targetC: Double? = nil      // engine caps at 40 °C
    enum CodingKeys: String, CodingKey { case type, on, intensity, targetC = "target_c" }
}

struct HapticStopAll: Encodable { let type = "haptic.stop_all" }

struct LinkPing: Encodable {
    let type = "link.ping"
    var id: Int
    var tApp: Double
    var flash: Bool
    enum CodingKeys: String, CodingKey { case type, id, flash, tApp = "t_app" }
}
