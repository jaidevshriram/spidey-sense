import Charts
import SwiftUI
import UIKit

/// Haptics tab: a native port of the web Haptic Playground, driven through the engine's WebSocket.
/// Its main job is checking the wireless links: Phone -> Mac (Wi-Fi WebSocket), Mac -> El Jefe (BLE),
/// El Jefe -> Dot. Protocol: docs/protocol.md "Haptics tab + link check".
struct HapticsView: View {
    let stream: EventStream

    @State private var color = Color(red: 0, green: 0.6, blue: 1)
    @State private var ledMode = "global"
    @State private var ledColors: [Color?] = Array(repeating: nil, count: 8)   // nil = unknown/off
    @State private var slots: [SeqSlot] = [.wave("STRONG_CLICK_P100"), .rest(0.2), .wave("BUZZ1_P100")]
        + Array(repeating: .empty, count: 5)
    @State private var hz = 170.0
    @State private var manualIntensity = 0.5
    @State private var manualOn = false
    @State private var thermalIntensity = 0.3
    @State private var targetC = 34.0
    @State private var intensity = 1.0

    private var live: Bool { stream.isLive }
    /// Upper bound of the target stepper. 40 °C (the engine's cap). The `-debugTargetMax 55` launch
    /// argument raises it so the engine-side cap can be tested from the app.
    private var targetMax: Double { max(40, UserDefaults.standard.double(forKey: "debugTargetMax")) }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 14) {
                    if !live {
                        SampleBanner(text: "Sample data: not connected to the Mac engine. Controls are disabled and the chart shows made-up temperatures. Set the server in Settings.")
                    }
                    LinkCheckCard(stream: stream)
                    summaryCard
                    lightCard
                    vibrationCard
                    thermalCard
                    commandLogCard
                }
                .padding()
            }
            .safeAreaInset(edge: .bottom) { stopBar }
            .engineHeader(stream)
            .navigationTitle("Haptics")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarLeading) { StatusDot(status: stream.status) } }
            .task {
                // While this tab is visible: keep telemetry streaming (and the engine's "thermal off
                // when the app goes away" timer fed) and measure the links every 2 s.
                while !Task.isCancelled {
                    stream.subscribeHaptics()
                    stream.ping(flash: false)
                    try? await Task.sleep(for: .seconds(2))
                }
            }
            .onAppear { intensity = stream.settings.intensity }
            .onChange(of: stream.settings.intensity) { _, v in intensity = v }
        }
    }

    // MARK: - 2. Connection summary

    private var summaryCard: some View {
        Card(title: "Connection", icon: "antenna.radiowaves.left.and.right") {
            let transport = stream.link?.transport
                ?? stream.devices.first(where: { $0.id == "el_jefe" })?.transport ?? "?"
            let lastMs = stream.telemetry?.lastCmdMs ?? stream.ackLog.first(where: { $0.ms != nil })?.ms
            LabeledContent("Transport", value: live ? transport.uppercased() : "— (offline)")
            LabeledContent("Last command", value: live ? (lastMs.map { "\(fmtMs($0)) ms" } ?? "—") : "—")
            VStack(alignment: .leading, spacing: 2) {
                Text("Master intensity \(Int((intensity * 100).rounded()))%")
                Slider(value: $intensity, in: 0...1, step: 0.05) { editing in
                    if !editing { tapFeedback(.light); stream.command(.setIntensity(intensity)) }
                }
                Text("Scales presets, sequences and manual vibration (weaker waveforms). 0 = light only. Same value as in Settings.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .disabled(!live)
        }
    }

    // MARK: - 3. Light

    private static let ledModes = ["off", "global", "individual", "breathe", "track_thermal"]

    private var lightCard: some View {
        Card(title: "Light", icon: "lightbulb") {
            ColorPicker("Color", selection: $color, supportsOpacity: false)
            HStack {
                Picker("Mode", selection: $ledMode) {
                    ForEach(Self.ledModes, id: \.self) { Text($0.replacingOccurrences(of: "_", with: " ")).tag($0) }
                }
                .pickerStyle(.menu)
                Spacer()
                Button("Apply") {
                    tapFeedback()
                    if stream.haptic(HapticLED(mode: ledMode, rgb: rgb(color)), label: "haptic.led") {
                        let shown: Color? = ledMode == "off" ? nil : color
                        ledColors = Array(repeating: shown, count: 8)
                    }
                }
                .buttonStyle(.borderedProminent)
            }
            Text("Tap an LED to set just that one to the color above:").font(.caption).foregroundStyle(.secondary)
            HStack {
                ForEach(0..<8, id: \.self) { i in
                    Button {
                        tapFeedback(.light)
                        if stream.haptic(HapticLED(mode: "individual", rgb: rgb(color), index: i), label: "haptic.led #\(i)") {
                            ledColors[i] = color
                        }
                    } label: {
                        Circle()
                            .fill(ledColors[i] ?? Color.gray.opacity(0.2))
                            .overlay(Circle().stroke(.secondary.opacity(0.5), lineWidth: 1))
                            .overlay(Text("\(i)").font(.caption2).foregroundStyle(.secondary))
                            .frame(maxWidth: .infinity).frame(height: 32)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("LED \(i)")
                }
            }
        }
        .disabled(!live)
    }

    // MARK: - 4. Vibration

    private var vibrationCard: some View {
        Card(title: "Vibration", icon: "waveform.path") {
            HStack {
                ForEach(["urgent", "pulse", "ambient"], id: \.self) { p in
                    Button {
                        tapFeedback(.heavy)
                        stream.haptic(HapticPreset(name: p), label: "haptic.preset \(p)")
                    } label: { Text(p.capitalized).frame(maxWidth: .infinity) }
                    .buttonStyle(.borderedProminent).tint(patternColor(p))
                }
            }
            Divider()
            Text("Sequence (8 slots)").font(.subheadline.bold())
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: 4), spacing: 6) {
                ForEach(0..<8, id: \.self) { i in slotMenu(i) }
            }
            HStack {
                Button("Clear") { slots = Array(repeating: .empty, count: 8) }
                Spacer()
                Button {
                    tapFeedback()
                    stream.haptic(HapticVibeSequence(steps: slots.compactMap(\.step)), label: "haptic.vibe.sequence")
                } label: { Label("Play", systemImage: "play.fill") }
                .buttonStyle(.borderedProminent)
                .disabled(slots.allSatisfy { $0.step == nil })
            }
            Divider()
            Text("Manual").font(.subheadline.bold())
            VStack(alignment: .leading, spacing: 2) {
                Text("Frequency \(Int(hz)) Hz").font(.subheadline)
                Slider(value: $hz, in: 50...300, step: 5) { editing in if !editing { resendManual() } }
                Text("Intensity \(Int((manualIntensity * 100).rounded()))%").font(.subheadline)
                Slider(value: $manualIntensity, in: 0...1, step: 0.05) { editing in if !editing { resendManual() } }
            }
            HStack {
                Button {
                    tapFeedback()
                    if stream.haptic(HapticVibeManual(hz: hz, intensity: manualIntensity, on: true),
                                     label: "haptic.vibe.manual on") { manualOn = true }
                } label: { Label("Start", systemImage: "play.circle").frame(maxWidth: .infinity) }
                .buttonStyle(.bordered)
                Button {
                    tapFeedback()
                    stream.haptic(HapticVibeManual(hz: hz, intensity: manualIntensity, on: false),
                                  label: "haptic.vibe.manual off")
                    manualOn = false
                } label: { Label("Stop", systemImage: "stop.circle").frame(maxWidth: .infinity) }
                .buttonStyle(.bordered)
            }
            if manualOn {
                Label("Vibrating at \(Int(hz)) Hz", systemImage: "waveform").font(.caption).foregroundStyle(.pink)
            }
        }
        .disabled(!live)
    }

    private func resendManual() {
        guard manualOn else { return }
        stream.haptic(HapticVibeManual(hz: hz, intensity: manualIntensity, on: true), label: "haptic.vibe.manual on")
    }

    private func slotMenu(_ i: Int) -> some View {
        Menu {
            Button("—") { slots[i] = .empty }
            Menu("Rest") {
                ForEach(Waveforms.rests, id: \.self) { s in
                    Button("Rest \(String(format: "%g", s)) s") { slots[i] = .rest(s) }
                }
            }
            ForEach(Waveforms.groups, id: \.name) { g in
                Menu(g.name) {
                    ForEach(g.waves, id: \.self) { w in Button(w) { slots[i] = .wave(w) } }
                }
            }
        } label: {
            VStack(spacing: 2) {
                Text("\(i + 1)").font(.caption2).foregroundStyle(.secondary)
                Text(slots[i].label)
                    .font(.caption2.bold()).lineLimit(2).minimumScaleFactor(0.7)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(slots[i].step == nil ? .secondary : .primary)
            }
            .frame(maxWidth: .infinity, minHeight: 48)
            .padding(4)
            .background(slots[i].tint.opacity(0.15), in: RoundedRectangle(cornerRadius: 8))
        }
    }

    // MARK: - 5. Thermal

    private var thermalCard: some View {
        Card(title: "Thermal", icon: "thermometer.medium") {
            let t = live ? stream.telemetry : nil
            let limits = stream.telemetry?.limits ?? ThermalLimits()
            VStack(alignment: .leading, spacing: 2) {
                Text("Intensity \(String(format: "%+.2f", thermalIntensity)) \(thermalIntensity > 0 ? "(heat)" : thermalIntensity < 0 ? "(cool)" : "")")
                    .font(.subheadline)
                Slider(value: $thermalIntensity, in: -1...1, step: 0.05)
            }
            HStack {
                Button {
                    tapFeedback()
                    stream.haptic(HapticThermal(on: true, intensity: thermalIntensity), label: "haptic.thermal")
                } label: { Text("Apply intensity").frame(maxWidth: .infinity) }
                .buttonStyle(.borderedProminent).tint(thermalIntensity >= 0 ? .orange : .blue)
                Button(role: .destructive) {
                    tapFeedback()
                    stream.haptic(HapticThermal(on: false), label: "haptic.thermal off")
                } label: { Text("Off").frame(maxWidth: .infinity) }
                .buttonStyle(.bordered)
            }
            HStack {
                Stepper("Target \(Int(targetC)) °C", value: $targetC, in: 25...targetMax, step: 1)
                Button("Hold target") {
                    tapFeedback()
                    stream.haptic(HapticThermal(on: true, targetC: targetC), label: "haptic.thermal")
                }
                .buttonStyle(.borderedProminent).tint(.orange)
            }
            Text("Cap \(Int(limits.targetCapC)) °C").font(.caption).foregroundStyle(.secondary)
            if let a = stream.lastThermalAck, a.capped == true {
                Label("Engine capped the target at \(a.value.map { String(format: "%.0f", $0) } ?? "40") °C",
                      systemImage: "exclamationmark.shield").font(.caption.bold()).foregroundStyle(.orange)
            }

            TimelineView(.periodic(from: .now, by: 1)) { ctx in
                TemperatureChart(points: live ? stream.telemetryHistory : MockData.telemetryHistory(now: ctx.date),
                                 now: live ? max(ctx.date, Date(timeIntervalSince1970: stream.telemetry?.ts ?? 0)) : ctx.date,
                                 target: t?.thermal?.mode == "target" ? t?.thermal?.value : nil)
            }
            .frame(height: 190)

            let shownT = t ?? (live ? nil : MockData.telemetry(at: Date().timeIntervalSince1970))
            HStack {
                Readout(label: "Skin", value: shownT?.skinC.map { String(format: "%.2f °C", $0) } ?? "—", color: .orange)
                Readout(label: "Sink", value: shownT?.sinkC.map { String(format: "%.2f °C", $0) } ?? "—", color: .blue)
                Readout(label: "Power", value: shownT?.thermalPower.map { String(format: "%.2f", $0) } ?? "—", color: .secondary)
            }
            sessionReadout(t?.thermal)
            Text("Safety (enforced by the engine): target capped at \(Int(limits.targetCapC)) °C · thermal off if skin > \(Int(limits.skinCutoffC)) °C · auto-off after \(Int(limits.thermalMaxS)) s · off ~5 s after you leave this tab or the phone disconnects.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .disabled(!live)
    }

    @ViewBuilder
    private func sessionReadout(_ th: ThermalSession?) -> some View {
        if let th {
            let what = th.mode == "target"
                ? "target \(th.value.map { String(format: "%.0f °C", $0) } ?? "?")"
                : "intensity \(th.value.map { String(format: "%+.2f", $0) } ?? "?")"
            VStack(alignment: .leading, spacing: 4) {
                Label("Session: \(what) · \(fmtS(th.elapsedS)) elapsed · auto-off in \(fmtS(th.remainingS))",
                      systemImage: "flame").font(.subheadline.bold()).foregroundStyle(.orange)
                let cooling = (th.mode == "manual" && (th.value ?? 0) < 0) || th.reached?.keys.contains { $0.hasPrefix("-") } == true
                let keys = cooling ? ["-2C", "-4C"] : ["+2C", "+4C"]
                let parts = keys.map { k in "\(k.dropLast()) °C: \(th.reached?[k].map { "\(fmtS($0))" } ?? "—")" }
                    + (th.mode == "target" ? ["target: \(th.reached?["target"].map { fmtS($0) } ?? "—")"] : [])
                Text("Time to " + parts.joined(separator: " · ")).font(.caption.monospacedDigit())
                Text("Rate \(th.rateCPerS.map { String(format: "%+.3f °C/s", $0) } ?? "—")").font(.caption.monospacedDigit())
            }
        } else {
            Label("Session: off", systemImage: "snowflake").font(.subheadline).foregroundStyle(.secondary)
        }
    }

    // MARK: - 6. Command log

    private var commandLogCard: some View {
        Card(title: "Command log", icon: "list.bullet.rectangle") {
            if stream.ackLog.isEmpty {
                Text("No commands yet. Every button above gets an ack from the engine with the device time.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ForEach(stream.ackLog) { a in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Image(systemName: a.ok ? "checkmark.circle.fill" : "xmark.octagon.fill")
                        .foregroundStyle(a.ok ? .green : .red).font(.caption)
                    Text(a.date.formatted(.dateTime.hour().minute().second()))
                        .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(a.kind).font(.caption.bold())
                        if let n = a.note, !n.isEmpty {
                            Text(n).font(.caption2).foregroundStyle(a.ok ? Color.secondary : Color.red).lineLimit(2)
                        }
                    }
                    Spacer()
                    Text(a.ms.map { "\(fmtMs($0)) ms" } ?? "").font(.caption.monospacedDigit())
                }
            }
        }
    }

    // MARK: - STOP ALL

    private var stopBar: some View {
        HStack(spacing: 10) {
        Button {
            tapFeedback(.medium)
            stream.command(.resetDot)
            manualOn = false
            ledColors = Array(repeating: nil, count: 8)
        } label: {
            Label("Reset Dot", systemImage: "arrow.counterclockwise")
                .font(.headline).frame(maxWidth: .infinity).padding(.vertical, 6)
        }
        .buttonStyle(.bordered)
        .accessibilityHint("Back to baseline: LED off, vibration stopped, thermal off")
        Button {
            tapFeedback(.heavy)
            stream.haptic(HapticStopAll(), label: "haptic.stop_all")
            manualOn = false
            ledColors = Array(repeating: nil, count: 8)
        } label: {
            Label("STOP ALL", systemImage: "stop.circle.fill")
                .font(.headline).foregroundStyle(.white)
                .frame(maxWidth: .infinity).padding(.vertical, 6)
        }
        .buttonStyle(.borderedProminent).tint(.red)
        }
        .padding(.horizontal).padding(.vertical, 8)
        .background(.bar)
        .accessibilityHint("Vibration, thermal and LEDs off")
    }
}

// MARK: - Link check

/// The three wireless hops, measured every 2 s while the tab is visible.
struct LinkCheckCard: View {
    let stream: EventStream

    var body: some View {
        Card(title: "Link check", icon: "point.3.connected.trianglepath.dotted") {
            TimelineView(.periodic(from: .now, by: 1)) { ctx in
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(hops(now: ctx.date), id: \.title) { HopRow(hop: $0) }
                }
            }
            Divider()
            HStack {
                Button {
                    tapFeedback(.medium)
                    stream.ping(flash: true)
                } label: { Label("Ping all", systemImage: "dot.radiowaves.forward") }
                .buttonStyle(.borderedProminent).tint(.green)
                .disabled(!stream.isLive)
                Spacer()
                Text("Dot flashes green 0.5 s").font(.caption).foregroundStyle(.secondary)
            }
            if let p = stream.lastFullPing {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Total \(fmtMs(p.rttMs)) ms · \(p.date.formatted(.dateTime.hour().minute().second()))")
                        .font(.subheadline.bold().monospacedDigit())
                    Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 2) {
                        GridRow { Text("App ↔ Mac (Wi-Fi)"); Text("\(fmtMs(p.appMacMs)) ms") }
                        GridRow { Text("Mac ↔ El Jefe ↔ Dot (BLE)"); Text(p.bleMs.map { "\(fmtMs($0)) ms" } ?? "—") }
                        GridRow { Text("LED flash"); Text(p.flashMs.map { "\(fmtMs($0)) ms" } ?? "— (no Dot answered)") }
                    }
                    .font(.caption.monospacedDigit())
                }
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.green.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            }
        }
    }

    struct Hop {
        enum Level { case ok, warn, bad, unknown }
        let level: Level
        let title: String
        let detail: String
        let reason: String
    }

    func hops(now: Date) -> [Hop] {
        let nowS = now.timeIntervalSince1970
        // (1) Phone -> Mac
        let h1: Hop
        let rc = "reconnects \(stream.reconnects)"
        switch stream.status {
        case .disconnected:
            h1 = Hop(level: .bad, title: "Phone → Mac", detail: "WebSocket offline · \(rc)",
                     reason: "Wi-Fi: can't reach the engine" + (stream.lastError.map { " (\($0))" } ?? "")
                        + ". Same network? Engine running? URL in Settings?")
        case .connecting:
            h1 = Hop(level: .warn, title: "Phone → Mac", detail: "Connecting… · \(rc)", reason: "Opening the WebSocket")
        case .connected:
            if let p = stream.lastPing {
                let age = now.timeIntervalSince(p.date)
                let detail = "WebSocket live · RTT \(fmtMs(p.rttMs)) ms (app↔Mac \(fmtMs(p.appMacMs)) ms) · \(rc)"
                if age > 6 {
                    h1 = Hop(level: .warn, title: "Phone → Mac", detail: detail,
                             reason: "Stale: no ping reply for \(Int(age)) s. Engine busy or Wi-Fi stalling?")
                } else if p.appMacMs > 300 {
                    h1 = Hop(level: .warn, title: "Phone → Mac", detail: detail,
                             reason: "Slow: app↔Mac \(fmtMs(p.appMacMs)) ms (> 300 ms). Wi-Fi congested?")
                } else {
                    h1 = Hop(level: .ok, title: "Phone → Mac", detail: detail, reason: "ok")
                }
            } else {
                h1 = Hop(level: .warn, title: "Phone → Mac", detail: "WebSocket live · \(rc)",
                         reason: "Waiting for the first ping reply…")
            }
        }

        guard stream.isLive else {
            return [h1,
                    Hop(level: .unknown, title: "Mac → El Jefe", detail: "—", reason: "Unknown: the phone can't reach the Mac"),
                    Hop(level: .unknown, title: "El Jefe → Dot", detail: "—", reason: "Unknown: the phone can't reach the Mac")]
        }
        guard let l = stream.link, l.transport != nil else {
            let why = "No link info from the engine yet (older engine or no haptics sink?)"
            return [h1, Hop(level: .unknown, title: "Mac → El Jefe", detail: "—", reason: why),
                    Hop(level: .unknown, title: "El Jefe → Dot", detail: "—", reason: why)]
        }

        // (2) Mac -> El Jefe
        let transport = (l.transport ?? "?").uppercased()
        let device = l.device ?? "El Jefe"
        var parts = ["\(transport) · \(device)"]
        if let r = l.rssi { parts.append("rssi \(Int(r)) dBm") }
        let seenAge = l.lastSeen.map { max(0, nowS - $0) }
        parts.append(seenAge.map { "seen \(Int($0)) s ago" } ?? "never seen")
        if let ms = l.ms { parts.append("read \(fmtMs(ms)) ms") }
        let serverReason = (l.reason?.isEmpty == false && l.reason != "ok") ? l.reason : nil
        let h2: Hop
        if l.ok == false || l.linkUp == false {
            h2 = Hop(level: .bad, title: "Mac → El Jefe", detail: parts.joined(separator: " · "),
                     reason: serverReason ?? "\(transport): no response from \(device) — is El Jefe on?")
        } else if l.ok == nil {
            h2 = Hop(level: .warn, title: "Mac → El Jefe", detail: parts.joined(separator: " · "),
                     reason: serverReason ?? "Not checked yet")
        } else if let a = seenAge, a > 6 {
            h2 = Hop(level: .warn, title: "Mac → El Jefe", detail: parts.joined(separator: " · "),
                     reason: "Stale: last answer \(Int(a)) s ago")
        } else if let ms = l.ms, ms > 400 {
            h2 = Hop(level: .warn, title: "Mac → El Jefe", detail: parts.joined(separator: " · "),
                     reason: "Slow: register read \(fmtMs(ms)) ms (> 400 ms). Move El Jefe closer to the Mac?")
        } else {
            h2 = Hop(level: .ok, title: "Mac → El Jefe", detail: parts.joined(separator: " · "), reason: "ok")
        }

        // (3) El Jefe -> Dot
        let expected = l.addresses ?? [], answering = l.dots ?? []
        let missing = expected.filter { !answering.contains($0) }
        let d3 = "\(answering.count)/\(expected.count) answering · Dot address \(list(answering)) (expected \(list(expected)))"
        let h3: Hop
        if expected.isEmpty {
            h3 = Hop(level: .bad, title: "El Jefe → Dot", detail: d3, reason: "No Dots were found when the engine started")
        } else if answering.isEmpty {
            h3 = Hop(level: .bad, title: "El Jefe → Dot", detail: d3,
                     reason: h2.level == .bad ? "Unknown: El Jefe isn't answering" : "No Dot answers. Is the Dot on and charged?")
        } else if !missing.isEmpty {
            h3 = Hop(level: .warn, title: "El Jefe → Dot", detail: d3, reason: "Missing Dot(s) \(list(missing))")
        } else {
            h3 = Hop(level: h2.level == .ok ? .ok : h2.level, title: "El Jefe → Dot", detail: d3, reason: "ok")
        }
        return [h1, h2, h3]
    }

    private func list(_ a: [Int]) -> String { a.isEmpty ? "none" : a.map(String.init).joined(separator: ", ") }
}

struct HopRow: View {
    let hop: LinkCheckCard.Hop
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(color).frame(width: 12, height: 12).padding(.top, 4)
                .shadow(color: color.opacity(0.6), radius: hop.level == .ok ? 3 : 0)
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(hop.title).font(.subheadline.bold())
                    Spacer()
                    Text(label).font(.caption.bold()).foregroundStyle(color)
                }
                Text(hop.detail).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                if hop.reason != "ok" {
                    Text(hop.reason).font(.caption).foregroundStyle(hop.level == .bad ? .red : hop.level == .warn ? .orange : .secondary)
                }
            }
        }
        .accessibilityElement(children: .combine)
    }
    private var color: Color {
        switch hop.level { case .ok: .green; case .warn: .orange; case .bad: .red; case .unknown: .gray }
    }
    private var label: String {
        switch hop.level { case .ok: "OK"; case .warn: "CHECK"; case .bad: "DOWN"; case .unknown: "UNKNOWN" }
    }
}

// MARK: - Pieces

struct TemperatureChart: View {
    let points: [HapticTelemetry]
    let now: Date
    let target: Double?

    var body: some View {
        Chart {
            ForEach(points, id: \.ts) { p in
                if let s = p.skinC {
                    LineMark(x: .value("Time", Date(timeIntervalSince1970: p.ts)), y: .value("°C", s),
                             series: .value("Sensor", "Skin"))
                        .foregroundStyle(by: .value("Sensor", "Skin"))
                }
                if let s = p.sinkC {
                    LineMark(x: .value("Time", Date(timeIntervalSince1970: p.ts)), y: .value("°C", s),
                             series: .value("Sensor", "Sink"))
                        .foregroundStyle(by: .value("Sensor", "Sink"))
                }
            }
            if let target {
                RuleMark(y: .value("Target", target))
                    .foregroundStyle(.red.opacity(0.6))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
                    .annotation(position: .top, alignment: .leading) {
                        Text("target \(Int(target)) °C").font(.caption2).foregroundStyle(.red)
                    }
            }
        }
        .chartForegroundStyleScale(["Skin": Color.orange, "Sink": Color.blue])
        .chartXScale(domain: now.addingTimeInterval(-120)...now)
        .chartYScale(domain: .automatic(includesZero: false))
        .chartYAxisLabel("°C")
        .chartXAxis {
            AxisMarks(values: .stride(by: .second, count: 30)) { v in
                AxisGridLine()
                AxisValueLabel {
                    if let d = v.as(Date.self) { Text("-\(Int(now.timeIntervalSince(d).rounded())) s") }
                }
            }
        }
        .overlay {
            if points.isEmpty {
                Text("Waiting for telemetry…").font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

struct Readout: View {
    let label: String
    let value: String
    let color: Color
    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.subheadline.bold().monospacedDigit()).foregroundStyle(color)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct Card<Content: View>: View {
    let title: String
    let icon: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: icon).font(.headline)
            content
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 14))
    }
}

/// One slot of the vibration sequence builder.
enum SeqSlot: Hashable {
    case empty, rest(Double), wave(String)

    var step: VibeStep? {
        switch self {
        case .empty: nil
        case .rest(let s): .rest(s)
        case .wave(let w): .wave(w)
        }
    }
    var label: String {
        switch self {
        case .empty: "—"
        case .rest(let s): "rest \(String(format: "%g", s)) s"
        case .wave(let w): Waveforms.short(w)
        }
    }
    var tint: Color {
        switch self { case .empty: .gray; case .rest: .teal; case .wave: .pink }
    }
}

// MARK: - Helpers

/// Phone-side tap feedback for haptics-tab buttons.
func tapFeedback(_ style: UIImpactFeedbackGenerator.FeedbackStyle = .medium) {
    UIImpactFeedbackGenerator(style: style).impactOccurred()
}

func fmtMs(_ ms: Double) -> String { ms < 10 ? String(format: "%.1f", ms) : String(format: "%.0f", ms) }

func fmtS(_ s: Double?) -> String { s.map { String(format: "%.1f s", $0) } ?? "—" }

/// 0-255 RGB from a SwiftUI color (clamped; extended-sRGB colors can leave 0...1).
func rgb(_ c: Color) -> [Int] {
    var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
    UIColor(c).getRed(&r, green: &g, blue: &b, alpha: &a)
    return [r, g, b].map { Int((min(1, max(0, $0)) * 255).rounded()) }
}
