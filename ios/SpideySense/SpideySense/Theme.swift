import SwiftUI
import UIKit

// Consumer look: light, calm, generous whitespace, one warm coral accent (from the app icon's red dot),
// soft rounded cards. Used by Home, My alerts, the New-alert wizard and Settings.
// The technical (Developer mode) tabs keep their own styling.

enum Theme {
    static let accent = Color(red: 1.0, green: 0.31, blue: 0.37)          // #FF4F5E, the icon's red dot
    static let accentSoft = Color(red: 1.0, green: 0.31, blue: 0.37).opacity(0.10)
    static let ink = Color(red: 0.11, green: 0.11, blue: 0.13)             // near-black text
    static let inkSecondary = Color(red: 0.42, green: 0.42, blue: 0.46)
    static let inkTertiary = Color(red: 0.62, green: 0.62, blue: 0.66)
    static let background = Color(red: 0.975, green: 0.973, blue: 0.968)   // warm off-white
    static let card = Color.white
    static let hairline = Color.black.opacity(0.07)
    static let good = Color(red: 0.13, green: 0.62, blue: 0.40)
    static let warn = Color(red: 0.93, green: 0.55, blue: 0.10)

    static let radius: CGFloat = 20
    static let smallRadius: CGFloat = 14

    /// On-screen version of a Dot LED colour: pure LED primaries are harsh on white, so the defaults map to
    /// softer equivalents (white, invisible on a card, shows as grey). Other colours are shown as-is.
    static func led(_ rgb: [Int]) -> Color {
        switch rgb {
        case [255, 0, 0]: return accent
        case [255, 45, 140]: return Color(red: 0.93, green: 0.27, blue: 0.52)
        case [0, 90, 255]: return Color(red: 0.25, green: 0.47, blue: 0.95)
        case [255, 150, 0]: return Color(red: 0.95, green: 0.55, blue: 0.12)
        case [0, 200, 90]: return Color(red: 0.13, green: 0.68, blue: 0.42)
        case [255, 255, 255]: return Color(red: 0.62, green: 0.62, blue: 0.66)
        default: return TriggerColor.color(rgb)
        }
    }

    /// The one spring used everywhere (Airbnb-like, subtle).
    static let spring = Animation.spring(response: 0.35, dampingFraction: 0.85)

    /// The spring, or a short fade when Reduce Motion is on.
    static func motion(_ reduce: Bool) -> Animation { reduce ? .easeInOut(duration: 0.2) : spring }
}

// MARK: - Typography (one place to swap the family)

/// Headings use Plus Jakarta Sans (SIL OFL 1.1, Tokotype; bundled variable font `Fonts/PlusJakartaSans.ttf`,
/// registered via `UIAppFonts` in project.yml): big, bold, friendly, Airbnb-like. Body text stays SF Pro.
/// If the font didn't load, headings fall back to SF Pro Rounded. All sizes are Dynamic Type-relative.
/// To swap the family: change `customFamily` (and the bundled file + UIAppFonts), or set it to nil.
enum Typo {
    static let customFamily: String? = "Plus Jakarta Sans"

    /// True when the bundled font actually registered (checked once).
    static let customLoaded: Bool = {
        guard let f = customFamily else { return false }
        let ok = UIFont.familyNames.contains(f)
        #if DEBUG
        print("[Typo] \(f) loaded: \(ok); faces: \(UIFont.fontNames(forFamilyName: f))")
        #endif
        return ok
    }()

    static func heading(_ style: Font.TextStyle, _ weight: Font.Weight) -> Font {
        if customLoaded, let f = customFamily {
            return Font.custom(f, size: size(style), relativeTo: style).weight(weight)
        }
        return .system(style, design: .rounded, weight: weight)
    }

    private static func size(_ style: Font.TextStyle) -> CGFloat {
        switch style {
        case .largeTitle: 34; case .title: 28; case .title2: 22; case .title3: 20; case .headline: 17
        default: 17
        }
    }
}

extension Font {
    static let spLargeTitle = Typo.heading(.largeTitle, .bold)     // screen titles, 34 pt
    static let spTitle = Typo.heading(.title, .bold)               // detail headline, 28 pt
    static let spTitle2 = Typo.heading(.title2, .bold)             // hero card headline, 22 pt
    static let spCardTitle = Typo.heading(.title3, .semibold)      // card / choice titles, 20 pt
    static let spHeadline = Typo.heading(.headline, .semibold)     // row titles, buttons, 17 pt
}

/// `withAnimation` that respects Reduce Motion (read from UIKit so it works outside views).
func withSpring(_ body: () -> Void) {
    withAnimation(Theme.motion(UIAccessibility.isReduceMotionEnabled), body)
}

/// Light tap on the phone (selection feedback).
func softHaptic() { UISelectionFeedbackGenerator().selectionChanged() }

// MARK: - Cards

struct SoftCard: ViewModifier {
    var padding: CGFloat = 20
    var radius: CGFloat = Theme.radius
    func body(content: Content) -> some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous).stroke(Theme.hairline, lineWidth: 1))
            .shadow(color: .black.opacity(0.06), radius: 14, x: 0, y: 6)
    }
}

extension View {
    func softCard(padding: CGFloat = 20, radius: CGFloat = Theme.radius) -> some View {
        modifier(SoftCard(padding: padding, radius: radius))
    }
}

/// Big filled pill button: the one primary action on a screen.
struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    var color: Color = Theme.accent
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.spHeadline)
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity, minHeight: 56)
            .background(enabled ? color : Theme.inkTertiary.opacity(0.5),
                        in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(Theme.spring, value: configuration.isPressed)
    }
}

/// Outlined secondary button.
struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.spHeadline)
            .foregroundStyle(Theme.ink)
            .frame(maxWidth: .infinity, minHeight: 56)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Theme.ink.opacity(0.18), lineWidth: 1.5))
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(Theme.spring, value: configuration.isPressed)
    }
}

/// Cards and tiles: a gentle press-down.
struct PressableStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(Theme.spring, value: configuration.isPressed)
    }
}

// MARK: - Connection pill

/// Minimal connection state for Home: "Watching" / "Connecting…" / "Not connected".
struct StatusPill: View {
    let status: EventStream.Status
    var sample = false
    @State private var breathe = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 7) {
            Circle().fill(color).frame(width: 8, height: 8)
                .overlay(Circle().stroke(color.opacity(0.35), lineWidth: 5).scaleEffect(breathe ? 1.6 : 1).opacity(breathe ? 0 : 1))
            Text(label).font(.subheadline.weight(.medium)).foregroundStyle(Theme.ink)
        }
        .padding(.horizontal, 12).padding(.vertical, 7)
        .background(Theme.card, in: Capsule())
        .overlay(Capsule().stroke(Theme.hairline, lineWidth: 1))
        .onAppear {
            guard status == .connected, !reduceMotion else { return }
            withAnimation(.easeOut(duration: 1.6).repeatForever(autoreverses: false)) { breathe = true }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Connection: \(label)")
    }
    private var color: Color {
        switch status { case .connected: Theme.good; case .connecting: Theme.warn; case .disconnected: Theme.inkTertiary }
    }
    private var label: String {
        switch status {
        case .connected: "Watching"
        case .connecting: "Connecting…"
        case .disconnected: sample ? "Not connected" : "Offline"
        }
    }
}

/// Subtle "Sample" marker.
struct SampleTag: View {
    var body: some View {
        Text("SAMPLE")
            .font(.caption2.weight(.bold)).tracking(0.8)
            .foregroundStyle(Theme.inkSecondary)
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(Theme.ink.opacity(0.06), in: Capsule())
            .accessibilityLabel("Sample data")
    }
}

/// Section title used on the consumer screens.
struct SectionTitle: View {
    let text: String
    var body: some View {
        Text(text).font(.spTitle2).foregroundStyle(Theme.ink)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityAddTraits(.isHeader)
    }
}

// MARK: - Plain-language helpers

enum Plain {
    /// "just now" / "12 s ago" / "2 min ago" / "1 h ago" / "Yesterday 14:05".
    static func ago(_ date: Date, now: Date = Date()) -> String {
        let s = max(0, now.timeIntervalSince(date))
        if s < 5 { return "just now" }
        if s < 60 { return "\(Int(s)) s ago" }
        if s < 3600 { return "\(Int(s / 60)) min ago" }
        if s < 6 * 3600 { return "\(Int(s / 3600)) h ago" }
        return clock(date)
    }

    static func clock(_ date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = Calendar.current.isDateInToday(date) ? "HH:mm" : "EEE HH:mm"
        return f.string(from: date)
    }

    static func clockSeconds(_ date: Date) -> String {
        let f = DateFormatter(); f.dateFormat = "HH:mm:ss"; return f.string(from: date)
    }

    /// Friendly names for the three haptic patterns.
    static func feelName(_ feel: String) -> String {
        switch feel {
        case "urgent": "Strong tap"
        case "pulse": "Gentle pulse"
        case "ambient": "Soft hum"
        default: feel.capitalized
        }
    }

    static func feelBlurb(_ feel: String) -> String {
        switch feel {
        case "urgent": "A firm, unmistakable buzz. For things you shouldn't miss."
        case "pulse": "A heartbeat-like double tap. Noticeable, not alarming."
        case "ambient": "A light, soft hum in the background."
        default: "A custom pattern."
        }
    }

    static func feelIcon(_ feel: String) -> String {
        switch feel {
        case "urgent": "bolt.fill"
        case "pulse": "heart.fill"
        case "ambient": "wave.3.right"
        default: "waveform"
        }
    }

    /// "right" -> "on your right", "behind-left" -> "behind you on the left".
    static func place(_ whereStr: String?) -> String? {
        guard let w = whereStr?.lowercased().trimmingCharacters(in: .whitespaces), !w.isEmpty else { return nil }
        switch w {
        case "left": return "on your left"
        case "right": return "on your right"
        case "ahead", "front", "center", "centre": return "ahead of you"
        case "behind": return "behind you"
        case "behind-left", "behind_left": return "behind you on the left"
        case "behind-right", "behind_right": return "behind you on the right"
        default: return w.replacingOccurrences(of: "-", with: " ")
        }
    }

    /// "lion" -> "A lion", "person" -> "Someone", "elephant" -> "An elephant".
    static func subject(_ hazard: String?) -> String? {
        guard let h = hazard?.trimmingCharacters(in: .whitespaces).lowercased(), !h.isEmpty, h != "none" else { return nil }
        switch h {
        case "person", "people", "human", "someone": return "Someone"
        case "bike", "bicycle", "cyclist": return "A cyclist"
        case "car", "vehicle": return "A car"
        default:
            let article = "aeiou".contains(h.first!) ? "An" : "A"
            return "\(article) \(h)"
        }
    }

    static func sensorName(_ e: HapticEvent) -> (name: String, icon: String) {
        if e.isRadar { return ("Radar (behind you)", "dot.radiowaves.right") }
        if e.isHeart { return ("Heart-rate ring", "heart.fill") }
        return ("Glasses camera", "eyeglasses")
    }

    static func count(_ n: Int) -> String { n == 1 ? "Buzzed once" : "Buzzed \(n) times" }
}

/// LED colour for an event: the trigger's rgb if we know the trigger, else the pattern's default.
func ledColor(for trigger: String, pattern: String, rules: [SenseRule]) -> Color {
    if let r = rules.first(where: { $0.id == trigger }) {
        return Theme.led(r.rgb ?? TriggerColor.defaultRGB(r.feel))
    }
    return Theme.led(TriggerColor.defaultRGB(pattern))
}

extension View {
    /// Consumer screens hide the navigation bar; this keeps scrolled content from showing under the status bar.
    func statusBarBackdrop() -> some View {
        safeAreaInset(edge: .top, spacing: 0) {
            Color.clear.frame(height: 0).background(Theme.background.opacity(0.97))
        }
    }
}
