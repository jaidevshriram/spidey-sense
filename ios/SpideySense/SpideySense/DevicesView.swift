import SwiftUI

struct DevicesView: View {
    let stream: EventStream
    var embedded = false   // pushed from the Debug hub (no own NavigationStack)

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
        // v3: `status.source` / `status.radar` (2 s) are fresher than the device cards (5 s); prefer them.
        if let st = stream.engineStatus {
            if let src = st.source, let i = list.firstIndex(where: { $0.id == "neon" }) { list[i].source = src }
            if let r = st.radar {
                if let i = list.firstIndex(where: { $0.id == "radar" }) {
                    list[i].radar = r
                } else {
                    let status = r.connected == true ? (r.mode == "sim" ? "simulated" : "connected") : "disconnected"
                    let idx = list.firstIndex(where: { $0.id == "oura" }) ?? list.endIndex
                    list.insert(DeviceInfo(id: "radar", name: "Radar", kind: "radar", status: status,
                                           lastSeen: r.last?.ts ?? r.lastTs, radar: r), at: idx)
                }
            }
        }
        return list
    }

    var body: some View {
        NavWrap(embedded: embedded) {
            ScrollView {
                VStack(spacing: 12) {
                    if stream.devicesAreSample {
                        SampleBanner(text: "Sample devices: the Mac engine isn't connected, so these cards are placeholders.")
                    }
                    TimelineView(.periodic(from: .now, by: 1)) { ctx in
                        VStack(spacing: 12) {
                            ForEach(cards) { c in
                                DeviceCard(device: c, now: ctx.date,
                                           onReset: c.id == "el_jefe" ? { stream.command(.resetDot) } : nil)
                            }
                        }
                    }
                    if let t = stream.devicesUpdatedAt, !stream.devicesAreSample {
                        Text("Updated \(t.formatted(date: .omitted, time: .standard))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                .padding()
            }
            .engineHeader(stream)
            .navigationTitle("Devices")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarLeading) { StatusDot(status: stream.status) } }
        }
    }
}

struct DeviceCard: View {
    let device: DeviceInfo
    let now: Date
    var onReset: (() -> Void)? = nil

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
                // Newer engines already fold fps/gaze/battery into `detail`; only add the line when they don't.
                if let src = device.source, !(device.detail ?? "").contains("fps") { NeonDetail(source: src) }
                if let r = device.radar { RadarDetail(radar: r, now: now) }
                if let h = device.heart { HeartDetail(heart: h, now: now, lastSeen: device.lastSeen) }
                if let l = device.link, l.transport != nil {
                    let bits = [l.ms.map { "read \(fmtMs($0)) ms" }, l.rssi.map { "rssi \(Int($0)) dBm" },
                                l.dots.map { "dots \($0.map(String.init).joined(separator: ","))" }].compactMap { $0 }
                    if !bits.isEmpty {
                        Text(bits.joined(separator: " · ")).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                    if let r = l.reason, !r.isEmpty, r != "ok" {
                        Label(r, systemImage: "exclamationmark.triangle.fill").font(.caption)
                            .foregroundStyle(l.ok == false ? .red : .orange)
                    }
                }
                Text(lastSeen).font(.caption).foregroundStyle(.tertiary)
                if let onReset {
                    Button { onReset() } label: { Label("Reset Dot", systemImage: "arrow.counterclockwise") }
                        .buttonStyle(.bordered).controlSize(.small)
                        .accessibilityHint("LED off, vibration stopped, thermal off")
                }
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
        case "neon": device.source?.isFake == true ? "Fake camera source on the Mac" : "Eye tracker via the Companion phone"
        case "radar": device.radar?.mode == "sim" ? "Radar (simulated) via radar-lab" : "Radar board via radar-lab"
        case "oura":
            switch device.heart?.backend {
            case "open_oura": "Oura ring, live heart rate via open_oura (Bluetooth, Mac)"
            case "replay": "Heart rate replayed from a CSV on the Mac"
            case "fake": "Fake heart rate on the Mac"
            default: device.status == "coming_soon" ? "Heart rate ring" : "Heart rate ring (open_oura)"
            }
        default: device.kind
        }
    }
    private var icon: String {
        switch device.id {
        case "mac_hub": "desktopcomputer"; case "el_jefe": "hand.raised.fingers.spread"
        case "neon": "eyeglasses"; case "oura": "heart.circle"; case "radar": "dot.radiowaves.right"
        default: "sensor"
        }
    }
    private var statusLabel: String {
        switch device.status {
        case "coming_soon": "Coming soon"
        case "off": "Off"
        default: device.status.capitalized
        }
    }
    private var color: Color {
        switch device.status {
        case "connected": .green; case "simulated": .teal; case "connecting": .orange
        case "coming_soon", "off": .gray; default: .red
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

/// Neon card extras: live/fake, fps, gaze Hz, worn, battery, delay.
struct NeonDetail: View {
    let source: SourceStatus
    var body: some View {
        let bits: [String] = [
            source.isFake ? "fake" : (source.live == false ? "not live" : "live"),
            source.fps.map { String(format: "%.0f fps", $0) },
            source.gazeHz.map { String(format: "gaze %.0f Hz", $0) },
            source.worn.map { $0 ? "worn" : "not worn" },
            source.battery.map { "battery \(Int($0))%" },
            source.delayMs.map { String(format: "delay %.0f ms", $0) },
        ].compactMap { $0 }
        Text(bits.joined(separator: " · "))
            .font(.caption.monospacedDigit())
            .foregroundStyle(source.isFake ? .orange : (source.battery ?? 100) < 15 ? .red : .secondary)
    }
}

/// Radar card extras: connected/sim, url, last reading, rate, time since the last reading.
struct RadarDetail: View {
    let radar: RadarStatus
    let now: Date
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text([radar.connected == true ? "connected" : "not connected", radar.mode,
                  radar.rateHz.map { String(format: "%.0f Hz", $0) }].compactMap { $0 }.joined(separator: " · "))
                .font(.caption.monospacedDigit()).foregroundStyle(radar.connected == true ? Color.secondary : Color.red)
            if let u = radar.url { Text(u).font(.caption.monospaced()).foregroundStyle(.secondary) }
            if let l = radar.last {
                let angle = l.angleDeg.map { String(format: " · %.0f°", $0) } ?? ""
                Label("last: \(l.summary.isEmpty ? "—" : l.summary)\(angle)", systemImage: "scope")
                    .font(.caption.monospacedDigit()).foregroundStyle(.teal)
            }
            if let since = sinceLast {
                Text(String(format: "%.1f s since the last reading", since)).font(.caption).foregroundStyle(.tertiary)
            }
        }
    }
    private var sinceLast: Double? {
        if let ts = radar.last?.ts ?? radar.lastTs { return max(0, now.timeIntervalSince1970 - ts) }
        return radar.sinceLastS
    }
}

/// Oura card extras: backend, last HR, 2-min median, connected / time since the last beat.
struct HeartDetail: View {
    let heart: HeartCard
    let now: Date
    let lastSeen: Double?
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 10) {
                Label(heart.lastBpm.map { "\(Int($0.rounded())) bpm" } ?? "— bpm", systemImage: "heart.fill")
                    .font(.title3.monospacedDigit().bold()).foregroundStyle(.pink)
                VStack(alignment: .leading, spacing: 0) {
                    Text("2-min median").font(.caption2).foregroundStyle(.secondary)
                    Text(heart.medianBpm.map { "\(Int($0.rounded())) bpm" } ?? "warming up")
                        .font(.subheadline.monospacedDigit())
                }
            }
            Text([heart.backend, connectedText, heart.rateHz.map { String(format: "%.1f Hz", $0) }]
                    .compactMap { $0 }.joined(separator: " · "))
                .font(.caption.monospacedDigit())
                .foregroundStyle(heart.connected == false && !heart.isSimulated ? Color.red : Color.secondary)
            if let since = sinceLast {
                Text(String(format: "%.1f s since the last beat", since)).font(.caption).foregroundStyle(.tertiary)
            }
        }
    }
    private var connectedText: String? {
        heart.connected.map { $0 ? (heart.isSimulated ? "running" : "connected") : "not connected" }
    }
    private var sinceLast: Double? {
        if let ts = lastSeen { return max(0, now.timeIntervalSince1970 - ts) }
        return heart.sinceLastS
    }
}
