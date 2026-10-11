import SwiftUI

struct SettingsView: View {
    @Binding var serverURL: String          // "" = automatic
    let stream: EventStream
    let effectiveURL: String
    let source: ServerSource
    @State private var draft = ""
    @State private var intensity = 1.0
    @State private var confirmStop = false
    @FocusState private var urlFocused: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Automatic (\(effectiveURL))", text: $draft)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .focused($urlFocused)
                        .onSubmit(save)
                    HStack {
                        StatusDot(status: stream.status)
                        Spacer()
                        Button("Save & reconnect", action: save)
                    }
                    LabeledContent("Using") {
                        Text(effectiveURL).font(.footnote.monospaced()).textSelection(.enabled)
                    }
                    LabeledContent("Source", value: source.label)
                    if source == .custom {
                        Button("Reset to automatic") { draft = ""; urlFocused = false; serverURL = "" }
                    }
                } header: { Text("Engine server") } footer: {
                    Text("Leave empty for automatic: the app finds the engine on the network (Bonjour), else uses the Mac's IP from build time (iPhone) or 127.0.0.1 (Simulator). Custom: ws://<mac-ip>:8765 (`ipconfig getifaddr en0`).")
                }
                if let err = stream.lastError, !stream.isLive {
                    Section("Last error") { Text(err).font(.footnote).foregroundStyle(.secondary) }
                }

                Section {
                    VStack(alignment: .leading) {
                        Text("Master intensity \(Int(intensity * 100))%")
                        Slider(value: $intensity, in: 0...1, step: 0.05) { editing in
                            if !editing { stream.command(.setIntensity(intensity)) }
                        }
                    }
                    Toggle(isOn: Binding(get: { stream.settings.quiet }, set: { stream.command(.quiet($0)) })) {
                        Label("Quiet mode", systemImage: "moon")
                    }
                } header: { Text("Haptics") } footer: {
                    Text("Quiet mode logs every trigger but doesn't buzz. Master intensity scales the Dot's waveforms (weaker variants); 0 = light only.")
                }

                Section("Test pattern") {
                    ForEach(["urgent", "pulse", "ambient"], id: \.self) { p in
                        Button { stream.command(.testPattern(p)) } label: {
                            Label("Play \(p)", systemImage: "play.circle.fill").foregroundStyle(patternColor(p))
                        }
                    }
                }

                Section {
                    Button(role: .destructive) { stream.command(.stopAll); confirmStop = true } label: {
                        Label("Emergency stop", systemImage: "stop.circle.fill")
                            .font(.headline).foregroundStyle(.white).frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent).tint(.red)
                    .listRowBackground(Color.clear)
                } footer: {
                    if let ack = stream.lastAck { Text("Engine: \(ack)") }
                }

                Section { Button("Clear log", role: .destructive) { stream.clear() } }

                aboutSection
            }
            .engineHeader(stream)
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .onAppear { draft = ServerConfig.isAutomatic(serverURL) ? "" : serverURL; intensity = stream.settings.intensity }
            .onChange(of: stream.settings.intensity) { _, v in intensity = v }
            .alert("Stop sent", isPresented: $confirmStop) { Button("OK") {} } message: {
                Text(stream.isLive ? "Asked the Mac to stop all haptics." : "Not connected: nothing to stop.")
            }
        }
    }

    /// v3 `status.about` (what the engine runs) plus this app's version.
    private var aboutSection: some View {
        let live = stream.engineStatus?.about
        let about = live ?? (stream.isLive ? nil : MockData.status.about)
        let jefeSink = stream.devicesAreSample ? nil : stream.devices.first(where: { $0.id == "el_jefe" })?.sink
        let info = Bundle.main.infoDictionary
        return Section {
            LabeledContent("Config", value: about?.config ?? "—")
            LabeledContent("Model", value: about?.model ?? "—")
            LabeledContent("Commit", value: about?.commit ?? "—")
            LabeledContent("Sink") {
                let sink = about?.sink ?? jefeSink ?? "—"
                Text(sink).foregroundStyle(sink == "console" ? .orange : .secondary)
            }
            LabeledContent("App", value: "\(info?["CFBundleShortVersionString"] as? String ?? "?") (\(info?["CFBundleVersion"] as? String ?? "?"))")
        } header: { Text("About") } footer: {
            if live == nil {
                Text(stream.isLive ? "This engine doesn't send status.about yet." : "Sample values: not connected to the Mac engine.")
            }
        }
    }

    private func save() {
        urlFocused = false
        let new = draft.trimmingCharacters(in: .whitespaces)   // "" = automatic
        if new == serverURL { stream.connect(to: effectiveURL) } else { serverURL = new }
    }
}
