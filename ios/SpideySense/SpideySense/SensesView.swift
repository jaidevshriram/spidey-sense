import SwiftUI
import UIKit

// MARK: - Human-readable rule text

enum RuleText {
    static func when(_ r: SenseRule) -> String {
        let src = r.senseSource?.label ?? r.source
        if r.senseSource == .neon, case .kinds(let k) = r.when { return "\(src) (\(k.joined(separator: ", ")))" }
        return src
    }

    static func cond(_ r: SenseRule) -> String {
        let c = r.cond
        var parts: [String] = []
        if let b = c.hrBandBpm {
            let w = Int((c.hrWindowS ?? 120) / 60)
            parts.append("HR outside \(w)-min median ± \(Int(b)) bpm")
        }
        if let v = c.valueGt { parts.append("\(c.metric ?? "value") > \(fmt(v))") }
        if c.unseen == true { parts.append("hazard unseen") }
        if c.seen == true { parts.append("hazard seen") }
        if let m = c.minUrgency { parts.append("urgency ≥ \(fmt(m))") }
        if let m = c.maxUrgency { parts.append("urgency ≤ \(fmt(m))") }
        if parts.isEmpty { return r.senseSource == .manual ? "tapped" : "any \(r.when.label)" }
        return parts.joined(separator: ", ")
    }

    static func feel(_ r: SenseRule) -> String {
        var s = "\(r.feel) · \(dot(r))"
        if let i = r.intensity, i < 0.995 { s += " · \(pct(i))" }
        return s
    }

    static func dot(_ r: SenseRule) -> String {
        r.dot.map { $0 == 1 ? "left" : $0 == 2 ? "right" : "dot \($0)" } ?? "all dots"
    }

    static func pct(_ v: Double) -> String { "\(Int((v * 100).rounded()))%" }

    static func fmt(_ v: Double) -> String { v == v.rounded() ? String(Int(v)) : String(format: "%.2f", v) }
}

/// LED colour defaults and Color <-> [r, g, b] conversion for triggers.
enum TriggerColor {
    static func defaultRGB(_ feel: String) -> [Int] {
        switch feel {
        case "urgent": [255, 0, 0]
        case "pulse": [255, 45, 140]
        case "ambient": [0, 90, 255]
        default: [255, 255, 255]
        }
    }

    static func color(_ rgb: [Int]) -> Color {
        guard rgb.count == 3 else { return .gray }
        return Color(red: Double(rgb[0]) / 255, green: Double(rgb[1]) / 255, blue: Double(rgb[2]) / 255)
    }

    static func rgb(_ c: Color) -> [Int] {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        UIColor(c).getRed(&r, green: &g, blue: &b, alpha: &a)
        return [r, g, b].map { Int((min(max($0, 0), 1) * 255).rounded()) }
    }
}

// MARK: - Senses tab

struct SensesView: View {
    let stream: EventStream
    @State private var editing: SenseRule?
    @State private var creating = false
    @State private var confirmDelete: SenseRule?
    @State private var confirmReset = false

    var body: some View {
        NavigationStack {
            List {
                if stream.rulesAreSample {
                    SampleBanner(text: "Sample triggers: not synced with the Mac engine. Edits stay on this phone.")
                        .listRowSeparator(.hidden)
                }
                ForEach(stream.rules) { r in
                    SenseRow(rule: r, onToggle: { stream.setEnabled(r, $0) }, onTap: { editing = r })
                        .swipeActions(edge: .trailing, allowsFullSwipe: r.isCustom) {
                            Button(role: r.isCustom ? .destructive : nil) {
                                if r.isCustom { stream.deleteRule(id: r.id) } else { confirmDelete = r }
                            } label: {
                                Label("Delete", systemImage: "trash")
                            }
                            .tint(.red)
                        }
                }
            }
            .listStyle(.insetGrouped)
            .overlay {
                if stream.rules.isEmpty {
                    ContentUnavailableView("No triggers", systemImage: "dot.radiowaves.left.and.right",
                                           description: Text("Tap + to add one, or Reset to defaults."))
                }
            }
            .navigationTitle("Senses")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { StatusDot(status: stream.status) }
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Menu {
                        Button { creating = true } label: { Label("Add trigger", systemImage: "plus") }
                        Button(role: .destructive) { confirmReset = true } label: {
                            Label("Reset to defaults", systemImage: "arrow.counterclockwise")
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                    .accessibilityLabel("Trigger options")
                    Button { creating = true } label: { Image(systemName: "plus") }
                        .accessibilityLabel("Add trigger")
                }
            }
            .confirmationDialog("Delete the built-in trigger \"\(confirmDelete?.name ?? "")\"?",
                                isPresented: Binding(get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } }),
                                titleVisibility: .visible, presenting: confirmDelete) { r in
                Button("Delete \(r.name)", role: .destructive) { stream.deleteRule(id: r.id) }
                Button("Cancel", role: .cancel) {}
            } message: { _ in
                Text("Reset to defaults brings it back.")
            }
            .confirmationDialog("Reset triggers to defaults?", isPresented: $confirmReset, titleVisibility: .visible) {
                Button("Reset to defaults", role: .destructive) { stream.resetRules() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Removes every custom trigger and restores the built-in ones on the Mac.")
            }
            .onAppear {
                // Screenshot/demo hooks: `-openBuilder YES` opens the Add trigger sheet;
                // `-editTrigger <id>` opens that trigger's editor.
                if UserDefaults.standard.bool(forKey: "openBuilder") { creating = true }
                if let id = UserDefaults.standard.string(forKey: "editTrigger"),
                   let r = stream.rules.first(where: { $0.id == id }) { editing = r }
            }
            .sheet(item: $editing) { r in
                SenseBuilder(original: r, existingIDs: Set(stream.rules.map(\.id))) { stream.upsert($0) }
            }
            .sheet(isPresented: $creating) {
                SenseBuilder(original: nil, existingIDs: Set(stream.rules.map(\.id))) { stream.upsert($0) }
            }
        }
    }
}

struct KindBadge: View {
    let custom: Bool
    var body: some View {
        Text(custom ? "Custom" : "Built-in")
            .font(.caption2.bold())
            .foregroundStyle(custom ? Color.purple : Color.secondary)
            .padding(.horizontal, 6).padding(.vertical, 1)
            .background((custom ? Color.purple : Color.secondary).opacity(0.15), in: Capsule())
    }
}

struct SenseRow: View {
    let rule: SenseRule
    let onToggle: (Bool) -> Void
    var onTap: () -> Void = {}

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            details
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
                .onTapGesture(perform: onTap)   // tap to edit; kept off the toggle so it doesn't steal its taps
                .accessibilityAddTraits(.isButton)
            Toggle("Enabled", isOn: Binding(get: { rule.enabled }, set: onToggle)).labelsHidden()
        }
        .opacity(rule.enabled ? 1 : 0.55)
        .padding(.vertical, 2)
    }

    private var details: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: rule.isCustom ? "text.bubble" : (rule.senseSource?.icon ?? "questionmark"))
                .font(.title3).foregroundStyle(patternColor(rule.feel)).frame(width: 28)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(rule.name).font(.headline)
                    KindBadge(custom: rule.isCustom)
                    if rule.senseSource?.isWired == false {
                        Text("Mac side TODO").font(.caption2.bold()).foregroundStyle(.secondary)
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(.secondary.opacity(0.15), in: Capsule())
                    }
                }
                if rule.isCustom {
                    Text(rule.describe ?? "")
                        .font(.subheadline).lineLimit(2).truncationMode(.tail)
                    customSummary
                } else {
                    line("When", RuleText.when(rule))
                    line("If", RuleText.cond(rule))
                    HStack(spacing: 4) {
                        line("Feel", RuleText.feel(rule))
                        if let rgb = rule.rgb { Swatch(rgb: rgb) }
                    }
                    Text("cooldown \(RuleText.fmt(rule.cooldownS)) s").font(.caption).foregroundStyle(.tertiary)
                }
            }
        }
    }

    /// "Feel urgent · 80% · ● · cooldown 5s · only if unseen" as one wrapping line, swatch inline.
    private var customSummary: some View {
        let rgb = rule.rgb ?? TriggerColor.defaultRGB(rule.feel)
        var tail = " · cooldown \(RuleText.fmt(rule.cooldownS))s"
        if rule.cond.unseen == true { tail += " · only if unseen" }
        if rule.dot != nil { tail += " · \(RuleText.dot(rule))" }
        return (Text("Feel ").bold()
                + Text("\(rule.feel) · \(RuleText.pct(rule.intensity ?? 1)) · ")
                + Text(Image(systemName: "circle.fill")).foregroundColor(TriggerColor.color(rgb))
                + Text(tail))
            .font(.caption).foregroundStyle(.secondary)
            .accessibilityLabel("Feel \(rule.feel), \(RuleText.pct(rule.intensity ?? 1))\(tail)")
    }

    private func line(_ k: String, _ v: String) -> some View {
        (Text(k + " ").font(.caption.bold()).foregroundStyle(.secondary) + Text(v).font(.subheadline))
    }
}

struct Swatch: View {
    let rgb: [Int]
    var body: some View {
        Circle().fill(TriggerColor.color(rgb)).frame(width: 10, height: 10)
            .overlay(Circle().stroke(.secondary.opacity(0.4), lineWidth: 0.5))
            .accessibilityLabel("LED colour")
    }
}

// MARK: - Add / edit trigger sheet

struct SenseBuilder: View {
    enum CondKind: String, CaseIterable, Identifiable {
        case hazardUnseen, urgencyAtLeast, hrOutsideMedian, valueAbove, always
        var id: String { rawValue }
        var label: String {
            switch self {
            case .hazardUnseen: "Hazard unseen"
            case .urgencyAtLeast: "Urgency ≥ x"
            case .hrOutsideMedian: "HR outside 2-min median ± y"
            case .valueAbove: "Value > threshold"
            case .always: "Any trigger"
            }
        }
    }
    enum FeelKind: String, CaseIterable, Identifiable {
        case urgent, pulse, ambient, custom
        var id: String { rawValue }
    }

    let original: SenseRule?
    let existingIDs: Set<String>
    let onSave: (SenseRule) -> Void
    @Environment(\.dismiss) private var dismiss

    @State private var isCustom = true
    @State private var name = ""
    @State private var describe = ""
    // Built-in condition builder
    @State private var source: SenseSource = .neon
    @State private var hazards = "any"
    @State private var cond: CondKind = .hazardUnseen
    @State private var urgency = 0.6
    @State private var hrBand = 15.0
    @State private var metric = "cpu_temp_c"
    @State private var threshold = 90.0
    // Custom conditions
    @State private var onlyUnseen = true
    @State private var minUrgency = 0.5
    // Haptic action (both kinds)
    @State private var feel: FeelKind = .urgent
    @State private var customFeel = ""
    @State private var intensity = 1.0
    @State private var led: Color = TriggerColor.color(TriggerColor.defaultRGB("urgent"))
    @State private var ledTouched = false      // false = LED follows the pattern's default colour
    @State private var dot = 0                 // 0 all, 1 left, 2 right
    @State private var cooldown = 5.0
    @State private var enabled = true

    private var feelKey: String { feel == .custom ? customFeel.trimmingCharacters(in: .whitespaces) : feel.rawValue }
    private var trimmedName: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var trimmedDescribe: String { describe.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var effectiveName: String { trimmedName.isEmpty ? Self.suggestName(from: trimmedDescribe) : trimmedName }
    private var canSave: Bool {
        !(feel == .custom && feelKey.isEmpty)
            && (isCustom ? !trimmedDescribe.isEmpty && !effectiveName.isEmpty : !trimmedName.isEmpty)
    }

    var body: some View {
        NavigationStack {
            Form {
                if original == nil { quickStart }
                Section("Name") {
                    TextField(trimmedDescribe.isEmpty || !isCustom ? "Name" : Self.suggestName(from: trimmedDescribe),
                              text: $name)
                    if isCustom && trimmedName.isEmpty && !trimmedDescribe.isEmpty {
                        Text("Will be saved as \"\(effectiveName)\"").font(.footnote).foregroundStyle(.secondary)
                    }
                }
                if isCustom { customSections } else { builtinSections }
                hapticSection
                Section {
                    Toggle("Enabled", isOn: $enabled)
                } footer: {
                    if let id = original?.id { Text("id: \(id)") }
                }
            }
            .navigationTitle(original == nil ? "Add trigger" : "Edit trigger")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { onSave(build()); dismiss() }.disabled(!canSave)
                }
            }
            .onAppear { if let original { load(original) } }
            .onChange(of: feel) { _, f in
                if !ledTouched { led = TriggerColor.color(TriggerColor.defaultRGB(f.rawValue)) }
            }
        }
    }

    // MARK: Sections

    private var quickStart: some View {
        Section {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack {
                    ForEach(MockData.customTemplates) { t in
                        Button { load(t, asTemplate: true) } label: { Label(t.name, systemImage: "text.bubble") }
                            .buttonStyle(.bordered).tint(.purple)
                    }
                    ForEach(MockData.presets) { p in
                        Button { load(p, asTemplate: true) } label: {
                            Label(p.name, systemImage: p.senseSource?.icon ?? "star")
                        }
                        .buttonStyle(.bordered)
                    }
                }
            }
            Picker("Type", selection: $isCustom) {
                Text("Plain English").tag(true)
                Text("Built-in condition").tag(false)
            }
            .pickerStyle(.segmented)
        } header: {
            Text("Quick start")
        } footer: {
            Text("Plain-English triggers are judged by Haiku on the Mac, in the same call that spots hazards.")
        }
    }

    @ViewBuilder private var customSections: some View {
        Section("Describe what to watch for and how it should work") {
            ZStack(alignment: .topLeading) {
                if describe.isEmpty {
                    Text("e.g. warn me if someone holding a knife is near and I haven't looked at them")
                        .foregroundStyle(.tertiary).padding(.top, 8).padding(.leading, 5)
                        .allowsHitTesting(false)
                }
                TextEditor(text: $describe)
                    .frame(minHeight: 96)
                    .scrollContentBackground(.hidden)
            }
        }
        Section("When") {
            Toggle("Only if I haven't looked at it", isOn: $onlyUnseen)
            VStack(alignment: .leading) {
                Text("Min urgency \(minUrgency, specifier: "%.2f")")
                Slider(value: $minUrgency, in: 0...1, step: 0.05)
            }
            Stepper("Cooldown \(RuleText.fmt(cooldown)) s", value: $cooldown, in: 1...60, step: 1)
        }
    }

    @ViewBuilder private var builtinSections: some View {
        Section("When") {
            Picker("Source", selection: $source) {
                ForEach(SenseSource.allCases) { Label($0.label, systemImage: $0.icon).tag($0) }
            }
            if source == .neon {
                TextField("Hazard kinds (comma-separated, or any)", text: $hazards)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            }
            if !source.isWired {
                Text("Saved and synced, but the Mac engine doesn't read this source yet (TODO).")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        Section("If") {
            Picker("Condition", selection: $cond) {
                ForEach(CondKind.allCases) { Text($0.label).tag($0) }
            }
            switch cond {
            case .hazardUnseen, .urgencyAtLeast:
                VStack(alignment: .leading) {
                    Text("Urgency ≥ \(urgency, specifier: "%.2f")")
                    Slider(value: $urgency, in: 0...1, step: 0.05)
                }
            case .hrOutsideMedian:
                Stepper("± \(Int(hrBand)) bpm", value: $hrBand, in: 2...60, step: 1)
            case .valueAbove:
                TextField("Metric", text: $metric).textInputAutocapitalization(.never).autocorrectionDisabled()
                HStack {
                    Text("Threshold")
                    TextField("90", value: $threshold, format: .number)
                        .keyboardType(.decimalPad).multilineTextAlignment(.trailing)
                }
            case .always:
                EmptyView()
            }
            Stepper("Cooldown \(RuleText.fmt(cooldown)) s", value: $cooldown, in: 0...600, step: 1)
        }
    }

    private var hapticSection: some View {
        Section("Haptic action") {
            Picker("Pattern", selection: $feel) {
                ForEach(FeelKind.allCases) { Text($0.rawValue.capitalized).tag($0) }
            }
            .pickerStyle(.segmented)
            if feel == .custom {
                TextField("Pattern key (in spidey_haptics.PATTERNS)", text: $customFeel)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            }
            VStack(alignment: .leading) {
                Text("Intensity \(RuleText.pct(intensity))")
                Slider(value: $intensity, in: 0...1, step: 0.05)
            }
            ColorPicker("LED colour", selection: Binding(get: { led }, set: { led = $0; ledTouched = true }),
                        supportsOpacity: false)
            Picker("Dot", selection: $dot) {
                Text("All").tag(0); Text("Left (1)").tag(1); Text("Right (2)").tag(2)
            }
            .pickerStyle(.segmented)
        }
    }

    // MARK: Load / build

    private func load(_ r: SenseRule, asTemplate: Bool = false) {
        isCustom = r.isCustom
        name = r.name
        describe = r.describe ?? ""
        source = r.senseSource ?? .neon
        if case .kinds(let k) = r.when { hazards = k.joined(separator: ", ") } else { hazards = "any" }
        let c = r.cond
        if c.hrBandBpm != nil { cond = .hrOutsideMedian; hrBand = c.hrBandBpm ?? 15 }
        else if c.valueGt != nil { cond = .valueAbove; threshold = c.valueGt ?? 90; metric = c.metric ?? "" }
        else if c.unseen == true { cond = .hazardUnseen; urgency = c.minUrgency ?? 0 }
        else if c.minUrgency != nil { cond = .urgencyAtLeast; urgency = c.minUrgency ?? 0.6 }
        else { cond = .always }
        onlyUnseen = r.isCustom ? (c.unseen ?? false) : true
        minUrgency = c.minUrgency ?? 0.5
        if let f = FeelKind(rawValue: r.feel), f != .custom { feel = f; customFeel = "" }
        else { feel = .custom; customFeel = r.feel }
        intensity = r.intensity ?? 1
        led = TriggerColor.color(r.rgb ?? TriggerColor.defaultRGB(r.feel))
        ledTouched = r.rgb != nil && r.rgb != TriggerColor.defaultRGB(r.feel)
        dot = r.dot ?? 0
        cooldown = r.isCustom ? min(max(r.cooldownS, 1), 60) : r.cooldownS
        enabled = asTemplate ? true : r.enabled
    }

    private func build() -> SenseRule {
        let finalName = isCustom ? effectiveName : trimmedName
        var id = original?.id ?? triggerSlug(finalName, fallback: "trigger")
        if original == nil {   // new trigger: don't overwrite an existing id
            let base = id; var n = 2
            while existingIDs.contains(id) { id = "\(base)_\(n)"; n += 1 }
        }
        let rgb = TriggerColor.rgb(led)
        if isCustom {
            var c = RuleCondition()
            c.unseen = onlyUnseen; c.minUrgency = minUrgency
            return SenseRule(id: id, name: finalName, enabled: enabled, source: SenseSource.neon.rawValue,
                             when: .any, cond: c, feel: feelKey, dot: dot == 0 ? nil : dot, cooldownS: cooldown,
                             kind: "custom", describe: trimmedDescribe, intensity: (intensity * 100).rounded() / 100, rgb: rgb)
        }
        var c = original?.cond ?? RuleCondition()
        // Reset the keys this builder owns; keep anything else (e.g. max_urgency from config.yaml).
        c.unseen = nil; c.minUrgency = nil; c.hrBandBpm = nil; c.hrWindowS = nil; c.metric = nil; c.valueGt = nil
        switch cond {
        case .hazardUnseen: c.unseen = true; c.minUrgency = urgency > 0 ? urgency : nil
        case .urgencyAtLeast: c.minUrgency = urgency
        case .hrOutsideMedian: c.hrBandBpm = hrBand; c.hrWindowS = 120
        case .valueAbove: c.metric = metric; c.valueGt = threshold
        case .always: break
        }
        let kinds = hazards.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
            .filter { !$0.isEmpty && $0 != "any" }
        // Built-ins only send intensity/rgb when changed from the defaults (or already set), so untouched
        // built-ins keep following the engine's pattern colours.
        let keepIntensity = intensity < 0.995 || original?.intensity != nil
        let keepRGB = ledTouched || original?.rgb != nil
        return SenseRule(id: id, name: finalName, enabled: enabled, source: source.rawValue,
                         when: source == .neon && !kinds.isEmpty ? .kinds(kinds) : .any, cond: c,
                         feel: feelKey, dot: dot == 0 ? nil : dot, cooldownS: cooldown,
                         kind: "builtin", describe: nil,
                         intensity: keepIntensity ? (intensity * 100).rounded() / 100 : nil, rgb: keepRGB ? rgb : nil)
    }

    /// "warn me if someone holding a knife is near…" -> "Someone holding a knife".
    static func suggestName(from text: String) -> String {
        var words = text.split(whereSeparator: { $0.isWhitespace }).map {
            String($0).trimmingCharacters(in: .punctuationCharacters)
        }.filter { !$0.isEmpty }
        let fillers: Set<String> = ["warn", "tell", "alert", "buzz", "notify", "let", "me", "know", "if", "when",
                                    "whenever", "there's", "there", "is", "a", "an", "the", "please"]
        while let w = words.first, fillers.contains(w.lowercased()), words.count > 1 { words.removeFirst() }
        let s = words.prefix(4).joined(separator: " ")
        return s.prefix(1).uppercased() + s.dropFirst()
    }
}
