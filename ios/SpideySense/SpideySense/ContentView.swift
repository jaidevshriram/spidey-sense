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
    @State private var liveDecisions = false   // stream `trace` (one per Haiku call) while on
    @State private var selectedTrace: Trace?

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
                LiveDecisionsRows(stream: stream, on: $liveDecisions, selected: $selectedTrace)
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
            .engineHeader(stream)
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
            .background { Color.clear.sheet(item: $selectedTrace) { t in TraceDetail(trace: t) } }
            // Live decisions: the engine only streams while it heard trace.subscribe in the last 5 s.
            .task(id: "\(liveDecisions)-\(stream.isLive)") {
                guard liveDecisions, stream.isLive else { return }
                while !Task.isCancelled {
                    stream.subscribeTrace(true)
                    try? await Task.sleep(for: .seconds(2))
                }
            }
            .onChange(of: liveDecisions) { _, on in if !on { stream.subscribeTrace(false) } }
            .onDisappear { if liveDecisions { stream.subscribeTrace(false) } }
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
        HStack(alignment: .top, spacing: 10) {
            EventThumb(event: event, size: 56)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(Self.fmt.string(from: event.date)).font(.system(.subheadline, design: .monospaced))
                    Text(event.pattern.uppercased())
                        .font(.caption.bold()).foregroundStyle(.white)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(patternColor(event.pattern), in: Capsule())
                    if event.paused { Tag(text: "paused", color: .orange) }
                    Spacer(minLength: 0)
                    if event.quiet {
                        Image(systemName: "moon.fill").font(.caption).foregroundStyle(.indigo)
                            .accessibilityLabel("quiet: not felt")
                    }
                    if let verdict {
                        Image(systemName: verdict == .useful ? "hand.thumbsup.fill" : "speaker.slash.fill")
                            .font(.caption).foregroundStyle(verdict == .useful ? .green : .orange)
                    }
                    if let ms = event.latencyMs {
                        Text("\(Int(ms.rounded())) ms").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                }
                HStack(spacing: 4) {
                    Image(systemName: sensorIcon).font(.caption).foregroundStyle(.secondary)
                        .accessibilityLabel(event.isRadar ? "radar" : event.isHeart ? "heart rate" : "camera")
                    Text(event.triggerLabel.isEmpty ? subject : event.triggerLabel)
                        .font(.subheadline.bold()).lineLimit(1)
                }
                // Haiku's one-line reason when the engine sends it; the engine's reason string stays in the detail JSON.
                Text(event.why ?? (event.reason.isEmpty ? event.rule : event.reason))
                    .font(.callout).foregroundStyle(.primary)
                if let r = event.radar, !r.summary.isEmpty {
                    Label(r.summary, systemImage: "dot.radiowaves.right")
                        .font(.caption.monospacedDigit()).foregroundStyle(.teal)
                }
                if let h = event.heart {
                    Label(h.summary, systemImage: "heart.fill")
                        .font(.caption.monospacedDigit()).foregroundStyle(.pink)
                }
                if let t = event.trend {
                    HStack(spacing: 6) {
                        if !t.summary.isEmpty {
                            Label(t.summary, systemImage: "timer").font(.caption.monospacedDigit()).foregroundStyle(.purple)
                        }
                        if t.touching { Tag(text: "touching", color: .red) }
                    }
                }
                if !subject.isEmpty && !event.triggerLabel.isEmpty && !event.isRadar && !event.isHeart {
                    Text(subject).font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }

    private var sensorIcon: String {
        event.isRadar ? "dot.radiowaves.right" : event.isHeart ? "heart.fill"
            : (event.sensor == "camera" || event.thumb != nil ? "camera.fill" : "bolt.fill")
    }

    private var subject: String {
        var parts = [event.hazard, event.direction].compactMap { $0 }.filter { !$0.isEmpty }
        if let s = event.lookedAtSAgo { parts.append(String(format: "looked %.1f s ago", s)) }
        return parts.joined(separator: " · ")
    }
}

struct Tag: View {
    let text: String
    let color: Color
    var body: some View {
        Text(text).font(.caption2.bold()).foregroundStyle(color)
            .padding(.horizontal, 5).padding(.vertical, 1)
            .background(color.opacity(0.15), in: Capsule())
    }
}

/// The frame that fired (camera) or a sensor tile (radar / no thumbnail).
struct EventThumb: View {
    let event: HapticEvent
    let size: CGFloat
    var body: some View {
        Group {
            if let img = event.thumb {
                Image(uiImage: img).resizable().scaledToFill()
            } else {
                Image(systemName: event.isRadar ? "dot.radiowaves.right" : event.isHeart ? "heart.fill" : "waveform")
                    .font(.title2).foregroundStyle(tint)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(tint.opacity(0.12))
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .accessibilityLabel(event.thumb != nil ? "Frame thumbnail"
                            : (event.isRadar ? "Radar event" : event.isHeart ? "Heart-rate event" : "Event"))
    }
    private var tint: Color { event.isRadar ? .teal : event.isHeart ? .pink : patternColor(event.pattern) }
}

struct EventDetail: View {
    let event: HapticEvent
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if let img = event.thumb {
                        Image(uiImage: img).resizable().scaledToFit()
                            .frame(maxWidth: .infinity)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                            .accessibilityLabel("Frame that fired, with the gaze dot and box")
                    }
                    if let r = event.radar, !r.summary.isEmpty {
                        Label(r.summary, systemImage: "dot.radiowaves.right").font(.headline).foregroundStyle(.teal)
                    }
                    if let h = event.heart {
                        Label(h.summary, systemImage: "heart.fill").font(.headline).foregroundStyle(.pink)
                    }
                    if let t = event.trend, !t.summary.isEmpty {
                        Label(t.summary, systemImage: "timer").font(.subheadline).foregroundStyle(.purple)
                    }
                    if event.haikuRaw != nil || event.callID != nil {
                        HaikuRawSection(callID: event.callID, raw: event.haikuRaw)
                    }
                    Text(event.prettyJSON)
                        .font(.system(.footnote, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding()
            }
            .navigationTitle("Event #\(event.id)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("Done") { dismiss() } }
        }
    }
}
