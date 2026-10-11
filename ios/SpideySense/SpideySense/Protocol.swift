import Foundation

// Wire protocol between the app and the Mac engine. Keep in sync with docs/protocol.md
// and spidey/server.py + spidey/engine.py (Controller).
//
// server -> app: hello{history, rules, devices, settings}, event{...}, rules.list{rules},
//                device.status{devices}, settings{quiet, intensity}, ack{for, ok, ms, ...}, error{error},
//                haptic.telemetry{...}, link.pong{...}, system{ts, level, text}
// app -> server: rules.upsert{rule}, rules.delete{id}, feedback{event_id, ts, verdict},
//                command{name, args}, haptic.subscribe/led/vibe.sequence/vibe.manual/preset/thermal/stop_all,
//                link.ping{id, t_app, flash}
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
struct RuleCondition: Codable, Hashable {
    var unseen: Bool?
    var seen: Bool?
    var minUrgency: Double?
    var maxUrgency: Double?
    var hrBandBpm: Double?      // heart rate outside 2-min median ± this
    var hrWindowS: Double?      // median window, default 120 s
    var metric: String?         // Mac metric name, e.g. "cpu_temp_c"
    var valueGt: Double?        // fire when metric > this

    enum CodingKeys: String, CodingKey {
        case unseen, seen, metric
        case minUrgency = "min_urgency", maxUrgency = "max_urgency"
        case hrBandBpm = "hr_band_bpm", hrWindowS = "hr_window_s", valueGt = "value_gt"
    }
}

enum SenseSource: String, CaseIterable, Identifiable, Codable {
    case neon, heartRate = "heart_rate", macMetric = "mac_metric", manual
    var id: String { rawValue }
    var label: String {
        switch self {
        case .neon: "Neon vision"; case .heartRate: "Heart rate"
        case .macMetric: "Mac metric"; case .manual: "Manual"
        }
    }
    var icon: String {
        switch self {
        case .neon: "eye"; case .heartRate: "heart"; case .macMetric: "desktopcomputer"; case .manual: "hand.tap"
        }
    }
    /// Only Neon vision feeds the engine today; the rest are stubs on the Mac side.
    var isWired: Bool { self == .neon }
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

    enum CodingKeys: String, CodingKey {
        case id, name, enabled, source, when, feel, dot
        case cond = "if", cooldownS = "cooldown_s"
    }

    init(id: String, name: String, enabled: Bool = true, source: String = "neon", when: RuleWhen = .any,
         cond: RuleCondition = RuleCondition(), feel: String = "urgent", dot: Int? = nil, cooldownS: Double = 3) {
        self.id = id; self.name = name; self.enabled = enabled; self.source = source; self.when = when
        self.cond = cond; self.feel = feel; self.dot = dot; self.cooldownS = cooldownS
    }

    // Tolerant decoding: everything but id/name has a default.
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
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id); try c.encode(name, forKey: .name)
        try c.encode(enabled, forKey: .enabled); try c.encode(source, forKey: .source)
        try c.encode(when, forKey: .when); try c.encode(cond, forKey: .cond)
        try c.encode(feel, forKey: .feel); try c.encode(dot, forKey: .dot)   // null = all dots
        try c.encode(cooldownS, forKey: .cooldownS)
    }

    var senseSource: SenseSource? { SenseSource(rawValue: source) }
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

    enum CodingKeys: String, CodingKey {
        case id, name, kind, status, detail, transport, sink, link
        case lastSeen = "last_seen"
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

// MARK: - App -> server

enum Verdict: String, Codable { case useful, noisy }

struct RulesUpsert: Encodable { let type = "rules.upsert"; var rule: SenseRule }
struct RulesDelete: Encodable { let type = "rules.delete"; var id: String }
struct FeedbackMessage: Encodable {
    let type = "feedback"
    var eventId: Int
    var ts: Double
    var verdict: Verdict
    enum CodingKeys: String, CodingKey { case type, ts, verdict, eventId = "event_id" }
}

enum CommandName: String, Codable { case testPattern = "test_pattern", stopAll = "stop_all", setIntensity = "set_intensity", quiet }

/// Small JSON value for command args.
enum JSONValue: Encodable, Hashable {
    case string(String), number(Double), bool(Bool), null
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        case .bool(let b): try c.encode(b)
        case .null: try c.encodeNil()
        }
    }
}

struct CommandMessage: Encodable {
    let type = "command"
    var name: CommandName
    var args: [String: JSONValue] = [:]

    static func testPattern(_ p: String) -> Self { .init(name: .testPattern, args: ["pattern": .string(p)]) }
    static let stopAll = Self(name: .stopAll)
    static func setIntensity(_ v: Double) -> Self { .init(name: .setIntensity, args: ["value": .number(v)]) }
    static func quiet(_ on: Bool) -> Self { .init(name: .quiet, args: ["on": .bool(on)]) }
}

// MARK: - Haptics tab (app -> server). Each gets an `ack` with `for`, `ok`, `ms`.

struct HapticSubscribe: Encodable { let type = "haptic.subscribe"; var on: Bool }

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
