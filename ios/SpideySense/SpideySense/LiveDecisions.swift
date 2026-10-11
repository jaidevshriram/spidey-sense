import SwiftUI
import UIKit

// MARK: - Log tab: "Live decisions" (trace stream, one row per Haiku call)

private let traceTimeFmt: DateFormatter = {
    let f = DateFormatter(); f.dateFormat = "HH:mm:ss"; return f
}()

/// List rows at the top of the Log: the toggle, an All / Near misses filter, and the newest calls.
struct LiveDecisionsRows: View {
    let stream: EventStream
    @Binding var on: Bool
    @Binding var selected: Trace?
    @State private var nearMissesOnly = false
    @State private var showAll = false

    private let collapsedCount = 8
    private var filtered: [Trace] { nearMissesOnly ? stream.traces.filter(\.nearMiss) : stream.traces }
    private var shown: ArraySlice<Trace> { showAll ? filtered[...] : filtered.prefix(collapsedCount) }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Toggle(isOn: $on) {
                VStack(alignment: .leading, spacing: 1) {
                    Label("Live decisions", systemImage: "brain").font(.subheadline.bold())
                    Text(subtitle).font(.caption).foregroundStyle(on && !stream.isLive ? .red : .secondary)
                }
            }
            if on {
                Picker("Show", selection: $nearMissesOnly) {
                    Text("All").tag(false)
                    Text("Near misses").tag(true)
                }
                .pickerStyle(.segmented)
            }
        }
        .padding(.vertical, 2)
        .listRowBackground(Color.indigo.opacity(0.05))

        if on {
            if shown.isEmpty {
                Text(emptyText).font(.caption).foregroundStyle(.secondary)
                    .listRowBackground(Color.indigo.opacity(0.05))
            }
            ForEach(shown) { t in
                Button { selected = t } label: { TraceRow(trace: t) }
                    .buttonStyle(.plain)
                    .listRowBackground(t.nearMiss ? Color.orange.opacity(0.07) : Color.indigo.opacity(0.05))
            }
            if filtered.count > collapsedCount || !stream.traces.isEmpty {
                HStack {
                    if filtered.count > collapsedCount {
                        Button(showAll ? "Show fewer" : "Show more (\(filtered.count - collapsedCount))") {
                            showAll.toggle()
                        }
                    }
                    Spacer()
                    if !stream.traces.isEmpty {
                        Button("Clear", role: .destructive) { stream.clearTraces() }
                    }
                }
                .font(.caption.bold())
                .buttonStyle(.borderless)
                .listRowBackground(Color.indigo.opacity(0.05))
            }
        }
    }

    private var subtitle: String {
        guard on else { return "Why each trigger did or didn't fire, per Haiku call" }
        guard stream.isLive else { return "Not connected: nothing streams" }
        return "Streaming · \(stream.traces.count) call\(stream.traces.count == 1 ? "" : "s") kept"
    }

    private var emptyText: String {
        if !stream.isLive { return "Not connected: nothing streams." }
        return nearMissesOnly ? "No near misses yet." : "Waiting for Haiku calls… (older engines don't send traces)"
    }
}

/// One Haiku call: time, reason (hazard), then one line per decision.
struct TraceRow: View {
    let trace: Trace

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if let img = trace.thumb {
                Image(uiImage: img).resizable().scaledToFill()
                    .frame(width: 44, height: 44)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                    .accessibilityLabel("Frame thumbnail")
            }
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(traceTimeFmt.string(from: trace.date)).font(.system(.caption, design: .monospaced))
                    if trace.nearMiss { Tag(text: "near miss", color: .orange) }
                    if trace.stale == true { Tag(text: "stale", color: .orange) }
                    if trace.paused == true { Tag(text: "paused", color: .orange) }
                    if trace.error != nil { Tag(text: "error", color: .red) }
                    Spacer(minLength: 0)
                    if let ms = trace.latencyMs {
                        Text("\(Int(ms.rounded())) ms").font(.caption2.monospacedDigit()).foregroundStyle(.secondary)
                    }
                }
                summary.font(.caption).lineLimit(2)
                ForEach(trace.decisions) { d in DecisionLine(decision: d).lineLimit(2) }
            }
        }
        .padding(.vertical, 1)
        .contentShape(Rectangle())
    }

    private var summary: Text {
        let reason = trace.error.map { "error: \($0)" } ?? trace.reason ?? ""
        if let h = trace.hazard, !h.isEmpty {
            return Text(h).bold().foregroundColor(.red) + Text(reason.isEmpty ? "" : " · \(reason)")
        }
        return Text(reason.isEmpty ? "(no reason)" : reason)
    }
}

/// "✓ Name fired" (green) / "✗ Name: why" (secondary; orange when it matched but didn't fire).
struct DecisionLine: View {
    let decision: TraceDecision
    var body: some View {
        if decision.fired == true {
            Text("✓ \(decision.label) fired").font(.caption.bold()).foregroundStyle(.green)
        } else {
            Text("✗ \(decision.label): \(decision.why ?? "no match")")
                .font(.caption)
                .foregroundStyle(decision.matched == true ? Color.orange : Color.secondary)
        }
    }
}

/// Full details for one traced call, including Haiku's raw reply.
struct TraceDetail: View {
    let trace: Trace
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if let img = trace.thumb {
                    Section {
                        Image(uiImage: img).resizable().scaledToFit()
                            .frame(maxWidth: .infinity)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                            .accessibilityLabel("Frame for this call, with the gaze dot and box")
                    }
                }
                Section("Call") {
                    row("Captured", trace.ts.map { traceTimeFmt.string(from: Date(timeIntervalSince1970: $0)) })
                    row("Result", trace.t.map { traceTimeFmt.string(from: Date(timeIntervalSince1970: $0)) })
                    row("Latency", trace.latencyMs.map { "\(Int($0.rounded())) ms" })
                    row("Images", trace.nImages.map(String.init))
                    row("Hazard", trace.hazard ?? "none")
                    row("Urgency", trace.urgency.map { String(format: "%.2f", $0) })
                    row("Near miss", trace.nearMiss ? "yes" : "no")
                    row("Stale", flag(trace.stale))
                    row("Paused", flag(trace.paused))
                    if let e = trace.error {
                        Text("Error: \(e)").font(.callout).foregroundStyle(.red).textSelection(.enabled)
                    }
                    if let r = trace.reason, !r.isEmpty {
                        Text(r).font(.callout).textSelection(.enabled)
                    }
                }
                Section("Decisions") {
                    if trace.decisions.isEmpty {
                        Text("No enabled camera triggers").foregroundStyle(.secondary)
                    }
                    ForEach(trace.decisions) { d in
                        VStack(alignment: .leading, spacing: 3) {
                            DecisionLine(decision: d)
                            let meta = [d.triggerID.map { "id \($0)" }, d.gazeMode.map { "gaze \($0)" },
                                        d.matched.map { "matched \($0 ? "yes" : "no")" }].compactMap { $0 }
                            if !meta.isEmpty {
                                Text(meta.joined(separator: " · ")).font(.caption2).foregroundStyle(.secondary)
                            }
                            if let h = d.haikuReason, !h.isEmpty {
                                Text("Haiku: \(h)").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .textSelection(.enabled)
                    }
                }
                Section {
                    HaikuRawSection(callID: nil, raw: trace.raw, showHeader: false)
                } header: { Text("Haiku raw output") }
            }
            .navigationTitle(trace.callID.map { "Haiku call #\($0)" } ?? "Haiku call")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("Done") { dismiss() } }
        }
    }

    private func flag(_ b: Bool?) -> String? { b.map { $0 ? "yes" : "no" } }

    private func row(_ k: String, _ v: String?) -> some View {
        LabeledContent(k, value: v ?? "—").font(.callout)
    }
}

/// Haiku's raw reply, monospaced and selectable, with a copy button (and the call id when known).
struct HaikuRawSection: View {
    let callID: Int?
    let raw: String?
    var showHeader = true

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                if showHeader { Text("Haiku raw output").font(.headline) }
                if let id = callID {
                    Text("call #\(id)").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
                Spacer()
                if let raw {
                    Button { UIPasteboard.general.string = raw } label: { Label("Copy", systemImage: "doc.on.doc") }
                        .font(.caption).buttonStyle(.borderless)
                }
            }
            Text(raw ?? "(not sent by the engine)")
                .font(.system(.footnote, design: .monospaced))
                .foregroundStyle(raw == nil ? .secondary : .primary)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
                .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
        }
    }
}
