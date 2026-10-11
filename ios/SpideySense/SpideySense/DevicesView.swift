import SwiftUI

struct DevicesView: View {
    let stream: EventStream

    /// The Mac hub card always reflects the real WebSocket; the rest come from the engine
    /// (device.status every ~5 s) or sample data while offline.
    private var cards: [DeviceInfo] {
        var list = stream.devices
        if let i = list.firstIndex(where: { $0.id == "mac_hub" }) {
            switch stream.status {
            case .connected: list[i].status = "connected"
            case .connecting: list[i].status = "connecting"; list[i].detail = "Connecting…"
            case .disconnected: list[i].status = "disconnected"; list[i].detail = stream.lastError ?? "Not connected"
            }
        }
        return list
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 12) {
                    if stream.devicesAreSample {
                        SampleBanner(text: "Sample devices: the Mac engine isn't connected, so these cards are placeholders.")
                    }
                    TimelineView(.periodic(from: .now, by: 1)) { ctx in
                        VStack(spacing: 12) {
                            ForEach(cards) { DeviceCard(device: $0, now: ctx.date) }
                        }
                    }
                    if let t = stream.devicesUpdatedAt, !stream.devicesAreSample {
                        Text("Updated \(t.formatted(date: .omitted, time: .standard))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                .padding()
            }
            .navigationTitle("Devices")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarLeading) { StatusDot(status: stream.status) } }
        }
    }
}

struct DeviceCard: View {
    let device: DeviceInfo
    let now: Date

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: icon)
                .font(.title2).foregroundStyle(color)
                .frame(width: 44, height: 44)
                .background(color.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(title).font(.headline)
                    Spacer()
                    Text(statusLabel).font(.caption.bold()).foregroundStyle(color)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(color.opacity(0.15), in: Capsule())
                }
                Text(subtitle).font(.subheadline).foregroundStyle(.secondary)
                if let d = device.detail { Text(d).font(.footnote) }
                Text(lastSeen).font(.caption).foregroundStyle(.tertiary)
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 14))
    }

    private var title: String {
        switch device.id {
        case "el_jefe": "El Jefe \"\(device.name)\""
        default: device.name
        }
    }
    private var subtitle: String {
        switch device.id {
        case "mac_hub": "WebSocket hub on the Mac"
        case "el_jefe":
            ["ble", "usb"].contains(device.transport ?? "ble")
                ? "DataFeel haptics, \((device.transport ?? "ble").uppercased()) via the Mac"
                : "DataFeel haptics (simulated on the Mac)"
        case "neon": "Eye tracker via the Companion phone"
        case "oura": "Heart rate ring"
        default: device.kind
        }
    }
    private var icon: String {
        switch device.id {
        case "mac_hub": "desktopcomputer"; case "el_jefe": "hand.raised.fingers.spread"
        case "neon": "eyeglasses"; case "oura": "circle.circle"
        default: "sensor"
        }
    }
    private var statusLabel: String {
        switch device.status {
        case "coming_soon": "Coming soon"
        default: device.status.capitalized
        }
    }
    private var color: Color {
        switch device.status {
        case "connected": .green; case "simulated": .teal; case "connecting": .orange
        case "coming_soon": .gray; default: .red
        }
    }
    private var lastSeen: String {
        guard let ts = device.lastSeen else { return device.status == "coming_soon" ? "" : "Last seen: never" }
        let s = max(0, Int(now.timeIntervalSince1970 - ts))
        if s < 60 { return "Last seen \(s)s ago" }
        if s < 3600 { return "Last seen \(s / 60)m ago" }
        return "Last seen \(Date(timeIntervalSince1970: ts).formatted(date: .omitted, time: .shortened))"
    }
}
