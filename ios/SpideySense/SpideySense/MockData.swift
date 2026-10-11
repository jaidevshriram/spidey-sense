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

    static var rules: [SenseRule] {
        var r = presets
        r[2].enabled = false
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
        let rows: [(String, String, String, Bool, Double, String, Double)] = [
            ("urgent", "bike", "left", false, 0.86, "blindspot", 12),
            ("ambient", "person", "right", true, 0.35, "heads_up", 47),
            ("urgent", "car", "behind-left", false, 0.92, "blindspot", 95),
            ("pulse", "heart rate", "", false, 0.5, "heartbeat_shift", 160),
        ]
        return rows.enumerated().compactMap { i, r in
            HapticEvent([
                "id": 900 + i, "ts": now - r.6, "pattern": r.0, "dot": NSNull(), "rule": r.5,
                "reason": r.5 == "heartbeat_shift" ? "heartbeat_shift: HR 94 bpm, 2-min median 74 ± 15"
                    : "\(r.5): \(r.1) \(r.2) \(r.3 ? "seen" : "unseen") u=\(String(format: "%.2f", r.4))",
                "detection": ["hazard": r.1, "where": r.2.isEmpty ? NSNull() : r.2,
                              "seen_by_user": r.3, "urgency": r.4] as [String: Any],
                "latency_ms": 80 + Double(i) * 7, "sample": true,
            ])
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
