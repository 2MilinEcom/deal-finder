import SwiftUI
import SwiftData
import CoreLocation

struct HomeView: View {
    @Query(sort: \Receipt.purchaseDate, order: .reverse) private var receipts: [Receipt]
    @Query(sort: \Deal.createdAt, order: .reverse) private var localDeals: [Deal]
    @StateObject private var repository = DealRepository.shared
    @StateObject private var location = LocationManager.shared
    @State private var nearby: [StoreLocation] = []
    @StateObject private var proactive = ProactiveDealService.shared
    @State private var response: ForYouResponse?
    @State private var isRanking = false
    @State private var locationMessage: String?
    private var profile: [ReceiptProfile] {
        receipts.prefix(100).map { ReceiptProfile(store: $0.store, items: Array($0.items.prefix(100)).map { String($0.itemName.prefix(200)) }) }
    }
    private var recommendations: [Recommendation] {
        // Recompute immediately after offline feedback rather than retaining a stale server order.
        if repository.pending.isEmpty, let response, !response.recommendations.isEmpty {
            return response.recommendations.filter { r in repository.active.contains { $0.id == r.id } }
        }
        return ForYouService().local(deals: repository.deals, receipts: profile, nearby: nearby)
    }
    private var profileSignature: String { profile.map { $0.store + $0.items.joined(separator: "|") }.joined(separator: ";") }
    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Label("Small savings, built around you", systemImage: "sparkles").font(.headline)
                    Text("Offers chosen from your shopping history, feedback and nearby businesses.").foregroundStyle(.secondary)
                    HStack {
                        Label("\(repository.active.count) active", systemImage: "tag")
                        Spacer()
                        Label("\(receipts.count) receipts", systemImage: "receipt")
                    }.font(.caption).foregroundStyle(.secondary)
                }.padding(.vertical, 8)
                if let message = repository.message { Text(message).font(.footnote).foregroundStyle(.secondary) }
                if repository.isLoading || isRanking { ProgressView("Finding your best offers…") }
            }
            Section("Best deals for you") {
                if proactive.isComparing { ProgressView("Picking the strongest deals…") }
                if let shortlist = proactive.shortlist {
                    Label(shortlist.mode == "ai" ? "AI shortlist" : "Basic value checks", systemImage: "sparkles").font(.subheadline)
                    Text(shortlist.summary).font(.footnote).foregroundStyle(.secondary)
                }
                ForEach(proactive.bestOffers.prefix(3), id: \.0.id) { offer, pick in
                    FreshOfferRow(offer: offer, pick: pick)
                }
                if proactive.bestOffers.isEmpty && !proactive.isComparing {
                    Text(proactive.isChecking ? "Your shortlist will appear as nearby checks finish." : "No standout deal yet. Check nearby offers to find the next good saving.").font(.footnote).foregroundStyle(.secondary)
                }
                if let message = proactive.comparisonMessage { Text(message).font(.footnote).foregroundStyle(.secondary) }
                if !proactive.currentOffers.isEmpty {
                    Button("Pick my best deals") { Task { await proactive.compareOffers() } }.disabled(proactive.isComparing || !proactive.aiEnabled)
                }
            }
            Section("For you") {
                if recommendations.isEmpty && !repository.isLoading {
                    ContentUnavailableView("Make room for your next saving", systemImage: "sparkles",
                                           description: Text("Save an offer from Deals Near Me. Your receipts help us choose useful offers."))
                    NavigationLink("Explore deals near me") { NearbyDealsView() }
                }
                ForEach(recommendations.prefix(proactive.currentOffers.isEmpty ? 8 : 6)) { item in DealCard(deal: item.deal, why: item.why, distance: item.distanceMeters) }
            }
            Section("Fresh deals near you") {
                Toggle("Find deals automatically", isOn: $proactive.enabled)
                    .onChange(of: proactive.enabled) { _, value in
                        if value { location.requestCurrentLocation() }
                    }
                if proactive.isChecking { ProgressView("Checking local and delivery offers…") }
                if let status = proactive.status { Text(status).font(.footnote).foregroundStyle(.secondary) }
                if let checked = proactive.lastChecked {
                    Text("Last checked " + checked.formatted(date: .omitted, time: .shortened)).font(.caption).foregroundStyle(.secondary)
                }
                if proactive.currentOffers.isEmpty && !proactive.isChecking {
                    Text("Allow location to check nearby restaurants and shops automatically. Delivery offers are checked too.").font(.footnote)
                }
                ForEach(proactive.currentOffers.prefix(12)) { offer in FreshOfferRow(offer: offer) }
                Button("Check deals now") {
                    if let current = location.currentLocation {
                        Task { await proactive.find(around: current, force: true) }
                    } else { location.requestCurrentLocation() }
                }.disabled(proactive.isChecking)
                NavigationLink("Deal alerts and interests") { ProactiveDealSettingsView() }
            }
            if let response, !response.stacks.isEmpty {
                Section("Worth checking together") {
                    ForEach(response.stacks.filter { stack in stack.componentIDs.allSatisfy { id in repository.active.contains { $0.id == id } } }) { stack in
                        VStack(alignment: .leading, spacing: 8) {
                            Text("\(stack.store) · Possible stack").font(.headline)
                            ForEach(stack.components, id: \.self) { Text("• " + $0).font(.subheadline) }
                            Text(stack.terms).font(.footnote).foregroundStyle(.secondary)
                            Text("Combined savings are unknown until terms are checked.").font(.caption).foregroundStyle(.secondary)
                        }.padding(.vertical, 6)
                    }
                }
            }
            Section("Around you") {
                Button { location.requestCurrentLocation() } label: { Label("Find nearby businesses", systemImage: "location") }
                if let message = location.lastError ?? locationMessage { Text(message).font(.footnote).foregroundStyle(.secondary) }
                ForEach(nearby.prefix(3)) { store in
                    NavigationLink { NearbyBusinessDealsView(store: store) } label: {
                        VStack(alignment: .leading) {
                            Text(store.name).font(.headline)
                            Text(String(format: "%.1f mi · %@", store.distanceMeters / 1609.344, store.address)).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                NavigationLink("All deals near me") { NearbyDealsView() }
            }
            if !localDeals.isEmpty {
                Section("Saved on this iPhone") {
                    ForEach(localDeals.filter { !$0.isUsed && ($0.expirationDate.map { Calendar.current.startOfDay(for: $0) >= Calendar.current.startOfDay(for: .now) } ?? true) }.prefix(3)) { LocalDealRow(deal: $0) }
                }
            }
        }.navigationTitle("For You")
            .toolbar { NavigationLink { BackendSettingsView() } label: { Label("Settings", systemImage: "gearshape") } }
            .task { await refresh(); if proactive.shortlist == nil { await proactive.compareOffers() } }
            .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
                if proactive.enabled { location.requestCurrentLocation() }
            }
            .task(id: profileSignature) { await rank() }
            .onChange(of: repository.deals) { _, _ in Task { await rank() } }
            .onChange(of: location.currentLocation) { _, new in
                guard let new else { return }
                Task {
                    do {
                        nearby = try await StoreLocatorService().nearbyStores(around: new); locationMessage = nil
                        await rank()
                    }
                    catch { locationMessage = "Nearby businesses could not be loaded. Try again shortly." }
                }
            }
            .refreshable { await refresh(force: true) }
    }
    private func refresh(force: Bool = false) async {
        await repository.refresh(force: force)
        // Existing permission only; first-time access remains an explicit user choice.
        if location.authorizationStatus == .authorizedAlways || location.authorizationStatus == .authorizedWhenInUse {
            location.requestCurrentLocation()
        }
        await rank()
    }
    private func rank() async {
        guard !isRanking else { return }
        guard !repository.isLoading, repository.isConnected, repository.pending.isEmpty else { response = nil; return }
        isRanking = true
        defer { isRanking = false }
        do { response = try await ForYouService().recommendations(receipts: profile, nearby: nearby) }
        catch { response = nil }
    }
}
