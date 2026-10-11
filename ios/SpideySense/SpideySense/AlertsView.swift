import SwiftUI

// MARK: - Plain-language trigger text + icons

enum AlertIcon {
    /// An SF Symbol for a trigger, from keywords in its name / description.
    static func symbol(for text: String) -> String {
        let t = text.lowercased()
        func has(_ words: String...) -> Bool { words.contains { t.contains($0) } }
        if has("lion", "tiger", "cat", "animal", "dog", "bear", "paw") { return "pawprint.fill" }
        if has("water", "lake", "river", "sea", "pool") { return "water.waves" }
        if has("stair", "step", "curb", "kerb") { return "stairs" }
        if has("knife", "weapon", "gun") { return "exclamationmark.shield.fill" }
        if has("bike", "bicycle", "cyclist", "blindspot") { return "bicycle" }
        if has("car", "vehicle", "traffic") { return "car.fill" }
        if has("heart") { return "heart.fill" }
        if has("behind", "radar") { return "figure.walk.arrival" }
        if has("person", "someone", "people", "child") { return "figure.walk" }
        return "eye.fill"
    }

    static func symbol(for r: SenseRule) -> String {
        if r.isRadar { return "figure.walk.arrival" }
        if r.isHeart { return "heart.fill" }
        if r.senseSource == .macMetric { return "desktopcomputer" }
        return symbol(for: r.name + " " + (r.describe ?? "") + " " + r.id)
    }
}

enum RulePlain {
    /// "Buzzes when we see a lion or tiger and you haven't looked at it."
    static func describe(_ r: SenseRule) -> String {
        if r.isRadar {
            return "Buzzes when someone comes up behind you (within \(RuleText.fmt(r.cond.maxDistanceM ?? 2)) m)."
        }
        if r.isHeart { return "Buzzes when your heart rate jumps well above your normal." }
        if r.senseSource == .macMetric { return "Buzzes when the Mac gets too hot." }
        let gaze: String
        switch r.effectiveGazeMode {
        case .unseen: gaze = " and you haven't noticed it"
        case .lookingAt: gaze = " while you're looking at it"
        case .any: gaze = ""
        }
        if r.isCustom, let d = r.describe?.trimmingCharacters(in: .whitespaces), !d.isEmpty {
            let what = cleanWhat(d)
            return "Buzzes when we see \(what)\(gaze)" + (r.isTemporal ? ", and it keeps getting closer." : ".")
        }
        // Built-in hazard logic.
        if case .kinds(let k) = r.when, !k.isEmpty {
            return "Buzzes for a \(k.joined(separator: " or ")) nearby\(gaze)."
        }
        return r.effectiveGazeMode == .unseen || r.cond.unseen == true
            ? "Buzzes for a hazard nearby that you haven't noticed."
            : "A gentle heads-up about things around you."
    }

    /// Strips "warn me if" style lead-ins and the engine-only "compared to earlier frames" hint.
    static func cleanWhat(_ d: String) -> String {
        var s = d
        for lead in ["warn me if ", "warn me when ", "tell me if ", "tell me when ", "alert me if ", "buzz me if ", "buzz when "]
        where s.lowercased().hasPrefix(lead) { s = String(s.dropFirst(lead.count)) }
        for tail in [" compared to earlier frames", " (lake/river/sea)"] { s = s.replacingOccurrences(of: tail, with: "") }
        return s
    }
}

// MARK: - My alerts

struct AlertsView: View {
    let stream: EventStream

    @State private var wizard: WizardLaunch?
    @State private var technical: SenseRule?
    @State private var confirmDelete: SenseRule?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var live: Bool { stream.isLive }
    private var mine: [SenseRule] { stream.rules.filter { $0.isCustom } }
    private var basics: [SenseRule] { stream.rules.filter { !$0.isCustom } }
    private var suggested: [SenseRule] {
        let ids = Set(stream.rules.map(\.id))
        return MockData.customTemplates.filter { !ids.contains($0.id) }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text("Spidey triggers").font(.spLargeTitle).foregroundStyle(Theme.ink)
                                .lineLimit(1).minimumScaleFactor(0.8)
                                .accessibilityAddTraits(.isHeader)
                            Spacer()
                            if stream.rulesAreSample { SampleTag() }
                        }
                        Text("Your Spidey Sense taps you when one of these happens.")
                            .font(.body).foregroundStyle(Theme.inkSecondary)
                    }
                    .padding(.top, 12)

                    Button { wizard = WizardLaunch(original: nil) } label: {
                        Label("New Spidey trigger", systemImage: "plus").font(.spHeadline)
                    }
                    .buttonStyle(PrimaryButtonStyle())
                    .accessibilityHint("Set up a new alert, one question at a time")

                    if !live { offlineNote }

                    if !mine.isEmpty {
                        VStack(spacing: 14) {
                            ForEach(mine) { r in card(r).transition(.opacity.combined(with: .scale(scale: 0.97))) }
                        }
                    }
                    if !basics.isEmpty {
                        VStack(alignment: .leading, spacing: 12) {
                            SectionTitle(text: "Always on")
                            ForEach(basics) { r in card(r) }
                        }
                    }
                    if !suggested.isEmpty {
                        VStack(alignment: .leading, spacing: 12) {
                            SectionTitle(text: "Suggested")
                            ForEach(suggested) { r in suggestedRow(r) }
                        }
                    }
                    if stream.rules.isEmpty {
                        Text("No Spidey triggers yet. Tap New Spidey trigger to set one up.")
                            .font(.body).foregroundStyle(Theme.inkSecondary)
                            .frame(maxWidth: .infinity).padding(.top, 20)
                    }
                }
                .padding(.horizontal, 20).padding(.bottom, 32)
                .animation(Theme.motion(reduceMotion), value: stream.rules)
            }
            .scrollIndicators(.hidden)
            .background(Theme.background.ignoresSafeArea())
            .toolbar(.hidden, for: .navigationBar)
            .statusBarBackdrop()
            .fullScreenCover(item: $wizard) { w in
                AlertWizard(stream: stream, original: w.original, template: w.template)
            }
            .sheet(item: $technical) { r in
                SenseBuilder(original: r, existingIDs: Set(stream.rules.map(\.id)), stream: stream) { stream.upsert($0) }
            }
            .confirmationDialog("Delete \"\(confirmDelete?.name ?? "")\"?",
                                isPresented: Binding(get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } }),
                                titleVisibility: .visible, presenting: confirmDelete) { r in
                Button("Delete trigger", role: .destructive) { withSpring { stream.deleteRule(id: r.id) } }
                Button("Cancel", role: .cancel) {}
            } message: { _ in Text("Spidey will stop watching for this.") }
            .onAppear {
                // Screenshot/demo hooks: `-openWizard YES` (new alert), `-wizardStep N` picks the step.
                if UserDefaults.standard.bool(forKey: "openWizard") { wizard = WizardLaunch(original: nil) }
                // `-editAlert <id>` opens that alert in the wizard (Review screen).
                if let id = UserDefaults.standard.string(forKey: "editAlert"),
                   let r = stream.rules.first(where: { $0.id == id }) { edit(r) }
            }
        }
    }

    private var offlineNote: some View {
        HStack(spacing: 12) {
            Image(systemName: "wifi.slash").font(.title3).foregroundStyle(Theme.inkSecondary)
            Text("Your Spidey hub isn't connected, so triggers can't be changed right now.")
                .font(.subheadline).foregroundStyle(Theme.inkSecondary)
        }
        .softCard(padding: 16)
    }

    private func card(_ r: SenseRule) -> some View {
        let tint = Theme.led(r.rgb ?? TriggerColor.defaultRGB(r.feel))
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center) {
                Image(systemName: AlertIcon.symbol(for: r))
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(tint)
                    .frame(width: 52, height: 52)
                    .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                Spacer()
                Toggle("On", isOn: Binding(get: { r.enabled }, set: { on in softHaptic(); withSpring { stream.setEnabled(r, on) } }))
                    .labelsHidden().tint(Theme.accent).disabled(!live)
                    .accessibilityLabel("\(r.name) on")
            }
            VStack(alignment: .leading, spacing: 6) {
                Text(r.name).font(.spCardTitle).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Text(RulePlain.describe(r)).font(.body).foregroundStyle(Theme.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true).lineSpacing(2)
            }
            HStack(spacing: 6) {
                Image(systemName: Plain.feelIcon(r.feel)).font(.caption)
                Text(Plain.feelName(r.feel)).font(.caption.weight(.semibold))
            }
            .foregroundStyle(Theme.inkSecondary)
            .padding(.horizontal, 9).padding(.vertical, 4)
            .background(Theme.ink.opacity(0.05), in: Capsule())
        }
        .softCard(padding: 18)
        .opacity(r.enabled ? 1 : 0.6)
        .contentShape(RoundedRectangle(cornerRadius: Theme.radius))
        .onTapGesture { edit(r) }
        .contextMenu {
            Button { edit(r) } label: { Label("Edit", systemImage: "pencil") }
            Button(role: .destructive) { confirmDelete = r } label: { Label("Delete", systemImage: "trash") }
                .disabled(!live)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAction(named: "Edit") { edit(r) }
        .accessibilityAction(named: "Delete") { confirmDelete = r }
    }

    private func suggestedRow(_ r: SenseRule) -> some View {
        Button { wizard = WizardLaunch(original: nil, template: r) } label: {
            HStack(spacing: 14) {
                Image(systemName: AlertIcon.symbol(for: r))
                    .font(.system(size: 18, weight: .semibold)).foregroundStyle(Theme.inkSecondary)
                    .frame(width: 44, height: 44)
                    .background(Theme.ink.opacity(0.05), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                VStack(alignment: .leading, spacing: 2) {
                    Text(r.name).font(.body.weight(.semibold)).foregroundStyle(Theme.ink)
                    Text(RulePlain.describe(r)).font(.footnote).foregroundStyle(Theme.inkSecondary).lineLimit(2)
                }
                Spacer()
                Image(systemName: "plus.circle.fill").font(.title2).foregroundStyle(Theme.accent)
            }
            .softCard(padding: 14)
        }
        .buttonStyle(PressableStyle())
    }

    private func edit(_ r: SenseRule) {
        if r.isCustom { wizard = WizardLaunch(original: r) } else { technical = r }
    }
}

struct WizardLaunch: Identifiable {
    let id = UUID()
    var original: SenseRule?
    var template: SenseRule? = nil
}
