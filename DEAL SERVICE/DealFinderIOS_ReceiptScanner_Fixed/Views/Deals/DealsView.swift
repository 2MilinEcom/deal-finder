import SwiftUI
import SwiftData

struct DealsView: View {
    @StateObject private var repository = DealRepository.shared
    @Query(sort: \Deal.createdAt, order: .reverse) private var localDeals: [Deal]
    @State private var filter = "Active"
    private let filters = ["Active", "Saved", "Used", "Dismissed", "Expired"]
    private var filtered: [RemoteDeal] {
        repository.deals.filter {
            switch filter {
            case "Saved": return $0.isActive && $0.status == "saved"
            case "Used": return $0.used == 1
            case "Dismissed": return $0.status == "dismissed"
            case "Expired": return $0.isExpired && $0.used == 0
            default: return $0.isActive
            }
        }
    }
    var body: some View {
        List {
            Section {
                Picker("Deal history", selection: $filter) { ForEach(filters, id: \.self) { Text($0).tag($0) } }
                if repository.isLoading { ProgressView("Syncing your offers…") }
                if let message = repository.message { Text(message).font(.footnote).foregroundStyle(.secondary) }
                if !repository.pending.isEmpty {
                    Text("\(repository.pending.count) change(s) waiting to sync").font(.caption).foregroundStyle(.secondary)
                }
            }
            Section(filter + " deals") {
                if filtered.isEmpty {
                    ContentUnavailableView("No \(filter.lowercased()) deals", systemImage: "tag",
                                           description: Text("Discover an offer in Deals Near Me or sync deals from your Mac."))
                }
                ForEach(filtered) { deal in
                    DealCard(deal: deal, why: nil, distance: nil)
                }
            }
            if !localDeals.isEmpty {
                Section("Previously saved on this iPhone") {
                    ForEach(localDeals) { LocalDealRow(deal: $0) }
                }
            }
        }.navigationTitle("Deals")
            .toolbar { NavigationLink { BackendSettingsView() } label: { Label("Server settings", systemImage: "gearshape") } }
            .refreshable { await repository.refresh(force: true) }
            .task { await repository.refresh() }
    }
}

struct DealCard: View {
    let deal: RemoteDeal
    let why: [String]?
    let distance: Double?
    @ObservedObject private var repository = DealRepository.shared
    @State private var showReason = false
    @State private var confirmUse = false
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ViewThatFits(in: .horizontal) {
                HStack { merchant; Spacer(); value }
                VStack(alignment: .leading) { merchant; value }
            }
            Text(deal.title).font(.title3.weight(.semibold))
            if deal.memberDisplay != "Me" { Text(deal.memberDisplay).font(.caption).foregroundStyle(.secondary) }
            Label(deal.sourceDisplay, systemImage: deal.sourceType == "official" ? "building.2" : "tag")
                .font(.caption).foregroundStyle(.secondary)
            Text(deal.expirationDisplay).font(.caption).foregroundStyle(.secondary)
            if let minimum = deal.minimumSpend, minimum > 0 {
                Text("Minimum spend: \(minimum.formatted(.currency(code: "USD")))").font(.caption)
            }
            if let code = deal.promoCode, !code.isEmpty { Text("Code: \(code)").font(.subheadline.monospaced()).textSelection(.enabled) }
            if let notes = deal.notes, !notes.isEmpty { Text(notes).font(.footnote).foregroundStyle(.secondary).lineLimit(5) }
            if let why {
                DisclosureGroup("Why this deal?", isExpanded: $showReason) {
                    Text(why.joined(separator: ". ") + ".").font(.footnote).foregroundStyle(.secondary)
                }.font(.subheadline)
            }
            if let url = deal.safeSourceURL { Link("View offer and terms", destination: url).font(.subheadline.weight(.semibold)) }
            if deal.isActive {
                HStack {
                    Button(deal.status == "saved" ? "Unsave" : "Interested") {
                        Task { await repository.setState(deal, deal.status == "saved" ? "active" : "saved") }
                    }.buttonStyle(.bordered)
                    Button("Not for me") { Task { await repository.setState(deal, "dismissed") } }.buttonStyle(.bordered)
                }
                Button { confirmUse = true } label: { Label("Mark used", systemImage: "checkmark.circle").frame(maxWidth: .infinity) }
                    .buttonStyle(.borderedProminent).tint(.green)
            } else if deal.used == 1 || deal.status == "dismissed" {
                Button("Restore to active") { Task { await repository.setState(deal, "active") } }.buttonStyle(.bordered)
                if let date = deal.redeemedAt { Text("Marked used: \(String(date.prefix(10)))").font(.caption).foregroundStyle(.secondary) }
            }
            if repository.pending[deal.id] != nil { Label("Saved locally · sync pending", systemImage: "clock").font(.caption).foregroundStyle(.secondary) }
        }.padding(.vertical, 8).disabled(repository.isLoading)
            .confirmationDialog("Did you use this deal?", isPresented: $confirmUse, titleVisibility: .visible) {
                Button("Yes, mark used") { Task { await repository.setState(deal, "used") } }
                Button("Cancel", role: .cancel) { }
            }
    }
    private var merchant: some View {
        Text(deal.store + (distance.map { String(format: " · %.1f mi", $0 / 1609.344) } ?? "")).font(.subheadline).foregroundStyle(.secondary)
    }
    @ViewBuilder private var value: some View {
        if let value = deal.dollarValue { Text(value, format: .currency(code: "USD")).font(.headline).foregroundStyle(.green) }
        else if let value = deal.discountValue, value > 0, deal.typeDisplay.contains("%") {
            Text("\(value.formatted())% off").font(.headline).foregroundStyle(.green)
        }
    }
}

struct LocalDealRow: View {
    @Environment(\.modelContext) private var context
    let deal: Deal
    @State private var error: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(deal.title).font(.headline)
            Text(deal.store).foregroundStyle(.secondary)
            Text(deal.isUsed ? "Used" : "Saved on this iPhone").font(.caption)
            if let expiration = deal.expirationDate { Text("Expires \(expiration.formatted(date: .abbreviated, time: .omitted))").font(.caption) }
            Button(deal.isUsed ? "Mark unused" : "Mark used") {
                deal.isUsed.toggle()
                do { try context.save() } catch { context.rollback(); self.error = "This change could not be saved." }
            }.disabled(!deal.isUsed && deal.expirationDate.map { Calendar.current.startOfDay(for: $0) < Calendar.current.startOfDay(for: .now) } == true)
            if let error { Text(error).foregroundStyle(.red) }
        }
    }
}
