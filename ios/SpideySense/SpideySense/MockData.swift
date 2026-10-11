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

    /// Offline sample for Home + the Log: a lion that buzzed 3 times in the last ~1.5 min (one collapsed card),
    /// a cyclist 3 min ago (second fresh card), and older items for "Earlier".
    static let events: [HapticEvent] = {
        let now = Date().timeIntervalSince1970
        let water = sampleThumb().jpegData(compressionQuality: 0.8)?.base64EncodedString() ?? ""
        func lion(_ shift: CGFloat) -> String { lionThumb(shift: shift).jpegData(compressionQuality: 0.8)?.base64EncodedString() ?? "" }
        // (pattern, hazard, where, seen, urgency, trigger id, trigger name, seconds ago, why)
        let rows: [(String, String, String, Bool, Double, String, String, Double, String?)] = [
            ("urgent", "lion", "right", false, 0.86, "lion_unnoticed", "Lion I haven't noticed", 12,
             "A lion is standing at the edge of the path on your right, and your eyes haven't gone to it."),
            ("urgent", "lion", "right", false, 0.82, "lion_unnoticed", "Lion I haven't noticed", 41,
             "A lion is on your right, partly behind the bushes. You were looking at the path ahead."),
            ("urgent", "lion", "right", false, 0.80, "lion_unnoticed", "Lion I haven't noticed", 83,
             "A lion appeared on the right side of the view; you haven't looked at it."),
            ("urgent", "bike", "left", false, 0.86, "blindspot", "Blindspot", 190,
             "A cyclist is coming up quickly on your left, outside where you're looking."),
            ("urgent", "person", "behind-left", false, 0.9, "someone_behind", "Someone behind me", 18 * 60, nil),
            ("urgent", "person", "ahead", false, 0.84, "person_to_water_unwatched", "Person near water I'm not watching", 52 * 60,
             "A child is walking toward the lake edge over the last few seconds while you look away."),
            ("urgent", "person", "ahead", false, 0.80, "person_to_water_unwatched", "Person near water I'm not watching", 52 * 60 + 35,
             "A child is getting closer to the water's edge."),
            ("pulse", "", "", false, 0.5, "heart_racing", "Heart racing", 2 * 3600 + 300,
             "Your heart rate went to 104 bpm, 33 above your normal of 71, for 12 seconds."),
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
                d["why"] = "Someone walked up to about 1.4 m behind you, on your left."
            case "person_to_water_unwatched":
                d["sensor"] = "camera"; d["thumb"] = water; d["gaze_mode"] = "unseen"; d["looked_at_s_ago"] = 5.2
                d["trend"] = ["gap": [0.8, 0.4, 0.1], "ordinal": ["closer", "closer", "closer"],
                              "touching": false, "loom": 1.3] as [String: Any]
            case "lion_unnoticed":
                d["sensor"] = "camera"; d["thumb"] = lion(CGFloat(i) * 6); d["gaze_mode"] = "unseen"
                d["looked_at_s_ago"] = 4.1 + Double(i); d["call_id"] = 4120 + i
                d["raw"] = "{\"hazard\": \"lion\", \"x\": 0.78, \"urgency\": 0.86, \"triggers\": {\"lion_unnoticed\": {\"match\": true, \"bbox\": [0.66, 0.38, 0.92, 0.80]}}}"
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

    /// Demo hook (`-demoArrive YES`): a fresh sample event, as if the engine just sent it.
    static func demoEvent(_ kind: String, id: Int) -> HapticEvent? {
        let now = Date().timeIntervalSince1970
        var d: [String: Any] = ["id": id, "ts": now, "pattern": "urgent", "dot": NSNull(), "sample": true,
                                "sensor": "camera", "gaze_mode": "unseen", "latency_ms": 910]
        switch kind {
        case "lion":
            d["rule"] = "lion_unnoticed"; d["trigger"] = "lion_unnoticed"; d["trigger_name"] = "Lion I haven't noticed"
            d["detection"] = ["hazard": "lion", "where": "right", "seen_by_user": false, "urgency": 0.88] as [String: Any]
            d["why"] = "The lion is still on your right and a little closer; you haven't looked at it yet."
            d["thumb"] = lionThumb(shift: 14).jpegData(compressionQuality: 0.8)?.base64EncodedString() ?? ""
            d["looked_at_s_ago"] = 4.8
        default:
            d["rule"] = "step_down"; d["trigger"] = "step_down"; d["trigger_name"] = "Stairs or a step down"
            d["detection"] = ["hazard": "step", "where": "ahead", "seen_by_user": false, "urgency": 0.7] as [String: Any]
            d["why"] = "There's a step down about two paces ahead and you're looking to the side."
        }
        d["reason"] = "\(d["trigger"]!): sample"
        return HapticEvent(d)
    }

    /// Sample camera frame (320 x 200): a savanna path, a lion on the right in a box, the gaze dot on the path.
    static func lionThumb(shift: CGFloat = 0) -> UIImage {
        let size = CGSize(width: 320, height: 200)
        return UIGraphicsImageRenderer(size: size).image { ctx in
            let c = ctx.cgContext
            UIColor(red: 0.70, green: 0.84, blue: 0.95, alpha: 1).setFill(); c.fill(CGRect(x: 0, y: 0, width: 320, height: 92))
            UIColor(red: 0.86, green: 0.76, blue: 0.48, alpha: 1).setFill(); c.fill(CGRect(x: 0, y: 92, width: 320, height: 108))
            UIColor(red: 0.47, green: 0.60, blue: 0.32, alpha: 1).setFill()
            for x in stride(from: -10, to: 330, by: 46) { c.fillEllipse(in: CGRect(x: CGFloat(x), y: 76, width: 60, height: 30)) }
            // path
            UIColor(red: 0.78, green: 0.66, blue: 0.42, alpha: 1).setFill()
            let p = UIBezierPath(); p.move(to: CGPoint(x: 120, y: 200)); p.addLine(to: CGPoint(x: 150, y: 96))
            p.addLine(to: CGPoint(x: 166, y: 96)); p.addLine(to: CGPoint(x: 230, y: 200)); p.close(); p.fill()
            // lion
            let lion = NSAttributedString(string: "🦁", attributes: [.font: UIFont.systemFont(ofSize: 58)])
            lion.draw(at: CGPoint(x: 226 - shift, y: 86))
            // bbox
            UIColor.yellow.setStroke(); c.setLineWidth(2.5)
            c.stroke(CGRect(x: 218 - shift, y: 80, width: 82, height: 82))
            // gaze dot on the path ahead (not on the lion)
            UIColor.red.setFill(); c.fillEllipse(in: CGRect(x: 146, y: 118, width: 14, height: 14))
            UIColor.white.setStroke(); c.setLineWidth(2); c.strokeEllipse(in: CGRect(x: 146, y: 118, width: 14, height: 14))
        }
    }

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
