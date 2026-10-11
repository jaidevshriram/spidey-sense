import Foundation

/// Sample data shown while the app isn't connected to an engine. Every view that shows it
/// carries a "Sample data" banner, and sample events never send feedback to the Mac.
enum MockData {
    static let presets: [SenseRule] = [
        SenseRule(id: "blindspot", name: "Blindspot", source: SenseSource.neon.rawValue, when: .any,
                  cond: RuleCondition(unseen: true, minUrgency: 0.6), feel: "urgent", dot: nil, cooldownS: 3),
        SenseRule(id: "heartbeat_shift", name: "Heartbeat shift", source: SenseSource.heartRate.rawValue,
                  cond: RuleCondition(hrBandBpm: 15, hrWindowS: 120), feel: "pulse", dot: nil, cooldownS: 30),
        SenseRule(id: "machine_alert", name: "Machine alert", source: SenseSource.macMetric.rawValue,
                  cond: RuleCondition(metric: "cpu_temp_c", valueGt: 90), feel: "ambient", dot: 2, cooldownS: 60),
    ]

    /// One-tap plain-English templates for the Add trigger sheet.
    static let customTemplates: [SenseRule] = [
        custom("knife_nearby", "Knife nearby",
               "someone holding a knife or a knife within reach, and I haven't looked at it",
               feel: "urgent"),
        custom("someone_behind_me", "Someone behind me",
               "a person approaching from the side or behind, outside where I'm looking",
               feel: "pulse"),
        custom("stairs_step_down", "Stairs / step down", "a step, stairs or a drop ahead of me",
               feel: "ambient"),
    ]

    static func custom(_ id: String, _ name: String, _ describe: String, feel: String,
                       intensity: Double = 1, minUrgency: Double = 0.5, cooldown: Double = 5) -> SenseRule {
        SenseRule(id: id, name: name, source: SenseSource.neon.rawValue, when: .any,
                  cond: RuleCondition(unseen: true, minUrgency: minUrgency), feel: feel, dot: nil, cooldownS: cooldown,
                  kind: "custom", describe: describe, intensity: intensity, rgb: TriggerColor.defaultRGB(feel))
    }

    static var rules: [SenseRule] {
        var r = presets.map { p -> SenseRule in var p = p; p.kind = "builtin"; return p }
        r[2].enabled = false
        var knife = customTemplates[0]
        knife.intensity = 0.8
        r.insert(knife, at: 1)
        return r
    }

    static let devices: [DeviceInfo] = {
        let now = Date().timeIntervalSince1970
        return [
            DeviceInfo(id: "mac_hub", name: "Mac hub", kind: "websocket", status: "disconnected",
                       detail: "Not connected (sample)", lastSeen: nil),
            DeviceInfo(id: "el_jefe", name: "DataFeelCE", kind: "haptics", status: "connected",
                       detail: "datafeel-ble via the Mac (ble)", transport: "ble", sink: "datafeel-ble", lastSeen: now - 4,
                       link: MockData.link),
            DeviceInfo(id: "neon", name: "Neon", kind: "eye_tracker", status: "connected",
                       detail: "source: neon (realtime)", lastSeen: now - 1),
            DeviceInfo(id: "oura", name: "Oura", kind: "heart_rate", status: "coming_soon",
                       detail: "Heart-rate source coming soon", lastSeen: nil),
        ]
    }()

    static let events: [HapticEvent] = {
        let now = Date().timeIntervalSince1970
        // (pattern, hazard, where, seen, urgency, trigger id, trigger name, seconds ago, why)
        let rows: [(String, String, String, Bool, Double, String, String, Double, String?)] = [
            ("urgent", "knife", "right", false, 0.81, "knife_nearby", "Knife nearby", 6,
             "Person at the counter on your right is holding a kitchen knife; you're looking left."),
            ("urgent", "bike", "left", false, 0.86, "blindspot", "Blindspot", 12,
             "Cyclist closing fast on your left, outside your gaze."),
            ("ambient", "person", "right", true, 0.35, "heads_up", "heads_up", 47, nil),
            ("urgent", "car", "behind-left", false, 0.92, "blindspot", "Blindspot", 95,
             "Car reversing behind-left; you haven't looked that way."),
            ("pulse", "heart rate", "", false, 0.5, "heartbeat_shift", "Heartbeat shift", 160, nil),
        ]
        return rows.enumerated().compactMap { i, r in
            var d: [String: Any] = [
                "id": 900 + i, "ts": now - r.7, "pattern": r.0, "dot": NSNull(), "rule": r.5,
                "trigger": r.5, "trigger_name": r.6,
                "reason": r.5 == "heartbeat_shift" ? "heartbeat_shift: HR 94 bpm, 2-min median 74 ± 15"
                    : "\(r.5): \(r.1) \(r.2) \(r.3 ? "seen" : "unseen") u=\(String(format: "%.2f", r.4))",
                "detection": ["hazard": r.1, "where": r.2.isEmpty ? NSNull() : r.2,
                              "seen_by_user": r.3, "urgency": r.4] as [String: Any],
                "latency_ms": 80 + Double(i) * 7, "sample": true,
            ]
            if let why = r.8 { d["why"] = why }
            return HapticEvent(d)
        }
    }()

    // MARK: - Haptics tab (offline demo)

    static let link = LinkInfo(transport: "ble", device: "DataFeelCE", rssi: -41, ok: true, ms: 62,
                               lastSeen: Date().timeIntervalSince1970 - 1, dots: [3], addresses: [3],
                               linkUp: true, reason: "ok")

    /// Gently varying skin/sink temperatures for the last `seconds`, 2 Hz, ending at `now`.
    static func telemetryHistory(now: Date, seconds: Double = 120) -> [HapticTelemetry] {
        let end = now.timeIntervalSince1970.rounded(.down)
        return stride(from: end - seconds, through: end, by: 0.5).map { telemetry(at: $0) }
    }

    static func telemetry(at ts: Double) -> HapticTelemetry {
        let skin = 31.2 + 0.6 * sin(ts / 9) + 0.15 * sin(ts / 2.3)
        let sink = 30.1 - 0.3 * sin(ts / 9) + 0.1 * cos(ts / 3.1)
        return HapticTelemetry(ts: ts, skinC: skin, sinkC: sink, thermalPower: 0, lastCmdMs: 58, intensity: 1,
                               thermal: nil, limits: ThermalLimits())
    }
}
