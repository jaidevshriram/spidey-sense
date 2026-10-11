import SwiftUI
import UIKit

// v3.5 voice UI: "Hold to tell Spidey" (Spidey triggers), the draft sheet, the Settings row and the
// "speaking" glyph on Home cards. Audio lives in VoiceAudio.swift, wire messages in Protocol.swift.

/// Screenshot hooks share one switch: `-voiceDemo YES` shows the voice UI as if a voice-enabled engine were live.
enum VoiceDemo {
    static var active: Bool { UserDefaults.standard.bool(forKey: "voiceDemo") }
}

// MARK: - Hold to tell Spidey

struct TalkToSpideyButton: View {
    let stream: EventStream
    /// Called when a recording was sent (present the draft sheet).
    var onSent: () -> Void

    @State private var recorder = VoiceRecorder()
    @State private var pressing = false
    @State private var hint: String?
    @State private var hintTask: Task<Void, Never>?
    @State private var breathe = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicType

    private var live: Bool { stream.isLive || VoiceDemo.active }
    private var thinking: Bool { stream.voiceRequest == .thinking }
    private var recording: Bool { recorder.isRecording }
    private var enabled: Bool { live && !thinking }

    var body: some View {
        Group {
            if dynamicType.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 14) { orb; text }
            } else {
                HStack(spacing: 16) { orb; text; Spacer(minLength: 0) }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(recording ? Theme.accent.opacity(0.05) : Theme.card,
                    in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
            .stroke(recording ? Theme.accent : Theme.hairline, lineWidth: recording ? 2 : 1))
        .shadow(color: .black.opacity(recording ? 0.10 : 0.06), radius: 14, x: 0, y: 6)
        .scaleEffect(pressing && !reduceMotion ? 0.98 : 1)
        .animation(Theme.motion(reduceMotion), value: recording)
        .animation(Theme.motion(reduceMotion), value: pressing)
        .opacity(live ? 1 : 0.55)
        .contentShape(RoundedRectangle(cornerRadius: Theme.radius))
        // Press-and-hold anywhere on the card. Releasing (or a scroll taking over) ends the press.
        .onLongPressGesture(minimumDuration: .infinity, maximumDistance: .infinity, perform: {},
                            onPressingChanged: pressChanged)
        .accessibilityElement(children: .ignore)
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel(recording ? "Listening. Double-tap to send." : "Hold to tell Spidey")
        .accessibilityValue(thinking ? "Spidey is thinking" : "")
        .accessibilityHint(live ? "Describe a new trigger out loud. Double-tap to start, double-tap again to send."
                                : "Your Spidey hub isn't connected.")
        .accessibilityAction { toggleForVoiceOver() }
        .onAppear {
            recorder.onWillRecord = { [stream] in stream.speech.stop() }
            recorder.onFinish = { data, secs in finished(data, secs) }
            // Screenshot hook: `-talkDemo recording` shows the button mid-recording.
            if UserDefaults.standard.string(forKey: "talkDemo") == "recording" { recorder.showDemo() }
            if !reduceMotion {
                withAnimation(.easeOut(duration: 1.4).repeatForever(autoreverses: false)) { breathe = true }
            }
        }
    }

    // MARK: Pieces

    private var orb: some View {
        ZStack {
            // Live level meter: the ring grows with your voice.
            Circle().fill(Theme.accent.opacity(0.14))
                .frame(width: 60, height: 60)
                .scaleEffect(recording ? 1 + recorder.level * 0.55 : 1)
                .opacity(recording ? 1 : 0)
                .animation(.easeOut(duration: 0.08), value: recorder.level)
            // Calm pulse while listening.
            Circle().stroke(Theme.accent.opacity(0.45), lineWidth: 2)
                .frame(width: 60, height: 60)
                .scaleEffect(recording && breathe ? 1.7 : 1)
                .opacity(recording && breathe ? 0 : (recording ? 0.8 : 0))
            Circle().fill(enabled || recording ? Theme.accent : Theme.inkTertiary.opacity(0.6))
                .frame(width: 60, height: 60)
            if thinking {
                ProgressView().tint(.white)
            } else {
                Image(systemName: recording ? "waveform" : "mic.fill")
                    .font(.system(size: 24, weight: .semibold)).foregroundStyle(.white)
                    .contentTransition(.symbolEffect(.replace))
                    .symbolEffect(.variableColor.iterative, isActive: recording && !reduceMotion)
            }
        }
        .frame(width: 84, height: 84)
        .accessibilityHidden(true)
    }

    private var text: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.spHeadline).foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
                .contentTransition(.opacity)
            Text(detail).font(.subheadline).foregroundStyle(hint != nil ? Theme.accent : Theme.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .monospacedDigit()
            if recorder.denied && hint == nil {
                Button("Open Settings") {
                    if let u = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(u) }
                }
                .font(.subheadline.weight(.semibold)).tint(Theme.accent)
                .padding(.top, 2)
            }
        }
    }

    private var title: String {
        if recording { return "Listening… let go to send" }
        if thinking { return "Spidey is thinking…" }
        return "Hold to tell Spidey"
    }

    private var detail: String {
        if let hint { return hint }
        if recording { return "Up to 15 seconds · \(Int(recorder.remaining.rounded(.up))) s left" }
        if thinking { return "Turning what you said into a trigger." }
        if !live { return "Connect your Spidey hub to talk to Spidey." }
        if recorder.denied { return "Spidey needs the microphone to hear you." }
        return "Say what to watch for, like \u{201C}buzz me when a dog comes near\u{00A0}me.\u{201D}"
    }

    // MARK: Actions

    private func pressChanged(_ down: Bool) {
        pressing = down && enabled
        guard enabled || recording else { return }
        if down {
            // A short hold before the mic opens, so a scroll that starts on the card doesn't record.
            Task {
                try? await Task.sleep(for: .milliseconds(160))
                guard pressing, recorder.state == .idle else { return }
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                await recorder.start()
            }
        } else {
            recorder.stop()
        }
    }

    private func toggleForVoiceOver() {
        if recording { recorder.stop() }
        else if enabled { Task { await recorder.start() } }
    }

    private func finished(_ data: Data?, _ secs: TimeInterval) {
        pressing = false
        guard let data, !data.isEmpty else {
            showHint(recorder.denied ? "Allow the microphone in Settings to talk to Spidey."
                                     : "Couldn't use the microphone. Try again.")
            return
        }
        guard secs >= VoiceRecorder.minDuration else {
            showHint("Keep holding while you talk, then let go.")
            return
        }
        softHaptic()
        stream.sendVoiceCommand(data)
        onSent()
    }

    private func showHint(_ s: String) {
        hintTask?.cancel()
        withAnimation(Theme.motion(reduceMotion)) { hint = s }
        hintTask = Task {
            try? await Task.sleep(for: .seconds(3))
            guard !Task.isCancelled else { return }
            withAnimation(Theme.motion(reduceMotion)) { hint = nil }
        }
    }
}

// MARK: - Draft sheet

struct VoiceDraftSheet: View {
    let stream: EventStream
    /// "Edit": open the wizard on this rule (after the sheet is gone).
    var onEdit: (SenseRule) -> Void

    @State private var added: VoiceDraft?
    @State private var detent: PresentationDetent = .fraction(0.66)
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicType

    private var live: Bool { stream.isLive || VoiceDemo.active }

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            if let added {
                SuccessView(sentence: added.summary, name: added.rule.name, editing: false) { dismiss() }
                    .transition(.opacity)
            } else {
                switch stream.voiceRequest {
                case .idle, .thinking: thinkingView.transition(.opacity)
                case .draft(let d): draftView(d).transition(.opacity.combined(with: .scale(scale: 0.98)))
                case .failed(let message, let transcript): failedView(message, transcript).transition(.opacity)
                }
            }
        }
        .animation(Theme.motion(reduceMotion), value: stream.voiceRequest)
        .presentationDetents([.fraction(0.66), .large], selection: $detent)
        .presentationDragIndicator(.visible)
        .presentationCornerRadius(28)
        .onAppear { if dynamicType.isAccessibilitySize { detent = .large } }
    }

    // MARK: States

    private var thinkingView: some View {
        VStack(spacing: 18) {
            Spacer()
            ProgressView().controlSize(.large).tint(Theme.accent)
            VStack(spacing: 6) {
                Text("Spidey is thinking…").font(.spTitle2).foregroundStyle(Theme.ink)
                Text("Turning what you said into a trigger.")
                    .font(.body).foregroundStyle(Theme.inkSecondary)
            }
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            Spacer()
            Button("Cancel") { dismiss() }
                .font(.spHeadline).tint(Theme.inkSecondary)
                .frame(minHeight: 44)
        }
        .padding(.horizontal, 28).padding(.vertical, 20)
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .contain)
    }

    private func draftView(_ d: VoiceDraft) -> some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Label("Spidey heard you", systemImage: "waveform")
                        .font(.caption.weight(.heavy)).textCase(.uppercase).tracking(0.6)
                        .foregroundStyle(Theme.accent)
                    if !d.transcript.isEmpty {
                        Text("\u{201C}\(d.transcript)\u{201D}")
                            .font(.subheadline).italic().foregroundStyle(Theme.inkSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityLabel("You said: \(d.transcript)")
                    }
                    Text(d.summary).font(.spTitle).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true).lineSpacing(2)
                    ruleChip(d.rule)
                }
                .padding(.horizontal, 24).padding(.top, 28).padding(.bottom, 12)
            }
            .scrollIndicators(.hidden)
            .scrollBounceBehavior(.basedOnSize)

            VStack(spacing: 10) {
                if !live {
                    Label("Connect your Spidey hub to add triggers.", systemImage: "wifi.slash")
                        .font(.footnote).foregroundStyle(Theme.inkSecondary)
                }
                Button { add(d) } label: { Label("Add Spidey trigger", systemImage: "plus") }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(!live)
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 10) { editButton(d); cancelButton }
                    VStack(spacing: 10) { editButton(d); cancelButton }
                }
            }
            .padding(.horizontal, 20).padding(.top, 12).padding(.bottom, 12)
            .background(Theme.background.shadow(.drop(color: .black.opacity(0.04), radius: 8, y: -4)))
        }
    }

    private func editButton(_ d: VoiceDraft) -> some View {
        Button {
            softHaptic()
            onEdit(d.rule)
            dismiss()
        } label: { Label("Edit", systemImage: "slider.horizontal.3") }
            .buttonStyle(SecondaryButtonStyle())
    }

    private var cancelButton: some View {
        Button("Cancel") { dismiss() }.buttonStyle(SecondaryButtonStyle())
    }

    private func ruleChip(_ r: SenseRule) -> some View {
        let tint = Theme.led(r.rgb ?? TriggerColor.defaultRGB(r.feel))
        return HStack(spacing: 14) {
            Image(systemName: AlertIcon.symbol(for: r))
                .font(.system(size: 20, weight: .semibold)).foregroundStyle(tint)
                .frame(width: 48, height: 48)
                .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            VStack(alignment: .leading, spacing: 3) {
                Text(r.name).font(.body.weight(.semibold)).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Label(Plain.feelName(r.feel), systemImage: Plain.feelIcon(r.feel))
                    .font(.footnote).foregroundStyle(Theme.inkSecondary)
            }
            Spacer(minLength: 0)
        }
        .softCard(padding: 14)
        .accessibilityElement(children: .combine)
    }

    private func failedView(_ message: String, _ transcript: String?) -> some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: "ear.trianglebadge.exclamationmark")
                .font(.system(size: 34, weight: .semibold)).foregroundStyle(Theme.accent)
                .frame(width: 84, height: 84)
                .background(Theme.accentSoft, in: Circle())
                .accessibilityHidden(true)
            VStack(spacing: 8) {
                Text("Spidey didn't get that").font(.spTitle2).foregroundStyle(Theme.ink)
                Text(message).font(.body).foregroundStyle(Theme.inkSecondary)
                if let t = transcript, !t.isEmpty {
                    Text("You said \u{201C}\(t)\u{201D}").font(.subheadline).italic().foregroundStyle(Theme.inkTertiary)
                        .padding(.top, 4)
                }
            }
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            Spacer()
            Button("OK") { dismiss() }.buttonStyle(PrimaryButtonStyle())
        }
        .padding(.horizontal, 24).padding(.vertical, 16)
    }

    // MARK: Add

    private func add(_ d: VoiceDraft) {
        var r = d.rule
        // The engine picks a fresh id, but never overwrite an existing trigger by accident.
        let ids = Set(stream.rules.map(\.id))
        if ids.contains(r.id) {
            let base = r.id; var n = 2
            while ids.contains("\(base)_\(n)") { n += 1 }
            r.id = "\(base)_\(n)"
        }
        if stream.isLive { stream.upsert(r) }   // the offline screenshot demo only shows the success screen
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        withAnimation(Theme.motion(reduceMotion)) {
            added = VoiceDraft(transcript: d.transcript, summary: d.summary, rule: r)
            detent = .large
        }
    }
}

// MARK: - Settings: Spidey's voice

struct VoiceSettingsCard: View {
    let stream: EventStream
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var voice: VoiceSettings? { stream.settings.voice }
    private var live: Bool { stream.isLive || VoiceDemo.active }
    private var enabled: Bool { live && voice?.available == true }
    private var note: String? {
        guard live else { return nil }
        guard let voice else { return "Update Spidey on the Mac to use this." }
        return voice.available ? nil : "Add an ElevenLabs key on the Mac to turn this on."
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: stream.speech.isSpeaking ? "speaker.wave.3.fill" : "speaker.wave.2.fill")
                    .font(.title3).foregroundStyle(Theme.inkSecondary).frame(width: 28)
                    .symbolEffect(.variableColor.iterative, isActive: stream.speech.isSpeaking && !reduceMotion)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Spidey's voice").font(.spHeadline).foregroundStyle(Theme.ink)
                    Text("Spidey says why it buzzed, out loud, in your earbuds or on the phone.")
                        .font(.subheadline).foregroundStyle(Theme.inkSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    ForEach(VoiceSpeak.allCases) { m in
                        Pill(text: m.label, selected: stream.voiceSpeak == m) { pick(m) }
                    }
                }
                VStack(spacing: 2) {
                    ForEach(VoiceSpeak.allCases) { m in
                        RadioRow(text: m.label, selected: stream.voiceSpeak == m) { pick(m) }
                    }
                }
            }
            .disabled(!enabled)
            .opacity(enabled ? 1 : 0.5)
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Spidey's voice")
            if let note {
                Label(note, systemImage: "info.circle")
                    .font(.footnote).foregroundStyle(Theme.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .softCard(padding: 18)
        .id("voice")
    }

    private func pick(_ m: VoiceSpeak) {
        withSpring { stream.command(.voice(m)) }
    }
}

// MARK: - "Speaking" glyph on a Home card

struct SpeakingGlyph: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var body: some View {
        Image(systemName: "speaker.wave.2.fill")
            .font(.footnote.weight(.semibold))
            .foregroundStyle(Theme.accent)
            .symbolEffect(.variableColor.iterative.reversing, isActive: !reduceMotion)
            .padding(.horizontal, 9).padding(.vertical, 5)
            .background(Theme.accentSoft, in: Capsule())
            .accessibilityLabel("Spidey is explaining this out loud")
    }
}
