import SwiftUI
import UIKit

// MARK: - Draft (what the wizard is building)

/// The New-alert wizard's answers. `build` turns them into the same trigger JSON the technical editor sends
/// (docs/protocol.md "Triggers" / "v3"), so the engine behaviour is unchanged.
struct AlertDraft {
    enum Kind { case camera, radar, heart }

    var kind: Kind = .camera
    var tile: WatchTile.ID?
    var describe = ""
    var name = ""
    var nameEdited = false
    var gaze: GazeMode = .unseen
    var noticeMemory: Double = 3
    var temporal = false
    var feel: String?                 // nil = recommended
    var rgb: [Int]?                   // nil = the feel's default colour
    var intensity: Double = 1
    var minUrgency: Double = 0.5
    var cooldown: Double = 60
    var dot: Int?                     // nil = all dots
    var maxDistance: Double = 2.0     // radar
    var minSpeed: Double = 0.3
    var hrDelta: Double = 15          // heart
    var hrPct: Double = 20
    var hrSustain: Double = 8
    var original: SenseRule?

    var recommendedFeel: String {
        switch kind {
        case .radar: return "urgent"
        case .heart: return "pulse"
        case .camera:
            switch gaze {
            case .unseen: return "urgent"
            case .lookingAt: return "pulse"
            case .any: return temporal ? "pulse" : "ambient"
            }
        }
    }
    var effectiveFeel: String { feel ?? recommendedFeel }
    var effectiveRGB: [Int] { rgb ?? TriggerColor.defaultRGB(effectiveFeel) }

    var trimmedDescribe: String { describe.trimmingCharacters(in: .whitespacesAndNewlines) }
    var suggestedName: String {
        if let t = tile.flatMap(WatchTile.byID), t.describe == trimmedDescribe || kind != .camera { return t.title }
        return trimmedDescribe.isEmpty ? "My alert" : SenseBuilder.suggestName(from: trimmedDescribe)
    }
    var finalName: String {
        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return n.isEmpty ? suggestedName : n
    }
    var hasSubject: Bool { kind != .camera || !trimmedDescribe.isEmpty }

    /// "a big animal like a lion or tiger" (the tile's phrase if the text is still the tile's).
    var whatPhrase: String {
        if let t = tile.flatMap(WatchTile.byID), t.describe == trimmedDescribe { return t.phrase }
        return RulePlain.cleanWhat(trimmedDescribe)
    }

    /// "When Spidey sees a big animal and you haven't noticed it, you'll feel a Strong tap."
    var sentence: AttributedString {
        func b(_ s: String) -> AttributedString {
            var a = AttributedString(s); a.font = .title3.weight(.bold); a.foregroundColor = Theme.ink; return a
        }
        func p(_ s: String) -> AttributedString { AttributedString(s) }
        var out: AttributedString
        switch kind {
        case .radar:
            out = p("When ") + b("someone comes up behind you") + p(" (within \(RuleText.fmt(maxDistance)) m)")
        case .heart:
            out = p("When ") + b("your heart starts racing") + p(" (well above your normal for a few seconds)")
        case .camera:
            out = p("When Spidey sees ") + b(whatPhrase.isEmpty ? "something" : whatPhrase)
            switch gaze {
            case .unseen: out += p(" and ") + b("you haven't noticed it")
            case .lookingAt: out += p(" while ") + b("you're looking at it")
            case .any: break
            }
            if temporal { out += p(", and it ") + b("keeps getting closer") }
        }
        let f = Plain.feelName(effectiveFeel)
        out += p(", you'll feel a ") + b(f) + p(".")
        return out
    }

    /// Success screen: "We'll tap you when we see a big animal and you haven't noticed it."
    var successLine: String {
        let feelPart = " with a \(Plain.feelName(effectiveFeel).lowercased())"
        switch kind {
        case .radar: return "We'll tap you\(feelPart) when someone comes up behind you."
        case .heart: return "We'll tap you\(feelPart) when your heart starts racing."
        case .camera:
            var s = "We'll tap you\(feelPart) when we see \(whatPhrase.isEmpty ? "it" : whatPhrase)"
            switch gaze {
            case .unseen: s += " and you haven't noticed it"
            case .lookingAt: s += " while you're looking at it"
            case .any: break
            }
            if temporal { s += ", once it keeps getting closer" }
            return s + "."
        }
    }

    // MARK: Load / build

    static func from(_ r: SenseRule) -> AlertDraft {
        var d = AlertDraft()
        d.original = r
        d.kind = r.isRadar ? .radar : r.isHeart ? .heart : .camera
        d.describe = r.describe ?? ""
        d.tile = WatchTile.all.first { $0.describe == d.describe && $0.kind == d.kind }?.id
            ?? (d.kind == .radar ? "behind" : d.kind == .heart ? "heart" : nil)
        d.name = r.name; d.nameEdited = true
        d.gaze = r.effectiveGazeMode
        d.noticeMemory = r.noticeMemoryS ?? 3
        d.temporal = r.isTemporal
        d.feel = r.feel
        d.rgb = r.rgb
        d.intensity = r.intensity ?? 1
        d.minUrgency = r.cond.minUrgency ?? 0.5
        d.cooldown = r.cooldownS
        d.dot = r.dot
        d.maxDistance = r.cond.maxDistanceM ?? 2; d.minSpeed = r.cond.minSpeedMps ?? 0.3
        d.hrDelta = r.heartDeltaBpm; d.hrPct = r.heartDeltaPct; d.hrSustain = r.heartSustainS
        return d
    }

    /// Same shape as SenseBuilder.build() for custom triggers; an edited rule keeps any fields the wizard
    /// doesn't show (e.g. trend settings, `when`).
    func build(existingIDs: Set<String>) -> SenseRule {
        var id = original?.id ?? triggerSlug(finalName, fallback: "alert")
        if original == nil {
            let base = id; var n = 2
            while existingIDs.contains(id) { id = "\(base)_\(n)"; n += 1 }
        }
        var r = original ?? SenseRule(id: id, name: finalName)
        r.id = id; r.name = finalName; r.kind = "custom"
        r.enabled = original?.enabled ?? true
        r.feel = effectiveFeel
        r.intensity = (intensity * 100).rounded() / 100
        r.rgb = effectiveRGB
        r.dot = dot
        r.cooldownS = cooldown
        switch kind {
        case .heart:
            r.source = SenseSource.heartRate.rawValue; r.when = .any; r.describe = nil
            r.cond = RuleCondition(minDeltaBpm: hrDelta, minDeltaPct: hrPct, sustainS: hrSustain)
            r.gazeMode = nil; r.noticeMemoryS = nil; r.temporal = nil; r.trend = nil
        case .radar:
            r.source = SenseSource.radar.rawValue; r.when = .any; r.describe = nil
            r.cond = RuleCondition(unseen: true, maxDistanceM: (maxDistance * 10).rounded() / 10,
                                   minSpeedMps: (minSpeed * 100).rounded() / 100)
            r.gazeMode = GazeMode.unseen.rawValue; r.noticeMemoryS = nil; r.temporal = false; r.trend = nil
        case .camera:
            r.source = SenseSource.neon.rawValue
            r.describe = trimmedDescribe
            var c = original?.isCustom == true && original?.senseSource == .neon ? original!.cond : RuleCondition()
            c.unseen = gaze == .unseen; c.minUrgency = minUrgency
            c.maxDistanceM = nil; c.minSpeedMps = nil; c.minDeltaBpm = nil; c.minDeltaPct = nil; c.sustainS = nil
            r.cond = c
            r.gazeMode = gaze.rawValue
            r.noticeMemoryS = gaze == .unseen ? noticeMemory : nil
            r.temporal = temporal
            r.trend = temporal ? (original?.trend ?? TrendConfig()) : nil   // defaults: 3 ticks, medium
        }
        return r
    }
}

/// A ready-made "what to watch for" picture tile.
struct WatchTile: Identifiable {
    let id: String
    let title: String
    let symbol: String
    let badge: String?          // small secondary symbol
    let tint: Color
    let kind: AlertDraft.Kind
    let describe: String        // sent to Haiku (camera)
    let phrase: String          // for the summary sentence
    var gaze: GazeMode = .unseen
    var temporal = false

    static let all: [WatchTile] = [
        WatchTile(id: "behind", title: "Someone behind me", symbol: "figure.walk", badge: "dot.radiowaves.right",
                  tint: Color(red: 0.25, green: 0.47, blue: 0.95), kind: .radar,
                  describe: "", phrase: "someone coming up behind you"),
        WatchTile(id: "stairs", title: "Stairs or a step down", symbol: "stairs", badge: "arrow.down",
                  tint: Color(red: 0.95, green: 0.55, blue: 0.12), kind: .camera,
                  describe: "stairs, a curb or a step down just ahead of me", phrase: "stairs or a step down ahead"),
        WatchTile(id: "animal", title: "Big animal", symbol: "pawprint.fill", badge: nil,
                  tint: Color(red: 0.78, green: 0.52, blue: 0.20), kind: .camera,
                  describe: "a big animal such as a lion, tiger or bear", phrase: "a big animal like a lion or tiger"),
        WatchTile(id: "knife", title: "Knife nearby", symbol: "exclamationmark.shield.fill", badge: nil,
                  tint: Color(red: 0.90, green: 0.22, blue: 0.27), kind: .camera,
                  describe: "someone holding a knife near me", phrase: "someone holding a knife near you"),
        WatchTile(id: "water", title: "Person near water", symbol: "figure.walk", badge: "water.waves",
                  tint: Color(red: 0.10, green: 0.62, blue: 0.70), kind: .camera,
                  describe: "a person getting close to the edge of water (lake/river/sea) compared to earlier frames",
                  phrase: "a person near the water's edge", gaze: .any, temporal: true),
        WatchTile(id: "heart", title: "Heart racing", symbol: "heart.fill", badge: "waveform.path.ecg",
                  tint: Color(red: 0.93, green: 0.27, blue: 0.52), kind: .heart,
                  describe: "", phrase: "your heart racing"),
    ]
    static func byID(_ id: String) -> WatchTile? { all.first { $0.id == id } }
}

// MARK: - Wizard

struct AlertWizard: View {
    enum Step: Int, CaseIterable { case watch, when, wait, feel, summary }

    let stream: EventStream
    let original: SenseRule?
    var template: SenseRule? = nil

    @State private var draft = AlertDraft()
    @State private var step: Step = .watch
    @State private var forward = true
    @State private var customizeFeel = false
    @State private var saved = false
    @State private var confirmDelete = false
    @State private var returnToSummary = false   // editing: an "Edit" link jumps back to Review after one step
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var steps: [Step] {
        draft.kind == .camera ? Step.allCases : [.watch, .feel, .summary]
    }
    private var index: Int { steps.firstIndex(of: step) ?? 0 }
    private var progress: Double { Double(index + 1) / Double(steps.count) }

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            if saved {
                SuccessView(sentence: draft.successLine, name: draft.finalName, editing: original != nil) { dismiss() }
                    .transition(.opacity)
            } else {
                VStack(spacing: 0) {
                    topBar
                    ZStack {
                        stepView
                            .id(step)
                            .transition(stepTransition)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .clipped()
                    bottomBar
                }
            }
        }
        .preferredColorScheme(.light)
        .onAppear(perform: setUp)
        .task { if UserDefaults.standard.bool(forKey: "demoWizard") { await autoplay() } }
    }

    private func setUp() {
        if let original {
            draft = AlertDraft.from(original); step = .summary
        } else if let template {
            draft = AlertDraft.from(template); draft.original = nil; draft.nameEdited = false
            draft.name = template.name; draft.nameEdited = true
            step = .summary
        }
        // Screenshot/demo hooks: `-wizardTile animal -wizardStep 3 -wizardCustomize YES`.
        let d = UserDefaults.standard
        if let t = d.string(forKey: "wizardTile"), let tile = WatchTile.byID(t) { pick(tile) }
        if d.object(forKey: "wizardStep") != nil, let s = Step(rawValue: d.integer(forKey: "wizardStep")) { step = s }
        if d.bool(forKey: "wizardCustomize") { customizeFeel = true; draft.feel = draft.recommendedFeel }
        if d.bool(forKey: "wizardSaved") { saved = true }
    }

    /// `-demoWizard YES`: plays the whole flow by itself, for the screen recording.
    private func autoplay() async {
        func wait(_ s: Double) async { try? await Task.sleep(for: .seconds(s)) }
        let m = Theme.motion(reduceMotion)
        await wait(1.2); softHaptic(); withAnimation(m) { pick(WatchTile.byID("animal")!) }
        await wait(1.3); next()
        await wait(1.5); withAnimation(m) { draft.noticeMemory = 10 }
        await wait(1.2); next()
        await wait(1.6); next()
        await wait(1.6); draft.feel = draft.effectiveFeel; withAnimation(m) { customizeFeel = true }
        await wait(1.3); withAnimation(m) { draft.feel = "pulse"; draft.rgb = nil }
        await wait(1.2); withAnimation(m) { draft.rgb = [0, 90, 255] }
        await wait(1.3); next()
        await wait(2.0); withAnimation(m) { advanced = true }
        await wait(2.0); demoSave = true; save()
    }
    @State private var demoSave = false

    private var stepTransition: AnyTransition {
        if reduceMotion { return .opacity }
        return .asymmetric(insertion: .move(edge: forward ? .trailing : .leading).combined(with: .opacity),
                           removal: .move(edge: forward ? .leading : .trailing).combined(with: .opacity))
    }

    private func go(to s: Step) {
        let newForward = (steps.firstIndex(of: s) ?? 0) >= index
        forward = newForward
        DispatchQueue.main.async { withAnimation(Theme.motion(reduceMotion)) { step = s } }
    }

    private func next() {
        softHaptic()
        if returnToSummary, step != .summary { returnToSummary = false; go(to: .summary); return }
        if index + 1 < steps.count { go(to: steps[index + 1]) }
    }

    private func back() {
        if index == 0 || (original != nil && step == .summary) { dismiss(); return }
        if returnToSummary { returnToSummary = false; go(to: .summary); return }
        go(to: steps[index - 1])
    }

    // MARK: Chrome

    private var topBar: some View {
        VStack(spacing: 14) {
            HStack {
                Button(action: back) {
                    Image(systemName: index == 0 || (original != nil && step == .summary) ? "xmark" : "chevron.left")
                        .font(.body.weight(.semibold)).foregroundStyle(Theme.ink)
                        .frame(width: 44, height: 44)
                        .background(Theme.card, in: Circle())
                        .overlay(Circle().stroke(Theme.hairline))
                }
                .accessibilityLabel(index == 0 ? "Close" : "Back")
                Spacer()
                Text(original != nil ? "Edit trigger" : "Step \(index + 1) of \(steps.count)")
                    .font(.subheadline.weight(.medium)).foregroundStyle(Theme.inkSecondary)
                    .contentTransition(.numericText())
                Spacer()
                Color.clear.frame(width: 44, height: 44)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Theme.ink.opacity(0.08))
                    Capsule().fill(Theme.accent).frame(width: max(8, geo.size.width * progress))
                }
            }
            .frame(height: 6)
            .animation(Theme.motion(reduceMotion), value: progress)
            .accessibilityElement()
            .accessibilityLabel("Progress")
            .accessibilityValue("Step \(index + 1) of \(steps.count)")
        }
        .padding(.horizontal, 20).padding(.top, 8).padding(.bottom, 4)
    }

    @ViewBuilder private var bottomBar: some View {
        VStack(spacing: 10) {
            switch step {
            case .feel where !customizeFeel:
                Button {
                    draft.feel = nil; draft.rgb = nil; draft.intensity = 1; next()
                } label: { Text("Skip, use recommended") }
                    .buttonStyle(PrimaryButtonStyle())
                Button {
                    softHaptic()
                    draft.feel = draft.effectiveFeel
                    withAnimation(Theme.motion(reduceMotion)) { customizeFeel = true }
                } label: { Label("Customize", systemImage: "slider.horizontal.3") }
                    .buttonStyle(SecondaryButtonStyle())
            case .summary:
                if !stream.isLive {
                    Label("Connect your Spidey hub to save triggers.", systemImage: "wifi.slash")
                        .font(.footnote).foregroundStyle(Theme.inkSecondary)
                }
                Button(action: save) { Text(original != nil ? "Save changes" : "Turn on trigger") }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(!stream.isLive || !draft.hasSubject)
            default:
                Button(action: next) { Text("Next") }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(step == .watch && !draft.hasSubject)
            }
        }
        .padding(.horizontal, 20).padding(.top, 12).padding(.bottom, 12)
        .background(Theme.background.shadow(.drop(color: .black.opacity(0.04), radius: 8, y: -4)))
    }

    @ViewBuilder private var stepView: some View {
      ScrollViewReader { proxy in
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                switch step {
                case .watch: watchStep
                case .when: whenStep
                case .wait: waitStep
                case .feel: feelStep
                case .summary: summaryStep
                }
            }
            .padding(.horizontal, 20).padding(.top, 20).padding(.bottom, 24)
        }
        .scrollDismissesKeyboard(.interactively)
        .scrollIndicators(.hidden)
        .onChange(of: advanced) { _, open in
            // Opening Advanced brings it into view.
            guard open else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                withAnimation(Theme.motion(reduceMotion)) { proxy.scrollTo("advanced", anchor: .top) }
            }
        }
      }
    }

    private func title(_ t: String, _ sub: String? = nil, optional: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(t).font(.spLargeTitle).foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
            }
            if optional {
                Text("Optional").font(.caption.weight(.bold)).foregroundStyle(Theme.inkSecondary)
                    .padding(.horizontal, 9).padding(.vertical, 4)
                    .background(Theme.ink.opacity(0.06), in: Capsule())
            }
            if let sub { Text(sub).font(.body).foregroundStyle(Theme.inkSecondary).lineSpacing(2) }
        }
    }

    // MARK: Step 1: what

    private func pick(_ t: WatchTile) {
        draft.tile = t.id
        draft.kind = t.kind
        draft.describe = t.describe
        draft.gaze = t.gaze
        draft.temporal = t.temporal
        if !draft.nameEdited || original == nil { draft.name = "" ; draft.nameEdited = false }
        draft.cooldown = t.kind == .heart ? 300 : 60
    }

    private var watchStep: some View {
        VStack(alignment: .leading, spacing: 24) {
            title("What should your Spidey Sense watch\u{00A0}for?", "Pick one, or describe it in your own words.")
            LazyVGrid(columns: [GridItem(.flexible(), spacing: 14), GridItem(.flexible(), spacing: 14)], spacing: 14) {
                ForEach(WatchTile.all) { t in
                    TileButton(tile: t, selected: draft.tile == t.id) {
                        softHaptic()
                        withAnimation(Theme.motion(reduceMotion)) { pick(t) }
                    }
                }
            }
            VStack(alignment: .leading, spacing: 12) {
                Text("Or describe your own").font(.spCardTitle).foregroundStyle(Theme.ink)
                TextField("e.g. a dog off its leash", text: Binding(
                    get: { draft.kind == .camera && (draft.tile == nil || WatchTile.byID(draft.tile!)?.describe != draft.describe) ? draft.describe : "" },
                    set: { v in
                        draft.describe = v; draft.kind = .camera
                        if draft.tile != nil { withAnimation(Theme.motion(reduceMotion)) { draft.tile = nil } }
                        if !draft.nameEdited { draft.name = "" }
                    }), axis: .vertical)
                    .font(.title3)
                    .lineLimit(1...4)
                    .padding(18)
                    .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .stroke(draft.tile == nil && !draft.trimmedDescribe.isEmpty ? Theme.accent : Theme.ink.opacity(0.15),
                                lineWidth: draft.tile == nil && !draft.trimmedDescribe.isEmpty ? 2 : 1))
                    .accessibilityLabel("Describe what to watch for")
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(["someone walking up behind me", "a bicycle coming toward me", "a dog off its leash",
                                 "a wet floor sign"], id: \.self) { ex in
                            Button {
                                softHaptic()
                                withAnimation(Theme.motion(reduceMotion)) {
                                    draft.tile = nil; draft.kind = .camera; draft.describe = ex
                                    draft.gaze = .unseen; draft.temporal = false
                                    if !draft.nameEdited { draft.name = "" }
                                }
                            } label: {
                                Text(ex).font(.subheadline).foregroundStyle(Theme.ink)
                                    .padding(.horizontal, 14).padding(.vertical, 10)
                                    .background(Theme.card, in: Capsule())
                                    .overlay(Capsule().stroke(Theme.ink.opacity(0.12)))
                            }
                            .buttonStyle(PressableStyle())
                        }
                    }
                }
            }
        }
    }

    // MARK: Step 2: when

    private var whenStep: some View {
        VStack(alignment: .leading, spacing: 20) {
            title("When should I warn you?", "Spidey knows where you're looking, so it can stay quiet about things you've already seen.")
            ChoiceCard(icon: "eye.slash.fill", title: "Only if you haven't noticed it",
                       detail: "If you've already looked at it, Spidey stays quiet.",
                       recommended: true, selected: draft.gaze == .unseen) { setGaze(.unseen) }
            if draft.gaze == .unseen {
                VStack(alignment: .leading, spacing: 12) {
                    Text("After you've looked at it, stay quiet for").font(.spHeadline).foregroundStyle(Theme.ink)
                    HStack(spacing: 10) {
                        ForEach([3.0, 10, 30], id: \.self) { s in
                            Pill(text: "\(Int(s)) seconds", selected: draft.noticeMemory == s) {
                                withAnimation(Theme.motion(reduceMotion)) { draft.noticeMemory = s }
                            }
                        }
                    }
                }
                .padding(.horizontal, 4)
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
            ChoiceCard(icon: "eye.fill", title: "When you look at it",
                       detail: "A buzz to confirm what you're looking at.",
                       selected: draft.gaze == .lookingAt) { setGaze(.lookingAt) }
            ChoiceCard(icon: "bell.fill", title: "Always",
                       detail: "Whenever it's there, whether you've seen it or not.",
                       selected: draft.gaze == .any) { setGaze(.any) }
        }
    }

    private func setGaze(_ g: GazeMode) {
        softHaptic()
        withAnimation(Theme.motion(reduceMotion)) { draft.gaze = g }
    }

    // MARK: Step 3: wait

    private var waitStep: some View {
        VStack(alignment: .leading, spacing: 20) {
            title("Should I wait to be sure?", "Waiting a moment means fewer false alarms for things that move.")
            ChoiceCard(icon: "bolt.fill", title: "Warn right away",
                       detail: "As soon as Spidey sees it.",
                       recommended: !(draft.tile == "water"), selected: !draft.temporal) {
                softHaptic(); withAnimation(Theme.motion(reduceMotion)) { draft.temporal = false }
            }
            ChoiceCard(icon: "arrow.down.right.and.arrow.up.left", title: "Only if it keeps getting closer",
                       detail: "Spidey watches for a couple of seconds and only buzzes if it's clearly coming closer.",
                       recommended: draft.tile == "water", selected: draft.temporal) {
                softHaptic(); withAnimation(Theme.motion(reduceMotion)) { draft.temporal = true }
            }
        }
    }

    // MARK: Step 4: feel (optional)

    private var feelStep: some View {
        VStack(alignment: .leading, spacing: 20) {
            title("How should it feel?", "We've picked one that fits. You can keep it or choose your own.", optional: true)
            if !customizeFeel {
                FeelCard(feel: draft.recommendedFeel, color: Theme.led(TriggerColor.defaultRGB(draft.recommendedFeel)),
                         selected: true, recommended: true, stream: stream, action: nil)
                    .transition(.opacity)
            } else {
                VStack(spacing: 14) {
                    ForEach(["urgent", "pulse", "ambient"], id: \.self) { f in
                        FeelCard(feel: f, color: Theme.led(f == draft.effectiveFeel ? draft.effectiveRGB : TriggerColor.defaultRGB(f)),
                                 selected: draft.effectiveFeel == f, recommended: f == draft.recommendedFeel, stream: stream) {
                            softHaptic()
                            withAnimation(Theme.motion(reduceMotion)) { draft.feel = f; draft.rgb = nil }
                        }
                    }
                }
                VStack(alignment: .leading, spacing: 12) {
                    Text("Light colour").font(.spHeadline).foregroundStyle(Theme.ink)
                    HStack(spacing: 14) {
                        ForEach(Self.swatches, id: \.self) { rgb in
                            let on = draft.effectiveRGB == rgb
                            Button {
                                softHaptic(); withAnimation(Theme.motion(reduceMotion)) { draft.rgb = rgb }
                            } label: {
                                Circle().fill(Theme.led(rgb)).frame(width: 40, height: 40)
                                    .overlay(Circle().stroke(.white, lineWidth: 3).padding(3).opacity(on ? 1 : 0))
                                    .overlay(Circle().stroke(on ? Theme.ink : Theme.hairline, lineWidth: on ? 2 : 1))
                                    .scaleEffect(on ? 1.08 : 1)
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel(Self.swatchName(rgb))
                            .accessibilityAddTraits(on ? .isSelected : [])
                        }
                    }
                }
                .softCard()
                VStack(alignment: .leading, spacing: 10) {
                    Text("Strength").font(.spHeadline).foregroundStyle(Theme.ink)
                    Slider(value: $draft.intensity, in: 0.2...1, step: 0.05)
                        .tint(Theme.accent)
                        .accessibilityValue("\(Int(draft.intensity * 100)) percent")
                    HStack {
                        Text("Softer"); Spacer(); Text("Stronger")
                    }
                    .font(.subheadline).foregroundStyle(Theme.inkSecondary)
                }
                .softCard()
            }
        }
    }

    static let swatches: [[Int]] = [[255, 0, 0], [255, 45, 140], [255, 150, 0], [0, 200, 90], [0, 90, 255], [255, 255, 255]]
    static func swatchName(_ rgb: [Int]) -> String {
        switch rgb {
        case [255, 0, 0]: "Red"; case [255, 45, 140]: "Pink"; case [255, 150, 0]: "Orange"
        case [0, 200, 90]: "Green"; case [0, 90, 255]: "Blue"; default: "White"
        }
    }

    // MARK: Step 5: summary

    @State private var advanced = false

    private var summaryStep: some View {
        VStack(alignment: .leading, spacing: 20) {
            title(original != nil ? "Your Spidey trigger" : "Here's your Spidey trigger")
            Text(draft.sentence)
                .font(.title3).foregroundStyle(Theme.inkSecondary).lineSpacing(5)
                .fixedSize(horizontal: false, vertical: true)
                .softCard(padding: 22)

            VStack(spacing: 0) {
                summaryRow(icon: WatchTile.byID(draft.tile ?? "")?.symbol ?? AlertIcon.symbol(for: draft.describe + " " + draft.finalName), label: "Watching for",
                           value: draft.kind == .camera ? draft.whatPhrase.capitalizedFirst : (WatchTile.byID(draft.tile ?? "")?.title ?? ""),
                           edit: .watch)
                if draft.kind == .camera {
                    Divider().padding(.leading, 52)
                    summaryRow(icon: "eye", label: "Warn me", value: gazeText, edit: .when)
                    Divider().padding(.leading, 52)
                    summaryRow(icon: "timer", label: "Timing", value: draft.temporal ? "When it keeps getting closer" : "Right away", edit: .wait)
                }
                Divider().padding(.leading, 52)
                summaryRow(icon: Plain.feelIcon(draft.effectiveFeel), label: "Feels like",
                           value: Plain.feelName(draft.effectiveFeel) + (draft.feel == nil ? " (recommended)" : ""),
                           edit: .feel, swatch: draft.effectiveRGB)
            }
            .softCard(padding: 6)

            VStack(alignment: .leading, spacing: 10) {
                Text("Name").font(.spHeadline).foregroundStyle(Theme.ink)
                TextField(draft.suggestedName, text: Binding(get: { draft.nameEdited ? draft.name : draft.suggestedName },
                                                             set: { draft.name = $0; draft.nameEdited = true }))
                    .font(.title3)
                    .padding(16)
                    .background(Theme.card, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Theme.ink.opacity(0.15)))
            }

            DisclosureGroup(isExpanded: $advanced.animation(Theme.motion(reduceMotion))) {
                advancedContent.padding(.top, 14)
            } label: {
                Label("Advanced", systemImage: "slider.horizontal.3").font(.spHeadline).foregroundStyle(Theme.ink)
            }
            .tint(Theme.inkSecondary)
            .softCard(padding: 18)
            .id("advanced")

            if original != nil {
                Button(role: .destructive) { confirmDelete = true } label: {
                    Label("Delete this trigger", systemImage: "trash").font(.body.weight(.semibold))
                        .frame(maxWidth: .infinity, minHeight: 48)
                }
                .tint(.red)
                .disabled(!stream.isLive)
                .confirmationDialog("Delete \"\(draft.finalName)\"?", isPresented: $confirmDelete, titleVisibility: .visible) {
                    Button("Delete trigger", role: .destructive) {
                        if let id = original?.id { stream.deleteRule(id: id) }
                        dismiss()
                    }
                    Button("Cancel", role: .cancel) {}
                }
            }
        }
        .onAppear {
            if UserDefaults.standard.bool(forKey: "wizardAdvanced") {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { advanced = true }
            }
        }
    }

    private var gazeText: String {
        switch draft.gaze {
        case .unseen: "Only if I haven't noticed it"
        case .lookingAt: "When I look at it"
        case .any: "Always"
        }
    }

    private func summaryRow(icon: String, label: String, value: String, edit: Step, swatch: [Int]? = nil) -> some View {
        Button {
            softHaptic()
            returnToSummary = true
            if edit == .feel { customizeFeel = true; draft.feel = draft.effectiveFeel }
            go(to: edit)
        } label: {
            HStack(spacing: 12) {
                Image(systemName: icon).font(.body).foregroundStyle(Theme.inkSecondary).frame(width: 28)
                VStack(alignment: .leading, spacing: 2) {
                    Text(label).font(.footnote).foregroundStyle(Theme.inkSecondary)
                    HStack(spacing: 6) {
                        if let swatch { Circle().fill(Theme.led(swatch)).frame(width: 10, height: 10) }
                        Text(value).font(.body).foregroundStyle(Theme.ink).lineLimit(2).multilineTextAlignment(.leading)
                    }
                }
                Spacer(minLength: 8)
                Text("Edit").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.accent)
            }
            .padding(.horizontal, 12).padding(.vertical, 12)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(label): \(value)")
        .accessibilityHint("Edit")
    }

    @ViewBuilder private var advancedContent: some View {
        VStack(alignment: .leading, spacing: 20) {
            if draft.kind == .camera {
                advancedGroup("How sure should Spidey be?") {
                    VStack(spacing: 8) {
                        ForEach([(0.0, "Tell me about anything"), (0.5, "Only if it seems important (recommended)"),
                                 (0.8, "Only if it's urgent")], id: \.0) { v, t in
                            RadioRow(text: t, selected: abs(draft.minUrgency - v) < 0.01) {
                                withAnimation(Theme.motion(reduceMotion)) { draft.minUrgency = v }
                            }
                        }
                    }
                }
            }
            advancedGroup("Don't repeat for") {
                HStack(spacing: 8) {
                    ForEach([(10.0, "10 s"), (60, "1 min"), (300, "5 min")], id: \.0) { v, t in
                        Pill(text: t, selected: draft.cooldown == v) { withAnimation(Theme.motion(reduceMotion)) { draft.cooldown = v } }
                    }
                }
                if ![10.0, 60, 300].contains(draft.cooldown) {
                    Text("Currently \(RuleText.fmt(draft.cooldown)) s").font(.footnote).foregroundStyle(Theme.inkSecondary)
                }
            }
            advancedGroup("Which Dot") {
                HStack(spacing: 8) {
                    ForEach([(Int?.none, "All"), (1, "Left"), (2, "Right")], id: \.1) { v, t in
                        Pill(text: t, selected: draft.dot == v) { withAnimation(Theme.motion(reduceMotion)) { draft.dot = v } }
                    }
                }
            }
            switch draft.kind {
            case .camera:
                advancedGroup("Exact description sent to the AI") {
                    TextField("Description", text: $draft.describe, axis: .vertical)
                        .font(.callout.monospaced()).lineLimit(1...5)
                        .padding(12)
                        .background(Theme.ink.opacity(0.04), in: RoundedRectangle(cornerRadius: 10))
                }
            case .radar:
                advancedGroup("Within \(String(format: "%.1f", draft.maxDistance)) m") {
                    Slider(value: $draft.maxDistance, in: 0.5...4, step: 0.1).tint(Theme.accent)
                }
                advancedGroup("Moving faster than \(String(format: "%.2f", draft.minSpeed)) m/s") {
                    Slider(value: $draft.minSpeed, in: 0.1...1.5, step: 0.05).tint(Theme.accent)
                }
            case .heart:
                advancedGroup("Heart-rate thresholds") {
                    Stepper("Above my normal by \(Int(draft.hrDelta)) bpm", value: $draft.hrDelta, in: 5...40)
                    Stepper("or by \(Int(draft.hrPct))%", value: $draft.hrPct, in: 5...50)
                    Stepper("For at least \(Int(draft.hrSustain)) s", value: $draft.hrSustain, in: 3...30)
                }
            }
            Text("Sensor: " + (draft.kind == .camera ? "glasses camera (neon)" : draft.kind == .radar ? "radar" : "heart-rate ring (heart_rate)")
                 + (original.map { " · id \($0.id)" } ?? ""))
                .font(.footnote).foregroundStyle(Theme.inkTertiary)
        }
    }

    private func advancedGroup<C: View>(_ t: String, @ViewBuilder _ c: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(t).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.ink)
            c()
        }
    }

    private func save() {
        let rule = draft.build(existingIDs: Set(stream.rules.map(\.id)))
        if stream.isLive || !demoSave { stream.upsert(rule) }   // the offline demo only shows the success screen
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        withAnimation(Theme.motion(reduceMotion)) { saved = true }
    }
}

// MARK: - Wizard pieces

struct TileButton: View {
    let tile: WatchTile
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 16, style: .continuous).fill(tile.tint.opacity(0.13))
                    Image(systemName: tile.symbol).font(.system(size: 38, weight: .semibold)).foregroundStyle(tile.tint)
                    if let b = tile.badge {
                        Image(systemName: b).font(.system(size: 17, weight: .bold)).foregroundStyle(tile.tint)
                            .padding(7).background(.white, in: Circle())
                            .offset(x: 30, y: 22)
                    }
                }
                .frame(height: 92)
                Text(tile.title).font(.spHeadline).foregroundStyle(Theme.ink)
                    .multilineTextAlignment(.center).lineLimit(2, reservesSpace: true)
            }
            .padding(12)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                .stroke(selected ? Theme.accent : Theme.hairline, lineWidth: selected ? 2.5 : 1))
            .overlay(alignment: .topTrailing) {
                if selected {
                    Image(systemName: "checkmark.circle.fill").font(.title2).foregroundStyle(.white, Theme.accent)
                        .padding(8).transition(.scale.combined(with: .opacity))
                }
            }
            .shadow(color: .black.opacity(selected ? 0.10 : 0.05), radius: selected ? 16 : 10, y: 5)
            .scaleEffect(selected ? 1.02 : 1)
        }
        .buttonStyle(PressableStyle())
        .accessibilityLabel(tile.title)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

struct ChoiceCard: View {
    let icon: String
    let title: String
    let detail: String
    var recommended = false
    let selected: Bool
    let action: () -> Void
    @Environment(\.dynamicTypeSize) private var dynamicType

    var body: some View {
        Button(action: action) {
            Group {
                if dynamicType.isAccessibilitySize {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack { iconTile; Spacer(); check }
                        text
                    }
                } else {
                    HStack(alignment: .top, spacing: 16) {
                        iconTile
                        text
                        Spacer(minLength: 0)
                        check
                    }
                }
            }
            .padding(18)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(selected ? Theme.accent.opacity(0.05) : Theme.card,
                        in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                .stroke(selected ? Theme.accent : Theme.hairline, lineWidth: selected ? 2 : 1))
            .shadow(color: .black.opacity(0.05), radius: 10, y: 4)
            .scaleEffect(selected ? 1.0 : 0.99)
        }
        .buttonStyle(PressableStyle())
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
    }

    private var iconTile: some View {
        Image(systemName: icon).font(.system(size: 24, weight: .semibold))
            .foregroundStyle(selected ? .white : Theme.ink)
            .frame(width: 54, height: 54)
            .background(selected ? Theme.accent : Theme.ink.opacity(0.06),
                        in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private var check: some View {
        Image(systemName: selected ? "checkmark.circle.fill" : "circle")
            .font(.title2).foregroundStyle(selected ? Theme.accent : Theme.ink.opacity(0.2))
    }

    private var text: some View {
        VStack(alignment: .leading, spacing: 6) {
            if recommended { RecommendedBadge() }
            Text(title).font(.spCardTitle).foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
            Text(detail).font(.body).foregroundStyle(Theme.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct RecommendedBadge: View {
    var body: some View {
        Text("Recommended").font(.caption.weight(.bold)).foregroundStyle(Theme.accent)
            .lineLimit(1).fixedSize()
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(Theme.accentSoft, in: Capsule())
    }
}

struct Pill: View {
    let text: String
    let selected: Bool
    let action: () -> Void
    var body: some View {
        Button { softHaptic(); action() } label: {
            Text(text).font(.body.weight(.semibold))
                .foregroundStyle(selected ? .white : Theme.ink)
                .frame(maxWidth: .infinity, minHeight: 48)
                .background(selected ? Theme.ink : Theme.card, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(selected ? .clear : Theme.ink.opacity(0.15)))
        }
        .buttonStyle(PressableStyle())
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

struct RadioRow: View {
    let text: String
    let selected: Bool
    let action: () -> Void
    var body: some View {
        Button { softHaptic(); action() } label: {
            HStack(spacing: 12) {
                Image(systemName: selected ? "largecircle.fill.circle" : "circle")
                    .font(.title3).foregroundStyle(selected ? Theme.accent : Theme.ink.opacity(0.25))
                Text(text).font(.body).foregroundStyle(Theme.ink).multilineTextAlignment(.leading)
                Spacer()
            }
            .padding(.vertical, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

/// A feel option with a "Try it" button: plays the preset on the Dot (if connected) and on the phone.
struct FeelCard: View {
    let feel: String
    let color: Color
    let selected: Bool
    var recommended = false
    let stream: EventStream
    let action: (() -> Void)?

    @State private var playing = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            ZStack {
                Circle().fill(color.opacity(0.15)).frame(width: 56, height: 56)
                Circle().stroke(color.opacity(0.5), lineWidth: 2).frame(width: 56, height: 56)
                    .scaleEffect(playing ? 1.45 : 1).opacity(playing ? 0 : (selected ? 0.8 : 0))
                Image(systemName: Plain.feelIcon(feel)).font(.system(size: 22, weight: .semibold)).foregroundStyle(color)
                    .scaleEffect(playing ? 1.15 : 1)
            }
            VStack(alignment: .leading, spacing: 4) {
                if recommended { RecommendedBadge() }
                Text(Plain.feelName(feel)).font(.spCardTitle).foregroundStyle(Theme.ink)
                Text(Plain.feelBlurb(feel)).font(.subheadline).foregroundStyle(Theme.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                Button(action: tryIt) {
                    Label(playing ? "Playing…" : "Try it", systemImage: playing ? "waveform" : "play.fill")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Theme.ink)
                        .padding(.horizontal, 16).frame(minHeight: 40)
                        .background(Theme.card, in: Capsule())
                        .overlay(Capsule().stroke(Theme.ink.opacity(0.15)))
                        .symbolEffect(.variableColor.iterative, isActive: playing && !reduceMotion)
                }
                .buttonStyle(PressableStyle())
                .padding(.top, 6)
                .accessibilityLabel("Try \(Plain.feelName(feel))")
            }
            Spacer(minLength: 0)
        }
        .padding(18)
        .background(selected ? Theme.accent.opacity(0.05) : Theme.card,
                    in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
            .stroke(selected ? Theme.accent : Theme.hairline, lineWidth: selected ? 2 : 1))
        .shadow(color: .black.opacity(0.05), radius: 10, y: 4)
        .contentShape(RoundedRectangle(cornerRadius: Theme.radius))
        .onTapGesture { action?() }
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func tryIt() {
        if stream.isLive { stream.haptic(HapticPreset(name: feel), label: "haptic.preset \(feel)") }
        PhoneHaptics.play(feel)
        guard !reduceMotion else { return }
        withAnimation(.easeOut(duration: 0.8).repeatCount(2, autoreverses: false)) { playing = true }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) { withAnimation(Theme.spring) { playing = false } }
    }
}

/// An on-phone approximation of the three Dot patterns.
enum PhoneHaptics {
    static func play(_ feel: String) {
        switch feel {
        case "urgent":
            let g = UIImpactFeedbackGenerator(style: .heavy)
            for i in 0..<3 { DispatchQueue.main.asyncAfter(deadline: .now() + Double(i) * 0.12) { g.impactOccurred(intensity: 1) } }
        case "pulse":
            let g = UIImpactFeedbackGenerator(style: .medium)
            g.impactOccurred(); DispatchQueue.main.asyncAfter(deadline: .now() + 0.18) { g.impactOccurred(intensity: 0.7) }
        default:
            let g = UIImpactFeedbackGenerator(style: .soft)
            for i in 0..<4 { DispatchQueue.main.asyncAfter(deadline: .now() + Double(i) * 0.09) { g.impactOccurred(intensity: 0.4) } }
        }
    }
}

// MARK: - Success

struct SuccessView: View {
    let sentence: String
    let name: String
    let editing: Bool
    let done: () -> Void

    @State private var ring: CGFloat = 0
    @State private var check: CGFloat = 0
    @State private var textIn = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: 28) {
            Spacer()
            ZStack {
                Circle().fill(Theme.good.opacity(0.10)).frame(width: 140, height: 140)
                Circle().trim(from: 0, to: ring)
                    .stroke(Theme.good, style: StrokeStyle(lineWidth: 6, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .frame(width: 108, height: 108)
                CheckShape().trim(from: 0, to: check)
                    .stroke(Theme.good, style: StrokeStyle(lineWidth: 8, lineCap: .round, lineJoin: .round))
                    .frame(width: 48, height: 40)
            }
            .accessibilityHidden(true)
            VStack(spacing: 12) {
                Text(editing ? "Saved" : "Spidey Sense improved!").font(.spLargeTitle).foregroundStyle(Theme.ink)
                    .multilineTextAlignment(.center).minimumScaleFactor(0.85)
                    .fixedSize(horizontal: false, vertical: true)
                Text(sentence).font(.title3).foregroundStyle(Theme.inkSecondary)
                    .multilineTextAlignment(.center).lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 28)
            .opacity(textIn ? 1 : 0).offset(y: textIn ? 0 : 10)
            Spacer()
            Button(action: done) { Text("Done") }
                .buttonStyle(PrimaryButtonStyle())
                .padding(.horizontal, 20).padding(.bottom, 12)
        }
        .onAppear {
            if reduceMotion { ring = 1; check = 1; textIn = true; return }
            withAnimation(.easeOut(duration: 0.45)) { ring = 1 }
            withAnimation(.easeOut(duration: 0.3).delay(0.35)) { check = 1 }
            withAnimation(Theme.spring.delay(0.45)) { textIn = true }
        }
        .accessibilityElement(children: .contain)
    }
}

struct CheckShape: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: r.minX, y: r.midY + r.height * 0.05))
        p.addLine(to: CGPoint(x: r.minX + r.width * 0.36, y: r.maxY))
        p.addLine(to: CGPoint(x: r.maxX, y: r.minY))
        return p
    }
}

extension String {
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}
