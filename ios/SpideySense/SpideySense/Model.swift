import Foundation
import UIKit

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
    let trigger: String?       // id of the trigger that fired (newer engines)
    let triggerName: String?   // its display name
    let why: String?           // Haiku's one-line reason (newer engines)
    // v3 (all optional)
    let sensor: String?        // camera | radar
    let thumb: UIImage?        // decoded `thumb` (base64 JPEG, gaze dot + bbox drawn)
    let radar: RadarReading?
    let gazeMode: String?
    let lookedAtSAgo: Double?
    let paused: Bool           // engine was paused: logged, nothing reached the Dot
    let trend: EventTrend?
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
        trigger = (d["trigger"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        triggerName = (d["trigger_name"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        why = (d["why"] as? String).flatMap { $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : $0 }
        radar = RadarReading(d["radar"] as? [String: Any])
        sensor = (d["sensor"] as? String) ?? (radar != nil ? "radar" : nil)
        if let b64 = d["thumb"] as? String, !b64.isEmpty,
           let data = Data(base64Encoded: b64, options: .ignoreUnknownCharacters) {
            thumb = UIImage(data: data)
        } else {
            thumb = nil
        }
        gazeMode = d["gaze_mode"] as? String
        lookedAtSAgo = (d["looked_at_s_ago"] as? NSNumber)?.doubleValue
        paused = d["paused"] as? Bool ?? false
        trend = EventTrend(d["trend"] as? [String: Any])
        var r = d
        r.removeValue(forKey: "type")
        // Keep the detail JSON readable: the image is shown above it.
        if let t = d["thumb"] as? String { r["thumb"] = "<base64 JPEG, \(t.count) chars>" }
        raw = r
    }

    /// Dedupe key: id plus timestamp, so an engine restart that resets ids is not swallowed.
    var key: String { "\(id)@\(ts)" }

    var date: Date { Date(timeIntervalSince1970: ts) }

    /// Trigger id for filtering: `trigger`, else the engine's slug of the rule name (older engines send the name).
    var triggerID: String { trigger ?? triggerSlug(rule) }
    /// Label for the row: `trigger_name`, falling back to `rule`.
    var triggerLabel: String { triggerName ?? rule }

    var isRadar: Bool { sensor == "radar" }

    /// Built by MockData for the offline demo, never from the engine.
    var isSample: Bool { raw["sample"] as? Bool ?? false }

    var prettyJSON: String {
        guard let data = try? JSONSerialization.data(withJSONObject: raw, options: [.prettyPrinted, .sortedKeys]),
              let s = String(data: data, encoding: .utf8) else { return "\(raw)" }
        return s
    }
}

/// A system row in the Log: from the engine (`system` message) or local (WebSocket drop/reconnect).
struct SystemRow: Identifiable, Hashable {
    enum Level: String { case info, warning, error }
    let id = UUID()
    let ts: Double
    let level: Level
    let text: String
    let local: Bool            // true = logged by this phone, not the engine

    var date: Date { Date(timeIntervalSince1970: ts) }
}

/// One reply to a haptics-tab command, for the tab's command log.
struct AckEntry: Identifiable, Hashable {
    let id = UUID()
    let date: Date
    let kind: String           // e.g. haptic.led, command set_intensity
    let ok: Bool
    let ms: Double?
    let note: String?          // error or detail
}

/// Result of one link.ping round trip.
struct PingResult: Hashable {
    let date: Date
    let flash: Bool
    let rttMs: Double          // now - t_app
    let macMs: Double?         // time spent inside the engine
    let bleMs: Double?         // Mac <-> El Jefe <-> Dot register read
    let flashMs: Double?       // LED flash write
    var appMacMs: Double { max(0, rttMs - (macMs ?? 0)) }
}

/// Same as the engine's `rules.slug`: lowercase, runs of non [a-z0-9] -> "_", trimmed.
func triggerSlug(_ s: String, fallback: String = "rule") -> String {
    var out = "", lastUnderscore = false
    for ch in s.lowercased() {
        if ch.isASCII && (ch.isLetter || ch.isNumber) { out.append(ch); lastUnderscore = false }
        else if !lastUnderscore { out.append("_"); lastUnderscore = true }
    }
    let t = out.trimmingCharacters(in: CharacterSet(charactersIn: "_"))
    return t.isEmpty ? fallback : t
}

/// Event `trend` for temporal triggers. v3.3: {gap: [..] body-heights, ordinal: [closer|same|farther], touching, loom}.
/// Also reads the superseded v3.1/v3.2 shape {values: [..], unit: m|closeness|gap}.
struct EventTrend {
    var gap: [Double] = []
    var ordinal: [String] = []
    var touching = false
    var loom: Double?
    var values: [Double] = []   // legacy
    var unit: String?           // legacy

    init?(_ d: [String: Any]?) {
        guard let d else { return nil }
        func nums(_ k: String) -> [Double] { (d[k] as? [Any] ?? []).compactMap { ($0 as? NSNumber)?.doubleValue } }
        gap = nums("gap"); values = nums("values")
        ordinal = (d["ordinal"] as? [Any] ?? []).compactMap { $0 as? String }
        touching = d["touching"] as? Bool ?? false
        loom = (d["loom"] as? NSNumber)?.doubleValue
        unit = d["unit"] as? String
        if gap.isEmpty && values.isEmpty && ordinal.isEmpty && !touching { return nil }
    }

    /// "gap 0.8 → 0.4 → 0.1 body-heights · closer ×3"
    var summary: String {
        var parts: [String] = []
        if !gap.isEmpty {
            parts.append("gap " + gap.map { String(format: "%.1f", $0) }.joined(separator: " → ") + " body-heights")
        } else if !values.isEmpty {
            switch unit {
            case "closeness", "gap": parts.append((unit == "gap" ? "gap " : "")
                + values.map { "\(Int(($0 * 100).rounded()))%" }.joined(separator: " → "))
            default: parts.append(values.map { String(format: "%.1f", $0) }.joined(separator: " → ") + " \(unit ?? "")")
            }
        }
        if let last = ordinal.last {
            let run = ordinal.reversed().prefix { $0 == last }.count
            parts.append(run > 1 ? "\(last) ×\(run)" : last)
        }
        return parts.joined(separator: " · ")
    }
}
