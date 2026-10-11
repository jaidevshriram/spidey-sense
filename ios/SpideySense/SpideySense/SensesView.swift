import SwiftUI

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
        let d = r.dot.map { $0 == 1 ? "left" : $0 == 2 ? "right" : "dot \($0)" } ?? "all dots"
        return "\(r.feel) · \(d)"
    }

    static func fmt(_ v: Double) -> String { v == v.rounded() ? String(Int(v)) : String(format: "%.2f", v) }
}

// MARK: - Senses tab

struct SensesView: View {
    let stream: EventStream
    @State private var editing: SenseRule?
    @State private var creating = false

    var body: some View {
        NavigationStack {
            List {
                if stream.rulesAreSample {
                    SampleBanner(text: "Sample senses: not synced with the Mac engine. Edits stay on this phone.")
                        .listRowSeparator(.hidden)
                }
                ForEach(stream.rules) { r in
                    SenseRow(rule: r, onToggle: { stream.setEnabled(r, $0) })
                        .contentShape(Rectangle())
                        .onTapGesture { editing = r }
                        .swipeActions(edge: .trailing) {
                            Button(role: .destructive) { stream.deleteRule(id: r.id) } label: {
                                Label("Delete", systemImage: "trash")
                            }
                        }
                }
            }
            .listStyle(.insetGrouped)
            .overlay {
                if stream.rules.isEmpty {
                    ContentUnavailableView("No senses", systemImage: "dot.radiowaves.left.and.right",
                                           description: Text("Tap + to add one."))
                }
            }
            .navigationTitle("Senses")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { StatusDot(status: stream.status) }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { creating = true } label: { Image(systemName: "plus") }
                        .accessibilityLabel("Add sense")
                }
            }
            .onAppear {
                // Screenshot/demo hook: `-openBuilder YES` launch argument opens the builder.
                if UserDefaults.standard.bool(forKey: "openBuilder") { creating = true }
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

struct SenseRow: View {
    let rule: SenseRule
    let onToggle: (Bool) -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: rule.senseSource?.icon ?? "questionmark")
                .font(.title3).foregroundStyle(patternColor(rule.feel)).frame(width: 28)
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(rule.name).font(.headline)
                    if rule.senseSource?.isWired == false {
                        Text("Mac side TODO").font(.caption2.bold()).foregroundStyle(.secondary)
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(.secondary.opacity(0.15), in: Capsule())
                    }
                }
                line("When", RuleText.when(rule))
                line("If", RuleText.cond(rule))
                line("Feel", RuleText.feel(rule))
                Text("cooldown \(RuleText.fmt(rule.cooldownS)) s").font(.caption).foregroundStyle(.tertiary)
            }
            Spacer()
            Toggle("Enabled", isOn: Binding(get: { rule.enabled }, set: onToggle)).labelsHidden()
        }
        .opacity(rule.enabled ? 1 : 0.55)
        .padding(.vertical, 2)
    }

    private func line(_ k: String, _ v: String) -> some View {
        (Text(k + " ").font(.caption.bold()).foregroundStyle(.secondary) + Text(v).font(.subheadline))
    }
}

// MARK: - Sense Builder sheet

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

    @State private var name = "New sense"
    @State private var source: SenseSource = .neon
    @State private var hazards = "any"
    @State private var cond: CondKind = .hazardUnseen
    @State private var urgency = 0.6
    @State private var hrBand = 15.0
    @State private var metric = "cpu_temp_c"
    @State private var threshold = 90.0
    @State private var feel: FeelKind = .urgent
    @State private var customFeel = ""
    @State private var dot = 0        // 0 all, 1 left, 2 right
    @State private var cooldown = 3.0
    @State private var enabled = true

    var body: some View {
        NavigationStack {
            Form {
                if original == nil {
                    Section("Presets") {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack {
                                ForEach(MockData.presets) { p in
                                    Button { load(p, asPreset: true) } label: {
                                        Label(p.name, systemImage: p.senseSource?.icon ?? "star")
                                    }
                                    .buttonStyle(.bordered)
                                }
                            }
                        }
                    }
                }
                Section("Name") { TextField("Name", text: $name) }
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
                }
                Section("Feel") {
                    Picker("Pattern", selection: $feel) {
                        ForEach(FeelKind.allCases) { Text($0.rawValue.capitalized).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    if feel == .custom {
                        TextField("Pattern name (key in spidey_haptics.PATTERNS)", text: $customFeel)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                    }
                    Picker("Dot", selection: $dot) {
                        Text("All").tag(0); Text("Left").tag(1); Text("Right").tag(2)
                    }
                    .pickerStyle(.segmented)
                    Stepper("Cooldown \(RuleText.fmt(cooldown)) s", value: $cooldown, in: 0...600, step: 1)
                    Toggle("Enabled", isOn: $enabled)
                }
            }
            .navigationTitle(original == nil ? "New sense" : "Edit sense")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { onSave(build()); dismiss() }
                        .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty
                                  || (feel == .custom && customFeel.isEmpty))
                }
            }
            .onAppear { if let original { load(original, asPreset: false) } }
        }
    }

    private func load(_ r: SenseRule, asPreset: Bool) {
        name = r.name
        source = r.senseSource ?? .neon
        if case .kinds(let k) = r.when { hazards = k.joined(separator: ", ") } else { hazards = "any" }
        let c = r.cond
        if c.hrBandBpm != nil { cond = .hrOutsideMedian; hrBand = c.hrBandBpm ?? 15 }
        else if c.valueGt != nil { cond = .valueAbove; threshold = c.valueGt ?? 90; metric = c.metric ?? "" }
        else if c.unseen == true { cond = .hazardUnseen; urgency = c.minUrgency ?? 0 }
        else if c.minUrgency != nil { cond = .urgencyAtLeast; urgency = c.minUrgency ?? 0.6 }
        else { cond = .always }
        if let f = FeelKind(rawValue: r.feel), f != .custom { feel = f } else { feel = .custom; customFeel = r.feel }
        dot = r.dot ?? 0
        cooldown = r.cooldownS
        enabled = asPreset ? true : r.enabled
    }

    private func build() -> SenseRule {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        var id = original?.id ?? slug(trimmed)
        if original == nil {   // new sense: don't overwrite an existing id
            let base = id; var n = 2
            while existingIDs.contains(id) { id = "\(base)_\(n)"; n += 1 }
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
        return SenseRule(id: id, name: trimmed, enabled: enabled, source: source.rawValue,
                         when: source == .neon && !kinds.isEmpty ? .kinds(kinds) : .any, cond: c,
                         feel: feel == .custom ? customFeel : feel.rawValue,
                         dot: dot == 0 ? nil : dot, cooldownS: cooldown)
    }

    private func slug(_ s: String) -> String {
        let parts = s.lowercased().split { !($0.isLetter || $0.isNumber) || !$0.isASCII }
        return parts.isEmpty ? "sense" : parts.joined(separator: "_")
    }
}
