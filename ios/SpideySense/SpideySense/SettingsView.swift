import SwiftUI

/// Consumer Settings: connection in plain words, vibration strength, quiet mode, a test buzz, stop,
/// and the "Show Debug tab" switch. Everything technical lives in the Debug tab.
struct SettingsView: View {
    let stream: EventStream
    @AppStorage("showDebug") private var showDebug = true
    @State private var intensity = 1.0
    @State private var stopped = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Text("Settings").font(.spLargeTitle).foregroundStyle(Theme.ink)
                        .padding(.top, 12)
                        .accessibilityAddTraits(.isHeader)

                    connectionCard

                    VStack(alignment: .leading, spacing: 16) {
                        Text("Buzz strength").font(.spHeadline).foregroundStyle(Theme.ink)
                        Slider(value: $intensity, in: 0...1, step: 0.05) { editing in
                            if !editing { stream.command(.setIntensity(intensity)) }
                        }
                        .tint(Theme.accent)
                        .disabled(!stream.isLive)
                        .accessibilityValue("\(Int(intensity * 100)) percent")
                        HStack { Text("Light only"); Spacer(); Text("Strongest") }
                            .font(.subheadline).foregroundStyle(Theme.inkSecondary)
                        Divider()
                        Button {
                            stream.command(.testPattern("pulse")); PhoneHaptics.play("pulse"); softHaptic()
                        } label: {
                            Label("Send a test buzz", systemImage: "hand.tap.fill").font(.body.weight(.semibold))
                                .foregroundStyle(stream.isLive ? Theme.accent : Theme.inkTertiary)
                        }
                        .disabled(!stream.isLive)
                    }
                    .softCard()

                    VStack(spacing: 0) {
                        Toggle(isOn: Binding(get: { stream.settings.quiet },
                                             set: { on in withSpring { stream.command(.quiet(on)) } })) {
                            row("moon.fill", "Quiet mode", "Spidey keeps watching and logging, but won't buzz.")
                        }
                        .tint(Theme.accent).disabled(!stream.isLive)
                        .padding(.vertical, 6)
                    }
                    .softCard(padding: 18)

                    Button {
                        stream.command(.stopAll); UINotificationFeedbackGenerator().notificationOccurred(.warning)
                        withSpring { stopped = true }
                        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { withSpring { stopped = false } }
                    } label: {
                        Label(stopped ? (stream.isLive ? "Stopped" : "Not connected") : "Stop all buzzing now",
                              systemImage: stopped ? "checkmark" : "stop.fill")
                    }
                    .buttonStyle(PrimaryButtonStyle(color: Theme.ink))

                    VStack(spacing: 0) {
                        Toggle(isOn: $showDebug.animation(Theme.motion(reduceMotion))) {
                            row("wrench.and.screwdriver.fill", "Show Debug tab",
                                "Devices, link checks, live decisions, the raw log and engine details.")
                        }
                        .tint(Theme.accent)
                        .padding(.vertical, 6)
                    }
                    .softCard(padding: 18)

                    VStack(spacing: 4) {
                        Text(versionText)
                        Text("Headings: Plus Jakarta Sans (SIL OFL 1.1, Tokotype)")
                    }
                    .font(.footnote).foregroundStyle(Theme.inkTertiary)
                    .frame(maxWidth: .infinity)
                }
                .padding(.horizontal, 20).padding(.bottom, 32)
            }
            .scrollIndicators(.hidden)
            .background(Theme.background.ignoresSafeArea())
            .toolbar(.hidden, for: .navigationBar)
            .statusBarBackdrop()
            .onAppear { intensity = stream.settings.intensity }
            .onChange(of: stream.settings.intensity) { _, v in intensity = v }
        }
    }

    private var connectionCard: some View {
        HStack(spacing: 16) {
            ZStack {
                Circle().fill(connColor.opacity(0.12)).frame(width: 52, height: 52)
                Image(systemName: stream.isLive ? "checkmark.circle.fill" : "wifi.slash")
                    .font(.system(size: 24, weight: .semibold)).foregroundStyle(connColor)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(connTitle).font(.spHeadline).foregroundStyle(Theme.ink)
                Text(connDetail).font(.subheadline).foregroundStyle(Theme.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .softCard()
        .accessibilityElement(children: .combine)
    }

    private var connColor: Color {
        switch stream.status { case .connected: Theme.good; case .connecting: Theme.warn; case .disconnected: Theme.inkTertiary }
    }
    private var connTitle: String {
        switch stream.status {
        case .connected: "Connected to your Spidey hub"
        case .connecting: "Connecting…"
        case .disconnected: "Not connected"
        }
    }
    private var connDetail: String {
        switch stream.status {
        case .connected:
            stream.paused ? "Paused from the Debug tab: nothing will buzz." : "Your glasses and Dot are being looked after."
        case .connecting: "Looking for your Spidey hub on this Wi-Fi."
        case .disconnected: "Make sure your phone and the Mac are on the same Wi-Fi."
        }
    }

    private func row(_ icon: String, _ title: String, _ detail: String) -> some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: icon).font(.title3).foregroundStyle(Theme.inkSecondary).frame(width: 28)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.spHeadline).foregroundStyle(Theme.ink)
                Text(detail).font(.subheadline).foregroundStyle(Theme.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var versionText: String {
        let i = Bundle.main.infoDictionary
        return "Spidey Sense \(i?["CFBundleShortVersionString"] as? String ?? "?") (\(i?["CFBundleVersion"] as? String ?? "?"))"
    }
}

// MARK: - Debug tab

/// One hub for everything technical: devices, haptics / link check, pause, live decisions, the raw log,
/// the technical trigger editor and engine / connection details. Each is the original screen, pushed.
struct DebugView: View {
    let stream: EventStream
    @Binding var serverURL: String
    let effectiveURL: String
    let source: ServerSource
    @State private var path: [String] = []

    var body: some View {
        NavigationStack(path: $path) {
            List {
                Section {
                    HStack {
                        StatusDot(status: stream.status)
                        Spacer()
                        Text(stream.urlString ?? effectiveURL).font(.caption.monospaced()).foregroundStyle(.secondary)
                            .lineLimit(1).truncationMode(.middle)
                    }
                }
                Section {
                    Toggle(isOn: Binding(get: { stream.paused }, set: { stream.command(.pause($0)) })) {
                        Label("Pause detection", systemImage: stream.paused ? "pause.circle.fill" : "pause.circle")
                    }
                    .tint(.orange)
                    .disabled(!stream.isLive)
                } footer: {
                    Text("While paused, detection keeps running for the log, but nothing reaches the Dot.")
                }
                Section("Hardware") {
                    NavigationLink(value: "devices") {
                        Label("Devices", systemImage: "sensor")
                    }
                    NavigationLink(value: "haptics") {
                        Label("Haptics & link check", systemImage: "hand.raised.fingers.spread")
                    }
                }
                Section("Detection") {
                    NavigationLink(value: "live") {
                        Label("Live decisions", systemImage: "brain")
                    }
                    NavigationLink(value: "log") {
                        Label("Raw event log", systemImage: "waveform")
                    }
                    NavigationLink(value: "senses") {
                        Label("Triggers (technical editor)", systemImage: "dot.radiowaves.left.and.right")
                    }
                }
                Section("Engine") {
                    NavigationLink(value: "engine") {
                        Label("Engine, connection & about", systemImage: "desktopcomputer")
                    }
                }
            }
            .navigationTitle("Debug")
            .navigationDestination(for: String.self) { dest in
                switch dest {
                case "devices": DevicesView(stream: stream, embedded: true)
                case "haptics": HapticsView(stream: stream, embedded: true)
                case "live": LogView(stream: stream, serverURL: effectiveURL, embedded: true, startLive: true)
                case "senses": SensesView(stream: stream, embedded: true)
                case "engine": EngineSettingsView(serverURL: $serverURL, embedded: true, stream: stream,
                                                  effectiveURL: effectiveURL, source: source)
                default: LogView(stream: stream, serverURL: effectiveURL, embedded: true)
                }
            }
            .onAppear {
                // Screenshot hook: `-debugOpen devices|haptics|live|log|senses|engine` pushes that screen.
                if path.isEmpty, let d = UserDefaults.standard.string(forKey: "debugOpen") { path = [d] }
            }
        }
    }
}
