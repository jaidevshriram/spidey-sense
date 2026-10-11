import Foundation

// Wire protocol between the app and the Mac engine. Keep in sync with docs/protocol.md
// and spidey/server.py + spidey/engine.py (Controller).
//
// server -> app: hello{history, rules, devices, settings}, event{...}, rules.list{rules},
//                device.status{devices}, settings{quiet, intensity}, ack{for, ok, ...}, error{error}
// app -> server: rules.upsert{rule}, rules.delete{id}, feedback{event_id, ts, verdict},
//                command{name, args}
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

    enum CodingKeys: String, CodingKey {
        case id, name, kind, status, detail, transport, sink
        case lastSeen = "last_seen"
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
