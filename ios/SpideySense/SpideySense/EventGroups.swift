import Foundation
import SwiftUI

/// Repeated buzzes from the same alert collapse into one card.
///
/// Key = trigger id + the object label (the main noun of Haiku's `hazard`, e.g. "tiger standing on a rock" -> "tiger",
/// when the event has one), so "lion" and "tiger"
/// from the same "Big animal" alert stay separate. Events are walked oldest -> newest; an event joins the
/// open group with the same key if it came within `window` seconds of that group's LAST buzz (a rolling
/// window: a lion that keeps buzzing every 20 s stays one card), otherwise it starts a new group.
struct EventGroup: Identifiable {
    var events: [HapticEvent]          // newest first
    let key: String

    /// Stable across new buzzes: the first event in the group.
    var id: String { "\(key)#\(events.last?.key ?? "")" }
    var latest: HapticEvent { events[0] }
    var first: HapticEvent { events[events.count - 1] }
    var count: Int { events.count }
    var lastDate: Date { latest.date }
    var isSample: Bool { latest.isSample }
    /// The newest event that carries a snapshot (camera events).
    var snapshotEvent: HapticEvent? { events.first { $0.thumb != nil } }
    var snapshots: [HapticEvent] { events.filter { $0.thumb != nil } }

    static let window: TimeInterval = 60
    static let freshFor: TimeInterval = 5 * 60

    func isFresh(now: Date) -> Bool { now.timeIntervalSince(lastDate) < Self.freshFor }

    static func key(_ e: HapticEvent) -> String {
        guard let label = e.objectLabel else { return e.triggerID }
        return "\(e.triggerID)|\(label)"
    }

    /// `events` newest first (as EventStream keeps them). Returns groups newest first.
    static func build(_ events: [HapticEvent], window: TimeInterval = window) -> [EventGroup] {
        var open: [String: Int] = [:]       // key -> index in `groups`
        var groups: [EventGroup] = []
        for e in events.reversed() {        // oldest first
            let k = key(e)
            if let i = open[k], e.ts - groups[i].latest.ts <= window {
                groups[i].events.insert(e, at: 0)
            } else {
                groups.append(EventGroup(events: [e], key: k))
                open[k] = groups.count - 1
            }
        }
        return groups.sorted { $0.latest.ts > $1.latest.ts }
    }
}

// MARK: - Plain-language text for an event

extension HapticEvent {
    /// The main noun of what fired: Haiku's free-text `hazard` ("Adult tiger standing on rock" -> "tiger"), else the
    /// first noun of its reason ("Tiger visible on the right…" -> "tiger"). nil if none.
    var objectLabel: String? {
        let skip: Set<String> = ["a", "an", "the", "adult", "large", "big", "small", "young", "wild", "some", "one", "two",
                                 "possible", "potential", "partially", "visible", "sitting", "standing", "there", "is",
                                 "it", "this", "that", "wearer", "your", "you", "no", "yes", "match", "matched", "clearly"]
        func noun(_ text: String?) -> String? {
            guard let h = text?.lowercased(), !h.isEmpty, h != "none" else { return nil }
            return h.split(whereSeparator: { !$0.isLetter }).map(String.init).first { !skip.contains($0) && $0.count > 1 }
        }
        if isRadar || isHeart { return nil }
        return noun(hazard) ?? noun(cleanWhy)
    }

    /// Haiku's reason without the engine's appended diagnostics ("…; gaze in box 0.21 s [gaze cache …]").
    var cleanWhy: String? {
        guard let w = why else { return nil }
        var s = w
        for sep in ["; ", " ["] { if let r = s.range(of: sep) { s = String(s[..<r.lowerBound]) } }
        s = s.trimmingCharacters(in: .whitespaces)
        if s.hasSuffix(".;") { s.removeLast() }
        return s.isEmpty ? nil : s
    }

    /// The friendly name of the alert that fired ("Lion I haven't noticed").
    var alertName: String {
        let n = triggerLabel.trimmingCharacters(in: .whitespaces)
        if n.isEmpty { return "Spidey Sense" }
        if n.contains("_") && !n.contains(" ") {   // a bare id like heads_up
            let s = n.replacingOccurrences(of: "_", with: " ")
            return s.prefix(1).uppercased() + s.dropFirst()
        }
        return n
    }

    /// A short sentence for the card: "A lion on your right. You hadn't looked at it."
    var headline: String {
        if isHeart {
            if let b = heart?.bpm { return "Your heart rate jumped to \(Int(b.rounded())) bpm." }
            return "Your heart rate jumped."
        }
        if isRadar {
            let side = radar?.side.flatMap { $0 == "center" ? nil : $0 }
            return "Someone came up behind you" + (side.map { " on the \($0)" } ?? "") + "."
        }
        var s: String
        if let subj = Plain.subject(objectLabel) {
            s = subj + (Plain.place(direction).map { " \($0)" } ?? "")
        } else {
            s = "Your \u{201C}\(alertName)\u{201D} alert went off"
        }
        if let t = trend, t.touching { s += ", touching" }
        else if let t = trend, t.ordinal.last == "closer" || (t.gap.count > 1 && t.gap.last! < t.gap.first!) { s += ", getting closer" }
        s += "."
        switch gazeMode {
        case "unseen": s += " You hadn't looked at it."
        case "looking_at": s += " You were looking at it."
        default: if !seenByUser && hazard != nil && gazeMode == nil && !isRadar { s += " You hadn't looked at it." }
        }
        return s
    }

    /// Haiku's reason in plain words (or the engine's reason for older engines).
    var plainWhy: String {
        if let w = cleanWhy { return w }
        if isHeart, let h = heart { return h.summary }
        if isRadar, let r = radar, !r.summary.isEmpty { return "Radar: \(r.summary)" }
        return reason.isEmpty ? "Your \"\(alertName)\" alert matched what the glasses saw." : reason
    }
}
