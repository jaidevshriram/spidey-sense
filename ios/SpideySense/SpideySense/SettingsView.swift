import SwiftUI

struct SettingsView: View {
    @Binding var serverURL: String
    let stream: EventStream
    @State private var draft = ""
    @State private var intensity = 1.0
    @State private var confirmStop = false
    @FocusState private var urlFocused: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("ws://192.168.1.10:8765", text: $draft)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .focused($urlFocused)
                        .onSubmit(save)
                    HStack {
                        StatusDot(status: stream.status)
                        Spacer()
                        Button("Save & reconnect", action: save)
                    }
                } header: { Text("Engine server") } footer: {
                    Text("Mac LAN IP: run `ipconfig getifaddr en0`, then ws://<ip>:8765.")
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
                    Text("Quiet mode logs every trigger but doesn't buzz. Intensity is stored by the engine; applying it to the dots is still TODO.")
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
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .onAppear { draft = serverURL; intensity = stream.settings.intensity }
            .onChange(of: stream.settings.intensity) { _, v in intensity = v }
            .alert("Stop sent", isPresented: $confirmStop) { Button("OK") {} } message: {
                Text(stream.isLive ? "Asked the Mac to stop all haptics." : "Not connected: nothing to stop.")
            }
        }
    }

    private func save() {
        urlFocused = false
        if draft == serverURL { stream.connect(to: draft) } else { serverURL = draft }
    }
}
