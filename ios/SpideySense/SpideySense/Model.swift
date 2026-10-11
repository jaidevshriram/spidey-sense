import Foundation

/// One haptic event as broadcast by the engine. Raw JSON is kept for the detail sheet.
struct HapticEvent: Identifiable {
    let id: Int
    let ts: Double
    let pattern: String
    let dot: Int?
    let rule: String
    let reason: String
    let hazard: String?
    let direction: String?
    let seenByUser: Bool
    let urgency: Double?
    let latencyMs: Double?
    let quiet: Bool            // engine was in quiet mode: logged, not felt
    let raw: [String: Any]

    init?(_ d: [String: Any]) {
        guard let id = (d["id"] as? NSNumber)?.intValue else { return nil }
        let det = d["detection"] as? [String: Any] ?? [:]
        self.id = id
        ts = (d["ts"] as? NSNumber)?.doubleValue ?? Date().timeIntervalSince1970
        pattern = d["pattern"] as? String ?? "?"
        dot = (d["dot"] as? NSNumber)?.intValue
        rule = d["rule"] as? String ?? ""
        reason = d["reason"] as? String ?? ""
        hazard = det["hazard"] as? String
        direction = det["where"] as? String
        seenByUser = det["seen_by_user"] as? Bool ?? false
        urgency = (det["urgency"] as? NSNumber)?.doubleValue
        latencyMs = (d["latency_ms"] as? NSNumber)?.doubleValue
        quiet = d["quiet"] as? Bool ?? false
        var r = d
        r.removeValue(forKey: "type")
        raw = r
    }

    /// Dedupe key: id plus timestamp, so an engine restart that resets ids is not swallowed.
    var key: String { "\(id)@\(ts)" }

    var date: Date { Date(timeIntervalSince1970: ts) }

    /// Built by MockData for the offline demo, never from the engine.
    var isSample: Bool { raw["sample"] as? Bool ?? false }

    var prettyJSON: String {
        guard let data = try? JSONSerialization.data(withJSONObject: raw, options: [.prettyPrinted, .sortedKeys]),
              let s = String(data: data, encoding: .utf8) else { return "\(raw)" }
        return s
    }
}
