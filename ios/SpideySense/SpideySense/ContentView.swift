import SwiftUI

/// Root: four tabs sharing one EventStream (one WebSocket to the Mac engine).
struct ContentView: View {
    @AppStorage("serverURL") private var serverURL = "ws://192.168.1.10:8765"
    @State private var stream = EventStream()
    @SceneStorage("tab") private var tab = "log"

    var body: some View {
        TabView(selection: $tab) {
            LogView(stream: stream, serverURL: serverURL)
                .tabItem { Label("Log", systemImage: "waveform") }.tag("log")
            SensesView(stream: stream)
                .tabItem { Label("Senses", systemImage: "dot.radiowaves.left.and.right") }.tag("senses")
            DevicesView(stream: stream)
                .tabItem { Label("Devices", systemImage: "sensor") }.tag("devices")
            SettingsView(serverURL: $serverURL, stream: stream)
                .tabItem { Label("Settings", systemImage: "gearshape") }.tag("settings")
        }
        .onAppear {
            // Screenshot/demo hook: `-tab senses` launch argument picks the start tab.
            if let t = UserDefaults.standard.string(forKey: "tab") { tab = t }
            stream.connect(to: serverURL)
        }
        .onChange(of: serverURL) { _, new in stream.connect(to: new) }
    }
}

// MARK: - Log tab

struct LogView: View {
    let stream: EventStream
    let serverURL: String
    @State private var selected: HapticEvent?
    @State private var senseFilter: String?   // nil = all senses

    private var showingSample: Bool { stream.events.isEmpty && !stream.isLive }
    private var source: [HapticEvent] { showingSample ? MockData.events : stream.events }
    private var shown: [HapticEvent] {
        guard let f = senseFilter else { return source }
        return source.filter { $0.rule == f }
    }
    private var senseNames: [String] {
        Array(Set(source.map(\.rule) + stream.rules.map(\.name)).filter { !$0.isEmpty }).sorted()
    }

    var body: some View {
        NavigationStack {
            List {
                if showingSample {
                    SampleBanner(text: "Sample data: not connected to the Mac engine. Set the server in Settings.")
                        .listRowSeparator(.hidden)
                }
                ForEach(shown, id: \.key) { e in
                    Button { selected = e } label: { EventRow(event: e, verdict: stream.verdicts[e.key]) }
                        .buttonStyle(.plain)
                        .swipeActions(edge: .leading) {
                            Button { stream.feedback(e, .useful) } label: { Label("Useful", systemImage: "hand.thumbsup") }
                                .tint(.green)
                        }
                        .swipeActions(edge: .trailing) {
                            Button { stream.feedback(e, .noisy) } label: { Label("Noisy", systemImage: "speaker.slash") }
                                .tint(.orange)
                        }
                }
            }
            .listStyle(.plain)
            .overlay {
                if shown.isEmpty && !showingSample {
                    ContentUnavailableView(senseFilter == nil ? "Waiting for events" : "No \(senseFilter!) events",
                                           systemImage: "waveform", description: Text(serverURL))
                }
            }
            .navigationTitle("Spidey Sense")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { StatusDot(status: stream.status) }
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Picker("Sense", selection: $senseFilter) {
                            Text("All senses").tag(String?.none)
                            ForEach(senseNames, id: \.self) { Text($0).tag(Optional($0)) }
                        }
                    } label: {
                        Label(senseFilter ?? "All senses",
                              systemImage: senseFilter == nil ? "line.3.horizontal.decrease.circle"
                                                              : "line.3.horizontal.decrease.circle.fill")
                            .labelStyle(.titleAndIcon)
                    }
                }
            }
            .sheet(item: $selected) { e in EventDetail(event: e) }
        }
    }
}

struct SampleBanner: View {
    let text: String
    var body: some View {
        Label(text, systemImage: "theatermasks")
            .font(.footnote)
            .foregroundStyle(.orange)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
    }
}

struct StatusDot: View {
    let status: EventStream.Status
    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 10, height: 10)
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
    }
    private var color: Color {
        switch status { case .connected: .green; case .connecting: .orange; case .disconnected: .red }
    }
    private var label: String {
        switch status { case .connected: "live"; case .connecting: "connecting"; case .disconnected: "offline" }
    }
}

func patternColor(_ p: String) -> Color {
    switch p { case "urgent": .red; case "pulse": .pink; case "ambient": .blue; default: .gray }
}

struct EventRow: View {
    let event: HapticEvent
    var verdict: Verdict? = nil
    private static let fmt: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm:ss"; return f
    }()

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(Self.fmt.string(from: event.date)).font(.system(.subheadline, design: .monospaced))
                Text(event.pattern.uppercased())
                    .font(.caption.bold()).foregroundStyle(.white)
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(patternColor(event.pattern), in: Capsule())
                Text(subject).font(.subheadline.bold())
                Spacer()
                if event.quiet {
                    Image(systemName: "moon.fill").font(.caption).foregroundStyle(.indigo)
                        .accessibilityLabel("quiet: not felt")
                }
                if let verdict {
                    Image(systemName: verdict == .useful ? "hand.thumbsup.fill" : "speaker.slash.fill")
                        .font(.caption).foregroundStyle(verdict == .useful ? .green : .orange)
                }
                if let u = event.urgency {
                    Text(String(format: "%.2f", u)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
            Text(event.reason.isEmpty ? event.rule : event.reason)
                .font(.callout).foregroundStyle(.primary)
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }

    private var subject: String {
        [event.hazard, event.direction].compactMap { $0 }.joined(separator: " · ")
    }
}

struct EventDetail: View {
    let event: HapticEvent
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            ScrollView {
                Text(event.prettyJSON)
                    .font(.system(.footnote, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding()
            }
            .navigationTitle("Event #\(event.id)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("Done") { dismiss() } }
        }
    }
}
