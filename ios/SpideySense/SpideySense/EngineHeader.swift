import SwiftUI

/// v3 header strip shown on every tab: what the engine is reading from (Neon live / fake source / disconnected),
/// where the haptics go (real Dot over BLE vs the console sink). The pause switch lives in the Debug hub.
struct EngineHeader: View {
    let stream: EventStream

    var body: some View {
        VStack(spacing: 6) {
            HStack(spacing: 6) {
                Chip(text: source.text, icon: source.icon, color: source.color)
                if let s = sink { Chip(text: s.text, icon: s.icon, color: s.color) }
                if let r = radar { Chip(text: r.text, icon: r.icon, color: r.color) }
                Spacer(minLength: 4)
            }
            if stream.paused {
                Label("Paused (Debug → Pause detection): detection still logs, nothing reaches the Dot", systemImage: "pause.circle.fill")
                    .font(.caption.bold()).foregroundStyle(.orange)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 6)
        .background(.bar)
        .overlay(alignment: .bottom) { Divider() }
    }

    struct ChipInfo { var text: String; var icon: String; var color: Color }

    /// From `status.source`, else (older engine) the neon device card.
    private var source: ChipInfo {
        guard stream.isLive else { return ChipInfo(text: "Disconnected", icon: "wifi.slash", color: .gray) }
        if let s = stream.engineStatus?.source {
            if s.isFake { return ChipInfo(text: "Fake source", icon: "theatermasks.fill", color: .orange) }
            if s.live == false { return ChipInfo(text: "Neon offline", icon: "eyeglasses", color: .red) }
            let fps = s.fps.map { " · \(Int($0.rounded())) fps" } ?? ""
            return ChipInfo(text: "Neon live\(fps)", icon: "eyeglasses", color: .green)
        }
        if !stream.devicesAreSample, let n = stream.devices.first(where: { $0.id == "neon" }) {
            switch n.status {
            case "simulated": return ChipInfo(text: "Fake source", icon: "theatermasks.fill", color: .orange)
            case "connected": return ChipInfo(text: "Neon live", icon: "eyeglasses", color: .green)
            default: return ChipInfo(text: "Neon offline", icon: "eyeglasses", color: .red)
            }
        }
        return ChipInfo(text: "Engine live", icon: "desktopcomputer", color: .green)
    }

    /// From `status.about.sink`, else the El Jefe card's sink. Hidden offline.
    private var sink: ChipInfo? {
        guard stream.isLive else { return nil }
        let jefe = stream.devicesAreSample ? nil : stream.devices.first(where: { $0.id == "el_jefe" })
        guard let name = stream.engineStatus?.about?.sink ?? jefe?.sink else { return nil }
        if name == "console" { return ChipInfo(text: "No Dot — console", icon: "terminal", color: .orange) }
        if name.hasPrefix("datafeel") {
            let transport = name.hasSuffix("ble") ? "BLE" : "USB"
            let down = stream.link?.linkUp == false || jefe?.status == "disconnected"
            return ChipInfo(text: down ? "Dot · \(transport) down" : "Dot · \(transport)",
                            icon: "hand.raised.fingers.spread", color: down ? .red : .green)
        }
        return ChipInfo(text: name, icon: "hand.raised.fingers.spread", color: .orange)
    }

    private var radar: ChipInfo? {
        guard stream.isLive, let r = stream.engineStatus?.radar else { return nil }
        if r.connected != true { return ChipInfo(text: "Radar off", icon: "dot.radiowaves.right", color: .red) }
        return ChipInfo(text: r.mode == "sim" ? "Radar sim" : "Radar", icon: "dot.radiowaves.right",
                        color: r.mode == "sim" ? .orange : .green)
    }
}

struct Chip: View {
    let text: String
    let icon: String
    let color: Color
    var body: some View {
        Label(text, systemImage: icon)
            .font(.caption.bold())
            .labelStyle(.titleAndIcon)
            .lineLimit(1)
            .truncationMode(.tail)
            .minimumScaleFactor(0.85)
            .foregroundStyle(color)
            .padding(.horizontal, 7).padding(.vertical, 3)
            .background(color.opacity(0.15), in: Capsule())
    }
}

extension View {
    /// Pins the engine header (source, sink, pause) under the navigation bar.
    func engineHeader(_ stream: EventStream) -> some View {
        safeAreaInset(edge: .top, spacing: 0) { EngineHeader(stream: stream) }
    }
}

/// A NavigationStack unless the view is pushed inside one (the Debug hub).
struct NavWrap<Content: View>: View {
    let embedded: Bool
    @ViewBuilder let content: Content
    var body: some View {
        if embedded { content } else { NavigationStack { content } }
    }
}
