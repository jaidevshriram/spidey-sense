import SwiftUI

/// Root: five tabs sharing one EventStream (one WebSocket to the Mac engine).
struct ContentView: View {
    /// "" = automatic (see ServerConfig), else a custom URL typed in Settings.
    @AppStorage("serverURL") private var serverURL = ""
    @State private var stream = EventStream()
    @State private var resolver = ServerResolver()
    @SceneStorage("tab") private var tab = "log"

    private var effective: (url: String, source: ServerSource) { resolver.effective(stored: serverURL) }

    var body: some View {
        TabView(selection: $tab) {
            LogView(stream: stream, serverURL: effective.url)
                .tabItem { Label("Log", systemImage: "waveform") }.tag("log")
            SensesView(stream: stream)
                .tabItem { Label("Senses", systemImage: "dot.radiowaves.left.and.right") }.tag("senses")
            DevicesView(stream: stream)
                .tabItem { Label("Devices", systemImage: "sensor") }.tag("devices")
            HapticsView(stream: stream)
                .tabItem { Label("Haptics", systemImage: "hand.raised.fingers.spread") }.tag("haptics")
            SettingsView(serverURL: $serverURL, stream: stream, effectiveURL: effective.url, source: effective.source)
                .tabItem { Label("Settings", systemImage: "gearshape") }.tag("settings")
        }
        .onAppear {
            // Screenshot/demo hook: `-tab senses|devices|haptics|settings` launch argument picks the start tab.
            if let t = UserDefaults.standard.string(forKey: "tab") { tab = t }
        }
        .task {
            ServerConfig.migrateStoredURL()
            // Give Bonjour ~2 s to find the engine before falling back (build-time Mac IP / 127.0.0.1).
            if ServerConfig.isAutomatic(serverURL) { await resolver.start() }
            stream.resolveURL = { [resolver] in
                // After a disconnect: browse again, then use whatever is current (custom URL wins).
                let stored = UserDefaults.standard.string(forKey: "serverURL") ?? ""
                if ServerConfig.isAutomatic(stored) { await resolver.rebrowse() }
                return resolver.effective(stored: stored).url
            }
            stream.connect(to: effective.url)
        }
        .onChange(of: serverURL) { _, _ in stream.connect(to: effective.url) }   // Settings: custom / reset
        .onChange(of: effective.url) { _, new in
            // A Bonjour answer arriving later only switches over if we aren't already live.
            guard resolver.settled, !stream.isLive, new != stream.urlString else { return }
            stream.connect(to: new)
        }
    }
}

// MARK: - Log tab

struct LogView: View {
    let stream: EventStream
    let serverURL: String
    @State private var selected: HapticEvent?
    @State private var senseFilter: String?   // trigger id; nil = all senses
    @State private var unreachable = false     // offline for more than 3 s

    private var showingSample: Bool { stream.events.isEmpty && !stream.isLive }
    private var source: [HapticEvent] { showingSample ? MockData.events : stream.events }
    private var shown: [HapticEvent] {
        guard let f = senseFilter else { return source }
        return source.filter { $0.triggerID == f }
    }
    /// Events plus system rows (engine `system` messages, local WebSocket drops), newest first.
    /// A sense filter shows only that sense's events.
    private var items: [LogItem] {
        let ev = shown.map(LogItem.event)
        guard senseFilter == nil else { return ev }
        return (ev + stream.systemRows.map(LogItem.system)).sorted { $0.ts > $1.ts }
    }
    /// (trigger id, display name) for the filter menu: the engine's triggers plus any ids seen in the log.
    private var senseOptions: [(id: String, name: String)] {
        var names: [String: String] = [:]
        for e in source where !e.rule.isEmpty || e.trigger != nil { names[e.triggerID] = names[e.triggerID] ?? e.triggerLabel }
        for r in stream.rules { names[r.id] = r.name }
        return names.map { (id: $0.key, name: $0.value) }.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }
    private var filterName: String? {
        senseFilter.map { f in senseOptions.first { $0.id == f }?.name ?? f }
    }

    var body: some View {
        NavigationStack {
            List {
                if unreachable {
                    UnreachableBanner(url: stream.urlString ?? serverURL)
                        .listRowSeparator(.hidden)
                }
                if showingSample {
                    SampleBanner(text: "Sample data: not connected to the Mac engine. Set the server in Settings.")
                        .listRowSeparator(.hidden)
                }
                ForEach(items) { item in
                    switch item {
                    case .system(let r):
                        SystemRowView(row: r)   // no swipe feedback: not a haptic event
                    case .event(let e):
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
            }
            .listStyle(.plain)
            .overlay {
                if items.isEmpty && !showingSample {
                    ContentUnavailableView(senseFilter == nil ? "Waiting for events" : "No \(filterName ?? "") events",
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
                            ForEach(senseOptions, id: \.id) { Text($0.name).tag(Optional($0.id)) }
                        }
                    } label: {
                        Label(filterName ?? "All senses",
                              systemImage: senseFilter == nil ? "line.3.horizontal.decrease.circle"
                                                              : "line.3.horizontal.decrease.circle.fill")
                            .labelStyle(.titleAndIcon)
                    }
                }
            }
            .sheet(item: $selected) { e in EventDetail(event: e) }
            .task(id: stream.isLive) {
                if stream.isLive { unreachable = false; return }
                try? await Task.sleep(for: .seconds(3))
                if !Task.isCancelled { unreachable = !stream.isLive }
            }
        }
    }
}

enum LogItem: Identifiable {
    case event(HapticEvent), system(SystemRow)
    var id: String {
        switch self { case .event(let e): "e\(e.key)"; case .system(let r): "s\(r.id)" }
    }
    var ts: Double {
        switch self { case .event(let e): e.ts; case .system(let r): r.ts }
    }
}

/// Gray (info) / amber (warning) / red (error) row for link drops, reconnects and safety cut-offs.
struct SystemRowView: View {
    let row: SystemRow
    private static let fmt: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm:ss"; return f
    }()

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: icon).foregroundStyle(color)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(Self.fmt.string(from: row.date)).font(.system(.subheadline, design: .monospaced))
                    Text("SYSTEM").font(.caption.bold()).foregroundStyle(color)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(color.opacity(0.15), in: Capsule())
                    Text(row.local ? "this phone" : "engine").font(.caption).foregroundStyle(.secondary)
                }
                Text(row.text).font(.callout).foregroundStyle(row.level == .info ? .secondary : .primary)
            }
        }
        .padding(.vertical, 4)
        .listRowBackground(color.opacity(row.level == .info ? 0.04 : 0.08))
    }
    private var color: Color {
        switch row.level { case .info: .gray; case .warning: .orange; case .error: .red }
    }
    private var icon: String {
        switch row.level {
        case .info: "info.circle.fill"; case .warning: "exclamationmark.triangle.fill"; case .error: "xmark.octagon.fill"
        }
    }
}

/// Shown on the Log tab when the engine has been unreachable for more than 3 s.
struct UnreachableBanner: View {
    let url: String
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Can't reach the Spidey engine at \(url).", systemImage: "wifi.exclamationmark")
                .font(.footnote.bold()).foregroundStyle(.red)
            Text("On the Mac run: cd ~/Desktop/GIT-Projects/spidey-sense && source .venv/bin/activate && python3 backend/run.py")
                .font(.system(.caption, design: .monospaced))
                .textSelection(.enabled)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
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
            // Haiku's one-line reason when the engine sends it; the engine's reason string stays in the detail JSON.
            Text(event.why ?? (event.reason.isEmpty ? event.rule : event.reason))
                .font(.callout).foregroundStyle(.primary)
            if !event.triggerLabel.isEmpty {
                Label(event.triggerLabel, systemImage: "bolt.fill")
                    .font(.caption2.bold()).foregroundStyle(.secondary)
                    .labelStyle(.titleAndIcon)
                    .accessibilityLabel("Trigger \(event.triggerLabel)")
            }
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
