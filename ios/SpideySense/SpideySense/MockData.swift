import Foundation
import UIKit

/// Sample data shown while the app isn't connected to an engine. Every view that shows it
/// carries a "Sample data" banner, and sample events never send feedback to the Mac.
enum MockData {
    static let presets: [SenseRule] = [
        SenseRule(id: "blindspot", name: "Blindspot", source: SenseSource.neon.rawValue, when: .any,
                  cond: RuleCondition(unseen: true, minUrgency: 0.6), feel: "urgent", dot: nil, cooldownS: 3),
        SenseRule(id: "machine_alert", name: "Machine alert", source: SenseSource.macMetric.rawValue,
                  cond: RuleCondition(metric: "cpu_temp_c", valueGt: 90), feel: "ambient", dot: 2, cooldownS: 60),
    ]

    /// One-tap templates for the Add trigger sheet (v3: gaze modes, over-time, radar). Ids are fixed.
    static let customTemplates: [SenseRule] = {
        var bigCat = custom("big_cat_look", "Big cat (look)", "a big cat such as a lion or tiger", feel: "urgent",
                            gaze: .lookingAt)
        bigCat.cond.unseen = false
        let lion = custom("lion_unnoticed", "Lion I haven't noticed", "a lion or tiger", feel: "urgent",
                          gaze: .unseen, noticeMemory: 3)
        let water = "a person getting close to the edge of water (lake/river/sea) compared to earlier frames"
        var toWater = custom("person_to_water", "Person approaching water", water, feel: "pulse", gaze: .any)
        toWater.cond.unseen = false
        toWater.temporal = true; toWater.trend = TrendConfig(ticks: 3, sensitivity: "medium")
        var unwatched = custom("person_to_water_unwatched", "Person near water I'm not watching", water, feel: "urgent",
                               gaze: .unseen, noticeMemory: 3)
        unwatched.temporal = true; unwatched.trend = TrendConfig(ticks: 3, sensitivity: "medium")
        return [bigCat, lion, toWater, unwatched, radar("someone_behind", "Someone behind me", feel: "urgent"),
                heart("heart_racing", "Heart racing")]
    }()

    /// Heart-rate trigger: HR above the rolling 2-min median by `bpm` or `pct` %, for `sustain` s.
    static func heart(_ id: String, _ name: String, bpm: Double = 15, pct: Double = 20, sustain: Double = 8,
                      feel: String = "pulse", cooldown: Double = 60) -> SenseRule {
        SenseRule(id: id, name: name, source: SenseSource.heartRate.rawValue, when: .any,
                  cond: RuleCondition(minDeltaBpm: bpm, minDeltaPct: pct, sustainS: sustain),
                  feel: feel, dot: nil, cooldownS: cooldown, kind: "custom", describe: nil, intensity: 1,
                  rgb: TriggerColor.defaultRGB(feel))
    }

    static func custom(_ id: String, _ name: String, _ describe: String, feel: String, gaze: GazeMode = .unseen,
                       noticeMemory: Double? = nil, intensity: Double = 1, minUrgency: Double = 0.5,
                       cooldown: Double = 5) -> SenseRule {
        var r = SenseRule(id: id, name: name, source: SenseSource.neon.rawValue, when: .any,
                          cond: RuleCondition(unseen: gaze == .unseen, minUrgency: minUrgency), feel: feel, dot: nil,
                          cooldownS: cooldown, kind: "custom", describe: describe, intensity: intensity,
                          rgb: TriggerColor.defaultRGB(feel))
        r.gazeMode = gaze.rawValue
        r.noticeMemoryS = gaze == .unseen ? (noticeMemory ?? 3) : nil
        r.temporal = false
        return r
    }

    /// Radar trigger: a confirmed approaching target within `distance` m, faster than `speed` m/s. Always "unseen".
    static func radar(_ id: String, _ name: String, distance: Double = 2.0, speed: Double = 0.3, feel: String,
                      cooldown: Double = 5) -> SenseRule {
        var r = SenseRule(id: id, name: name, source: SenseSource.radar.rawValue, when: .any,
                          cond: RuleCondition(unseen: true, maxDistanceM: distance, minSpeedMps: speed),
                          feel: feel, dot: nil, cooldownS: cooldown, kind: "custom",
                          describe: "someone approaching me from behind (radar)", intensity: 1,
                          rgb: TriggerColor.defaultRGB(feel))
        r.gazeMode = GazeMode.unseen.rawValue
        return r
    }

    static var rules: [SenseRule] {
        var r = presets.map { p -> SenseRule in var p = p; p.kind = "builtin"; return p }
        r[1].enabled = false                       // machine_alert
        var lion = customTemplates[1]
        lion.intensity = 0.8
        r.insert(lion, at: 1)
        r.insert(customTemplates[3], at: 2)        // over time + unseen
        r.insert(customTemplates[0], at: 3)        // looking at it
        r.insert(customTemplates[4], at: 4)        // radar
        r.insert(customTemplates[5], at: 5)        // heart rate
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
                       detail: "source: neon (realtime)", lastSeen: now - 1, source: status.source),
            DeviceInfo(id: "radar", name: "Radar", kind: "radar", status: "simulated",
                       detail: "radar-lab server (sim)", lastSeen: now - 0.4, radar: status.radar),
            DeviceInfo(id: "oura", name: "Oura", kind: "heart_rate", status: "connected",
                       detail: "open_oura · 72 bpm · 2-min median 70 · open_oura: live", lastSeen: now - 0.8,
                       heart: HeartCard(backend: "open_oura", lastBpm: 72, medianBpm: 70, sinceLastS: 0.8,
                                        rateHz: 1.2, connected: true)),
        ]
    }()

    static let events: [HapticEvent] = {
        let now = Date().timeIntervalSince1970
        let thumbB64 = sampleThumb().jpegData(compressionQuality: 0.7)?.base64EncodedString() ?? ""
        // (pattern, hazard, where, seen, urgency, trigger id, trigger name, seconds ago, why)
        let rows: [(String, String, String, Bool, Double, String, String, Double, String?)] = [
            ("urgent", "person", "behind-left", false, 0.9, "someone_behind", "Someone behind me", 3, nil),
            ("urgent", "person", "ahead", false, 0.84, "person_to_water_unwatched", "Person near water I'm not watching", 6,
             "Child walking toward the lake edge over the last three frames; you're looking away."),
            ("urgent", "lion", "right", false, 0.81, "lion_unnoticed", "Lion I haven't noticed", 14,
             "Lion on the right side of the enclosure; your gaze hasn't been on it for 4 s."),
            ("urgent", "bike", "left", false, 0.86, "blindspot", "Blindspot", 22,
             "Cyclist closing fast on your left, outside your gaze."),
            ("ambient", "person", "right", true, 0.35, "heads_up", "heads_up", 47, nil),
            ("pulse", "", "", false, 0.5, "heart_racing", "Heart racing", 160,
             "HR 104 bpm, +33 over your 2-min median 71 for 12 s"),
        ]
        return rows.enumerated().compactMap { i, r in
            var d: [String: Any] = [
                "id": 900 + i, "ts": now - r.7, "pattern": r.0, "dot": NSNull(), "rule": r.5,
                "trigger": r.5, "trigger_name": r.6,
                "reason": r.5 == "heart_racing" ? "Heart racing: HR 104 bpm, +33 over your 2-min median 71 for 12 s"
                    : "\(r.5): \(r.1) \(r.2) \(r.3 ? "seen" : "unseen") u=\(String(format: "%.2f", r.4))",
                "detection": ["hazard": r.1, "where": r.2.isEmpty ? NSNull() : r.2,
                              "seen_by_user": r.3, "urgency": r.4] as [String: Any],
                "latency_ms": 880 + Double(i) * 37, "sample": true,
            ]
            if let why = r.8 { d["why"] = why }
            switch r.5 {
            case "someone_behind":
                d["sensor"] = "radar"; d["latency_ms"] = 42
                d["radar"] = ["distance_m": 1.4, "speed_mps": 0.6, "angle_deg": -32, "side": "left"] as [String: Any]
                d["why"] = "Radar: target approaching from behind-left, inside 2.0 m."
            case "person_to_water_unwatched":
                d["sensor"] = "camera"; d["thumb"] = thumbB64; d["gaze_mode"] = "unseen"; d["looked_at_s_ago"] = 5.2
                d["trend"] = ["gap": [0.8, 0.4, 0.1], "ordinal": ["closer", "closer", "closer"],
                              "touching": false, "loom": 1.3] as [String: Any]
            case "lion_unnoticed":
                d["sensor"] = "camera"; d["thumb"] = thumbB64; d["gaze_mode"] = "unseen"; d["looked_at_s_ago"] = 4.1
                d["paused"] = true
            case "blindspot", "heads_up":
                d["sensor"] = "camera"
            case "heart_racing":
                d["sensor"] = "heart"; d["latency_ms"] = 35; d.removeValue(forKey: "detection")
                d["heart"] = ["bpm": 104, "median_bpm": 71, "delta_bpm": 33, "above_s": 12] as [String: Any]
            default: break
            }
            return HapticEvent(d)
        }
    }()

    /// Tiny placeholder camera frame (240 x 160): lake, a person, the bbox and the gaze dot, like the engine draws.
    static func sampleThumb() -> UIImage {
        let size = CGSize(width: 240, height: 160)
        return UIGraphicsImageRenderer(size: size).image { ctx in
            let c = ctx.cgContext
            UIColor(red: 0.62, green: 0.78, blue: 0.92, alpha: 1).setFill(); c.fill(CGRect(x: 0, y: 0, width: 240, height: 70))
            UIColor(red: 0.45, green: 0.62, blue: 0.35, alpha: 1).setFill(); c.fill(CGRect(x: 0, y: 70, width: 240, height: 90))
            UIColor(red: 0.18, green: 0.42, blue: 0.75, alpha: 1).setFill()
            c.fillEllipse(in: CGRect(x: 120, y: 80, width: 150, height: 60))
            // person
            UIColor(white: 0.15, alpha: 1).setFill()
            c.fillEllipse(in: CGRect(x: 96, y: 74, width: 12, height: 12))
            c.fill(CGRect(x: 94, y: 86, width: 16, height: 30))
            // bbox
            UIColor.yellow.setStroke(); c.setLineWidth(2)
            c.stroke(CGRect(x: 88, y: 70, width: 28, height: 52))
            // gaze dot (looking away, top-left)
            UIColor.red.setFill(); c.fillEllipse(in: CGRect(x: 30, y: 40, width: 12, height: 12))
            UIColor.white.setStroke(); c.setLineWidth(1.5); c.strokeEllipse(in: CGRect(x: 30, y: 40, width: 12, height: 12))
        }
    }

    /// Offline stand-in for the engine's v3 `status`.
    static let status = StatusMessage(
        paused: false,
        source: SourceStatus(type: "neon", live: true, fps: 16.2, gazeHz: 130, worn: true, battery: 13, delayMs: 140),
        radar: RadarStatus(connected: true, url: "ws://localhost:8090", mode: "sim",
                           last: RadarReading(distanceM: 1.4, speedMps: 0.6, angleDeg: -32, side: "left"),
                           rateHz: 10, sinceLastS: 0.4),
        about: AboutInfo(config: "config.neon.yaml", model: "claude-haiku-5-5", commit: "bbee782", sink: "datafeel-ble"))

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
