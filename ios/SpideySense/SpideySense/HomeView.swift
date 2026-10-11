import SwiftUI

// MARK: - Home ("Now")

/// "I see a lion, I get a buzz, I open my phone and see why." Buzzes from the last 5 minutes are big,
/// text-first cards (repeats collapse into one card, see EventGroup); older ones sit in a plain list.
struct HomeView: View {
    let stream: EventStream
    var openSettings: () -> Void = {}

    @State private var now = Date()
    @State private var selected: EventGroup?
    @State private var demoExtra: [HapticEvent] = []   // `-demoArrive YES` (sample mode only)
    @State private var expandHero = false
    @Namespace private var ns
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicType

    private var showingSample: Bool { stream.events.isEmpty && !stream.isLive }
    private var groups: [EventGroup] {
        if UserDefaults.standard.bool(forKey: "forceEmpty") { return [] }   // screenshot hook for the empty state
        return EventGroup.build(showingSample ? demoExtra + MockData.events : stream.events)
    }

    var body: some View {
        let all = groups
        let fresh = all.filter { $0.isFresh(now: now) }
        let earlier = all.filter { !$0.isFresh(now: now) }
        NavigationStack {
          ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    header
                    if all.isEmpty {
                        EmptyHome().padding(.top, 40)
                    } else {
                        if !fresh.isEmpty {
                            VStack(spacing: 16) {
                                ForEach(Array(fresh.enumerated()), id: \.element.id) { i, g in
                                    FreshCard(group: g, hero: i == 0, now: now, accent: color(g), expand: i == 0 && expandHero) { selected = g }
                                        .matchedGeometryEffect(id: g.id, in: ns)
                                        .transition(insertion)
                                }
                            }
                        } else {
                            QuietNow().transition(.opacity)
                        }
                        if !earlier.isEmpty {
                            VStack(alignment: .leading, spacing: 12) {
                                SectionTitle(text: "Earlier")
                                VStack(spacing: 0) {
                                    ForEach(Array(earlier.enumerated()), id: \.element.id) { i, g in
                                        if i > 0 { Divider().padding(.leading, 72) }
                                        EarlierRow(group: g, now: now, accent: color(g)) { selected = g }
                                            .matchedGeometryEffect(id: g.id, in: ns)
                                            .transition(.opacity)
                                    }
                                }
                                .softCard(padding: 0)
                            }
                            .id("earlier")
                        }
                        if showingSample {
                            Text("These are examples. When your Spidey hub is connected, your real alerts show up here.")
                                .font(.footnote).foregroundStyle(Theme.inkSecondary)
                                .frame(maxWidth: .infinity, alignment: .center).multilineTextAlignment(.center)
                                .padding(.horizontal, 12)
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.bottom, 32)
                .animation(Theme.motion(reduceMotion), value: all.map(\.id))
                .animation(Theme.motion(reduceMotion), value: fresh.map(\.id))
            }
            .scrollIndicators(.hidden)
            .background(Theme.background.ignoresSafeArea())
            .toolbar(.hidden, for: .navigationBar)
            .statusBarBackdrop()
            .sheet(item: $selected) { g in
                EventGroupDetail(stream: stream, groupID: g.id, fallback: g, extra: demoExtra)
            }
            .task {
                // Refresh "12 s ago" and move cards past 5 min into Earlier (animated).
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(5))
                    withAnimation(Theme.motion(reduceMotion)) { now = Date() }
                }
            }
            .onChange(of: stream.events.first?.key) { _, _ in now = Date() }
            .onAppear {
                // Screenshot/demo hooks: `-openEvent first` opens the newest card's detail sheet,
                // `-expandPhoto YES` opens the top card's picture, `-homeScroll earlier` scrolls to Earlier.
                let d = UserDefaults.standard
                if d.string(forKey: "openEvent") != nil {   // after a moment, so a live engine's history is in
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { selected = groups.first }
                }
                if d.bool(forKey: "expandPhoto") { expandHero = true }
                if d.string(forKey: "homeScroll") == "earlier" {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { proxy.scrollTo("earlier", anchor: .top) }
                }
            }
            .task { if UserDefaults.standard.bool(forKey: "demoArrive") { await demoArrive() } }
          }
        }
    }

    /// Demo for the screen recording (sample mode): a new alert arrives, then the lion buzzes again (count bump),
    /// then the top card's picture opens, then the detail sheet.
    private func demoArrive() async {
        try? await Task.sleep(for: .seconds(2.5))
        if let e = MockData.demoEvent("step", id: 990) { withAnimation(Theme.motion(reduceMotion)) { demoExtra.insert(e, at: 0); now = Date() } }
        try? await Task.sleep(for: .seconds(3))
        if let e = MockData.demoEvent("lion", id: 991) { withAnimation(Theme.motion(reduceMotion)) { demoExtra.insert(e, at: 0); now = Date() } }
        try? await Task.sleep(for: .seconds(2.5))
        expandHero = true
        try? await Task.sleep(for: .seconds(3))
        selected = groups.first
    }

    private var insertion: AnyTransition {
        reduceMotion ? .opacity : .asymmetric(insertion: .scale(scale: 0.96, anchor: .top).combined(with: .opacity),
                                               removal: .opacity)
    }

    private func color(_ g: EventGroup) -> Color {
        ledColor(for: g.latest.triggerID, pattern: g.latest.pattern, rules: stream.rules)
    }

    /// Status row on top (pill + sample tag), then the big "Spidey Sense" title on its own line, so the title
    /// never has to share its width with the pill (no wrapping at any text size).
    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Button(action: openSettings) { StatusPill(status: stream.status, sample: showingSample) }
                    .buttonStyle(PressableStyle())
                if showingSample { SampleTag() }
                Spacer(minLength: 0)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Spidey Sense").font(.spLargeTitle).foregroundStyle(Theme.ink)
                    .lineLimit(1).minimumScaleFactor(0.85)
                    .accessibilityAddTraits(.isHeader)
                Text(subtitle).font(.body).foregroundStyle(Theme.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.top, 8)
    }

    private var subtitle: String {
        switch stream.status {
        case .connected:
            if stream.paused { return "Paused: we won't buzz you" }
            if stream.settings.quiet { return "Quiet mode: no buzzing" }
            let recent = groups.filter { $0.isFresh(now: now) }.reduce(0) { $0 + $1.count }
            return recent > 0 ? "Your Spidey Sense tingled \(recent == 1 ? "once" : "\(recent) times") recently" : "Your Spidey Sense is on"
        case .connecting: return "Looking for your Spidey hub…"
        case .disconnected: return showingSample ? "Showing examples" : "Can't reach your Spidey hub"
        }
    }
}

// MARK: - Fresh card (last 5 minutes)

struct FreshCard: View {
    let group: EventGroup
    let hero: Bool
    let now: Date
    let accent: Color
    var expand = false            // demo / screenshot hook: open the picture
    let open: () -> Void

    @State private var showPhoto = false
    @State private var bump = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var e: HapticEvent { group.latest }

    private var timeLabel: some View {
        Label(Plain.ago(group.lastDate, now: now), systemImage: "clock")
            .font(.subheadline).foregroundStyle(Theme.inkSecondary)
            .lineLimit(1).fixedSize()
    }

    private var countBadge: some View {
        Text(Plain.count(group.count))
            .font(.subheadline.weight(.semibold))
            .lineLimit(1).fixedSize()
            .foregroundStyle(group.count > 1 ? Theme.accent : Theme.inkSecondary)
            .contentTransition(.numericText(value: Double(group.count)))
            .padding(.horizontal, 10).padding(.vertical, 5)
            .background(group.count > 1 ? Theme.accentSoft : Theme.ink.opacity(0.05), in: Capsule())
            .scaleEffect(bump ? 1.12 : 1)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if hero && now.timeIntervalSince(group.lastDate) < 120 {
                Label("Spidey Sense tingled!", systemImage: "sparkles")
                    .font(.caption.weight(.heavy)).textCase(.uppercase).tracking(0.6)
                    .foregroundStyle(Theme.accent)
                    .accessibilityLabel("Spidey Sense tingled")
            }
            HStack(spacing: 8) {
                Circle().fill(accent).frame(width: 10, height: 10)
                Text(e.alertName).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.inkSecondary)
                    .lineLimit(1)
                Spacer(minLength: 8)
                if group.snapshotEvent != nil {
                    Button {
                        softHaptic()
                        withAnimation(Theme.motion(reduceMotion)) { showPhoto.toggle() }
                    } label: {
                        Image(systemName: showPhoto ? "photo.fill" : "photo")
                            .font(.body.weight(.medium))
                            .foregroundStyle(showPhoto ? .white : Theme.ink)
                            .frame(width: 40, height: 40)
                            .background(showPhoto ? Theme.ink : Theme.ink.opacity(0.06), in: Circle())
                    }
                    .buttonStyle(PressableStyle())
                    .accessibilityLabel(showPhoto ? "Hide what Spidey saw" : "See what Spidey saw")
                }
            }
            Text(e.headline)
                .font(hero ? .spTitle2 : .spCardTitle)
                .foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
                .lineSpacing(2)
            if e.cleanWhy != nil {
                Text(e.plainWhy)
                    .font(.body).foregroundStyle(Theme.inkSecondary)
                    .lineLimit(hero ? 3 : 2).lineSpacing(3)
            }
            if showPhoto, let s = group.snapshotEvent, let img = s.thumb {
                SnapshotImage(image: img)
                    .transition(reduceMotion ? .opacity : .opacity.combined(with: .scale(scale: 0.97, anchor: .top)))
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) {
                    timeLabel
                    Spacer(minLength: 4)
                    if group.count > 1 { MiniTimeline(events: group.events.prefix(8).map { $0 }, color: accent).frame(width: 44, height: 12) }
                    countBadge
                }
                VStack(alignment: .leading, spacing: 10) {
                    timeLabel
                    countBadge
                }
            }
        }
        .softCard(padding: hero ? 22 : 20)
        .overlay(
            RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                .stroke(Theme.accent.opacity(bump ? 0.6 : 0), lineWidth: 2)
        )
        .contentShape(RoundedRectangle(cornerRadius: Theme.radius))
        .onTapGesture(perform: open)
        .accessibilityElement(children: .contain)
        .accessibilityAction(named: "Open details", open)
        .onAppear { if expand { showPhoto = true } }
        .onChange(of: expand) { _, on in if on { withAnimation(Theme.motion(reduceMotion)) { showPhoto = true } } }
        .onChange(of: group.count) { _, _ in
            guard !reduceMotion else { return }
            withAnimation(.spring(response: 0.25, dampingFraction: 0.5)) { bump = true }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { withAnimation(Theme.spring) { bump = false } }
        }
    }
}

/// Dots on a line, one per buzz, spaced by time (oldest left).
struct MiniTimeline: View {
    let events: [HapticEvent]
    let color: Color
    var body: some View {
        Canvas { ctx, size in
            let ts = events.map(\.ts)
            guard let lo = ts.min(), let hi = ts.max() else { return }
            let mid = size.height / 2
            var line = Path(); line.move(to: CGPoint(x: 4, y: mid)); line.addLine(to: CGPoint(x: size.width - 4, y: mid))
            ctx.stroke(line, with: .color(Theme.ink.opacity(0.12)), lineWidth: 2)
            for t in ts {
                let x = hi > lo ? 4 + (t - lo) / (hi - lo) * (size.width - 8) : size.width / 2
                ctx.fill(Path(ellipseIn: CGRect(x: x - 4, y: mid - 4, width: 8, height: 8)), with: .color(color))
            }
        }
        .accessibilityHidden(true)
    }
}

/// The frame that fired, with the gaze dot and the box the engine drew.
struct SnapshotImage: View {
    let image: UIImage
    var caption = true
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(uiImage: image).resizable().scaledToFit()
                .frame(maxWidth: .infinity)
                .clipShape(RoundedRectangle(cornerRadius: Theme.smallRadius, style: .continuous))
                .accessibilityLabel("What the glasses saw. A dot shows where you were looking and a box shows what Spidey noticed.")
            if caption {
                Label("Red dot: where you were looking. Box: what Spidey noticed.", systemImage: "info.circle")
                    .font(.footnote).foregroundStyle(Theme.inkSecondary)
            }
        }
    }
}

// MARK: - Earlier row

struct EarlierRow: View {
    let group: EventGroup
    let now: Date
    let accent: Color
    let open: () -> Void
    private var e: HapticEvent { group.latest }

    var body: some View {
        Button(action: open) {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: icon)
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(accent)
                    .frame(width: 42, height: 42)
                    .background(accent.opacity(0.12), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(e.alertName).font(.body.weight(.semibold)).foregroundStyle(Theme.ink).lineLimit(1)
                        Spacer(minLength: 4)
                        Text(Plain.ago(group.lastDate, now: now)).font(.footnote).foregroundStyle(Theme.inkSecondary)
                            .fixedSize()
                    }
                    Text(e.headline).font(.subheadline).foregroundStyle(Theme.inkSecondary).lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                    if group.count > 1 || group.snapshotEvent != nil {
                        HStack(spacing: 10) {
                            if group.count > 1 {
                                Text(Plain.count(group.count)).font(.caption.weight(.semibold)).foregroundStyle(Theme.accent)
                            }
                            if group.snapshotEvent != nil {
                                Label("Picture", systemImage: "photo").font(.caption).foregroundStyle(Theme.inkTertiary)
                                    .accessibilityLabel("Has a picture")
                            }
                        }
                        .padding(.top, 2)
                    }
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 14)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressableStyle())
    }

    private var icon: String {
        if e.isRadar { return "figure.walk.arrival" }
        if e.isHeart { return "heart.fill" }
        return AlertIcon.symbol(for: [e.triggerLabel, e.hazard ?? "", e.why ?? ""].joined(separator: " "))
    }
}

// MARK: - Empty states

struct EmptyHome: View {
    var body: some View {
        VStack(spacing: 20) {
            ZStack {
                Circle().fill(Theme.accentSoft).frame(width: 150, height: 150)
                Circle().fill(Theme.accent.opacity(0.07)).frame(width: 110, height: 110)
                Image(systemName: "eyeglasses").font(.system(size: 54, weight: .medium)).foregroundStyle(Theme.accent)
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 34)).foregroundStyle(.white, Theme.good)
                    .offset(x: 48, y: 44)
            }
            .accessibilityHidden(true)
            VStack(spacing: 8) {
                Text("Your Spidey Sense is on.").font(.spTitle2).foregroundStyle(Theme.ink)
                Text("We'll tap you if something needs your attention.")
                    .font(.body).foregroundStyle(Theme.inkSecondary).multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 24)
    }
}

/// Shown above "Earlier" when nothing happened in the last 5 minutes.
struct QuietNow: View {
    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: "checkmark.circle.fill").font(.system(size: 30)).foregroundStyle(.white, Theme.good)
            VStack(alignment: .leading, spacing: 2) {
                Text("All quiet").font(.spHeadline).foregroundStyle(Theme.ink)
                Text("Nothing needed your attention in the last 5 minutes.")
                    .font(.subheadline).foregroundStyle(Theme.inkSecondary)
            }
        }
        .softCard()
    }
}

// MARK: - Detail sheet

struct EventGroupDetail: View {
    let stream: EventStream
    let groupID: String
    let fallback: EventGroup
    var extra: [HapticEvent] = []   // demo events (sample mode)

    @State private var showPhoto = false
    @State private var showTech = false
    @State private var photoIndex = 0
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// Live: keeps up with new buzzes in this group while the sheet is open.
    private var group: EventGroup {
        let src = stream.events.isEmpty && !stream.isLive ? extra + MockData.events : stream.events
        return EventGroup.build(src).first { $0.id == groupID } ?? fallback
    }
    private var e: HapticEvent { group.latest }
    private var accent: Color { ledColor(for: e.triggerID, pattern: e.pattern, rules: stream.rules) }

    var body: some View {
        let g = group
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(spacing: 8) {
                            Circle().fill(accent).frame(width: 10, height: 10)
                            Text(e.alertName).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.inkSecondary)
                            if g.isSample { SampleTag() }
                        }
                        Text(e.headline).font(.spTitle).foregroundStyle(Theme.ink)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("\(Plain.clock(g.lastDate)) · \(Plain.ago(g.lastDate))")
                            .font(.subheadline).foregroundStyle(Theme.inkSecondary)
                    }

                    if g.snapshotEvent != nil { photoCard(g) }

                    VStack(alignment: .leading, spacing: 10) {
                        Label("Why it buzzed", systemImage: "questionmark.bubble").font(.spHeadline).foregroundStyle(Theme.ink)
                        Text(e.plainWhy).font(.body).foregroundStyle(Theme.ink).lineSpacing(3)
                            .fixedSize(horizontal: false, vertical: true)
                        if let s = e.lookedAtSAgo, e.gazeMode == "unseen" {
                            Text("You last looked at it \(Int(s.rounded())) s before the buzz.")
                                .font(.subheadline).foregroundStyle(Theme.inkSecondary)
                        }
                    }
                    .softCard()

                    timelineCard(g)

                    VStack(spacing: 0) {
                        infoRow("Alert", e.alertName, icon: "bell.fill")
                        Divider().padding(.leading, 44)
                        infoRow("Sensor", Plain.sensorName(e).name, icon: Plain.sensorName(e).icon)
                        Divider().padding(.leading, 44)
                        infoRow("Felt as", Plain.feelName(e.pattern) + (e.quiet || e.paused ? " (not played)" : ""),
                                icon: Plain.feelIcon(e.pattern))
                    }
                    .softCard(padding: 6)

                    feedbackCard

                    DisclosureGroup(isExpanded: $showTech.animation(Theme.motion(reduceMotion))) {
                        VStack(alignment: .leading, spacing: 12) {
                            if let ms = e.latencyMs {
                                Text("Latency \(Int(ms.rounded())) ms · event #\(e.id)" + (e.urgency.map { String(format: " · urgency %.2f", $0) } ?? ""))
                                    .font(.footnote.monospacedDigit()).foregroundStyle(Theme.inkSecondary)
                            }
                            if e.haikuRaw != nil || e.callID != nil { HaikuRawSection(callID: e.callID, raw: e.haikuRaw) }
                            Text(e.prettyJSON)
                                .font(.system(.caption, design: .monospaced))
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(10)
                                .background(Theme.ink.opacity(0.04), in: RoundedRectangle(cornerRadius: 10))
                        }
                        .padding(.top, 10)
                    } label: {
                        Label("Technical details", systemImage: "chevron.left.forwardslash.chevron.right")
                            .font(.subheadline.weight(.medium)).foregroundStyle(Theme.inkSecondary)
                    }
                    .tint(Theme.inkSecondary)
                    .padding(.horizontal, 4)
                }
                .padding(20)
                .animation(Theme.motion(reduceMotion), value: g.count)
            }
            .background(Theme.background.ignoresSafeArea())
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.font(.spHeadline).tint(Theme.ink)
                }
            }
            .onAppear {
                if UserDefaults.standard.bool(forKey: "openPhoto") { showPhoto = true }
                if UserDefaults.standard.bool(forKey: "openTech") { showTech = true }
            }
            .task {
                guard UserDefaults.standard.bool(forKey: "demoArrive") else { return }
                try? await Task.sleep(for: .seconds(1.2))
                withAnimation(Theme.motion(reduceMotion)) { showPhoto = true }
            }
        }
        .presentationDragIndicator(.visible)
    }

    private func photoCard(_ g: EventGroup) -> some View {
        let shots = g.snapshots
        return VStack(alignment: .leading, spacing: 14) {
            Button {
                softHaptic()
                withAnimation(Theme.motion(reduceMotion)) { showPhoto.toggle() }
            } label: {
                HStack {
                    Label("See what Spidey saw", systemImage: "photo").font(.spHeadline).foregroundStyle(Theme.ink)
                    Spacer()
                    Image(systemName: "chevron.down").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.inkSecondary)
                        .rotationEffect(.degrees(showPhoto ? 180 : 0))
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if showPhoto, !shots.isEmpty {
                let i = min(photoIndex, shots.count - 1)
                VStack(alignment: .leading, spacing: 10) {
                    if let img = shots[i].thumb { SnapshotImage(image: img) }
                    if shots.count > 1 {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 8) {
                                ForEach(Array(shots.enumerated()), id: \.element.key) { j, s in
                                    Button { withAnimation(Theme.motion(reduceMotion)) { photoIndex = j } } label: {
                                        VStack(spacing: 4) {
                                            if let t = s.thumb {
                                                Image(uiImage: t).resizable().scaledToFill()
                                                    .frame(width: 64, height: 44).clipped()
                                                    .clipShape(RoundedRectangle(cornerRadius: 8))
                                                    .overlay(RoundedRectangle(cornerRadius: 8)
                                                        .stroke(j == i ? Theme.accent : .clear, lineWidth: 2))
                                            }
                                            Text(Plain.clockSeconds(s.date)).font(.caption2.monospacedDigit())
                                                .foregroundStyle(j == i ? Theme.ink : Theme.inkSecondary)
                                        }
                                    }
                                    .buttonStyle(.plain)
                                    .accessibilityLabel("Snapshot at \(Plain.clockSeconds(s.date))")
                                }
                            }
                        }
                    }
                }
                .transition(reduceMotion ? .opacity : .opacity.combined(with: .scale(scale: 0.97, anchor: .top)))
            }
        }
        .softCard()
    }

    private func timelineCard(_ g: EventGroup) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label(Plain.count(g.count), systemImage: "waveform.path").font(.spHeadline).foregroundStyle(Theme.ink)
                    .contentTransition(.numericText(value: Double(g.count)))
                Spacer()
            }
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(g.events.enumerated()), id: \.element.key) { i, ev in
                    HStack(alignment: .top, spacing: 12) {
                        VStack(spacing: 0) {
                            Circle().fill(i == 0 ? accent : accent.opacity(0.45)).frame(width: 10, height: 10).padding(.top, 5)
                            if i < g.count - 1 { Rectangle().fill(Theme.hairline).frame(width: 2).frame(maxHeight: .infinity) }
                        }
                        .frame(width: 12)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(Plain.clockSeconds(ev.date)).font(.body.monospacedDigit()).foregroundStyle(Theme.ink)
                            Text(gapText(g, i)).font(.footnote).foregroundStyle(Theme.inkSecondary)
                        }
                        .padding(.bottom, 12)
                        Spacer()
                    }
                    .transition(.opacity)
                }
            }
            Text("Repeat buzzes from the same alert within a minute of each other are grouped here.")
                .font(.footnote).foregroundStyle(Theme.inkTertiary)
        }
        .softCard()
    }

    private func gapText(_ g: EventGroup, _ i: Int) -> String {
        if i == g.count - 1 { return g.count == 1 ? Plain.ago(g.events[i].date) : "First buzz" }
        let d = g.events[i].ts - g.events[i + 1].ts
        let gap = d < 60 ? "\(Int(d.rounded())) s later" : "\(Int((d / 60).rounded())) min later"
        return i == 0 ? "\(gap) · latest" : gap
    }

    private func infoRow(_ k: String, _ v: String, icon: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon).font(.body).foregroundStyle(Theme.inkSecondary).frame(width: 26)
            Text(k).foregroundStyle(Theme.inkSecondary)
            Spacer()
            Text(v).foregroundStyle(Theme.ink).multilineTextAlignment(.trailing)
        }
        .font(.body)
        .padding(.horizontal, 12).padding(.vertical, 14)
    }

    private var feedbackCard: some View {
        let v = stream.verdicts[e.key]
        return VStack(alignment: .leading, spacing: 12) {
            Text("Was this buzz helpful?").font(.spHeadline).foregroundStyle(Theme.ink)
            HStack(spacing: 12) {
                verdictButton("Yes, helpful", "hand.thumbsup", .useful, selected: v == .useful)
                verdictButton("Not needed", "hand.thumbsdown", .noisy, selected: v == .noisy)
            }
        }
        .softCard()
    }

    private func verdictButton(_ title: String, _ icon: String, _ verdict: Verdict, selected: Bool) -> some View {
        Button {
            softHaptic()
            withAnimation(Theme.motion(reduceMotion)) { stream.feedback(e, verdict) }
        } label: {
            Label(title, systemImage: selected ? icon + ".fill" : icon)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(selected ? .white : Theme.ink)
                .frame(maxWidth: .infinity, minHeight: 48)
                .background(selected ? Theme.ink : Theme.ink.opacity(0.05), in: RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(PressableStyle())
    }
}
