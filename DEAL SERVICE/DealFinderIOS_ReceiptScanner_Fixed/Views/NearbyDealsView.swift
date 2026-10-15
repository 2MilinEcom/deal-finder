import SwiftUI
import CoreLocation

struct NearbyDealsView: View {
    @StateObject private var location = LocationManager.shared
    @State private var stores: [StoreLocation] = []
    @State private var loading = false
    @State private var message: String?
    var body: some View {
        List {
            Section {
                Button { location.requestCurrentLocation() } label: { Label("Find deals near me", systemImage: "location.fill") }.disabled(loading)
                Text("Choose a business to search current public offers. Each search is cached for 45 minutes.").font(.footnote).foregroundStyle(.secondary)
                if loading { ProgressView("Finding nearby businesses…") }
                if let error = location.lastError ?? message { Text(error).font(.footnote).foregroundStyle(.secondary) }
            }
            Section("Nearby businesses") {
                if stores.isEmpty && !loading {
                    ContentUnavailableView("Discover your neighborhood", systemImage: "map", description: Text("Use your location to see nearby restaurants, shops, pharmacies and warehouse stores."))
                }
                ForEach(stores) { store in
                    NavigationLink { NearbyBusinessDealsView(store: store) } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(store.name).font(.headline)
                            Text(String(format: "%.1f mi · %@", store.distanceMeters / 1609.344, store.address)).font(.caption).foregroundStyle(.secondary)
                        }.padding(.vertical, 3)
                    }
                }
            }
        }.navigationTitle("Deals Near Me")
            .onChange(of: location.currentLocation) { _, value in
                guard let value else { return }
                Task { await load(value) }
            }
            .task { if let value = location.currentLocation { await load(value) } }
    }
    private func load(_ current: CLLocation) async {
        guard !loading else { return }
        loading = true; message = nil
        defer { loading = false }
        do { stores = try await StoreLocatorService().nearbyStores(around: current) }
        catch { message = "Nearby businesses could not be loaded. Check your connection and try again." }
    }
}

struct NearbyBusinessDealsView: View {
    let store: StoreLocation
    @State private var candidates: [NearbyDealCandidate] = []
    @State private var loading = false
    @State private var searched = false
    @State private var message: String?
    @State private var busyIDs: Set<String> = []
    @State private var hiddenIDs: Set<String> = []
    @State private var savedIDs: Set<String> = []
    var body: some View {
        List {
            Section {
                Text(store.address).foregroundStyle(.secondary)
                Text(String(format: "%.1f mi away", store.distanceMeters / 1609.344)).font(.caption)
                Button { Task { await search(force: searched) } } label: {
                    Label(searched ? "Refresh current offers" : "Search current offers", systemImage: "magnifyingglass")
                }.disabled(loading)
                if loading { ProgressView("Searching public sources…") }
                if let message { Text(message).font(.footnote).foregroundStyle(.secondary) }
            }
            Section("Offers to review") {
                if searched && !loading && candidates.isEmpty {
                    ContentUnavailableView("No current offers found", systemImage: "tag", description: Text("Check the business’s own rewards app or try again later."))
                }
                ForEach(candidates.filter { !hiddenIDs.contains($0.id) }) { candidate in
                    VStack(alignment: .leading, spacing: 10) {
                        Text(candidate.title).font(.headline)
                        Text(candidate.sourceLabel).font(.caption).foregroundStyle(.secondary)
                        Text(candidate.summary).font(.subheadline)
                        Text(candidate.expirationDate.map { "Expiration: " + $0 } ?? "Expiration unknown").font(.caption).foregroundStyle(.secondary)
                        Text(candidate.caveat ?? "Confirm current terms and eligibility.").font(.footnote).foregroundStyle(.secondary)
                        if let url = candidate.safeURL { Link("Open offer source", destination: url).font(.subheadline.weight(.semibold)) }
                        HStack {
                            Button(savedIDs.contains(candidate.id) ? "Saved" : "Save offer") {
                                Task { await action(candidate, save: true) }
                            }.buttonStyle(.borderedProminent).disabled(savedIDs.contains(candidate.id))
                            Button("Not for me") { Task { await action(candidate, save: false) } }.buttonStyle(.bordered)
                        }.disabled(busyIDs.contains(candidate.id))
                        if busyIDs.contains(candidate.id) { ProgressView() }
                    }.padding(.vertical, 8)
                }
            }
            Section {
                Text("Public search results may be expired, regional or account-specific. Official source means the website belongs to the merchant; it does not guarantee that an offer is valid. Verify terms before purchase.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }.navigationTitle(store.name).navigationBarTitleDisplayMode(.inline)
            .task { if !searched { await search(force: false) } }
    }
    private func search(force: Bool) async {
        guard !loading else { return }
        loading = true; message = nil
        defer { loading = false; searched = true }
        do {
            let response = try await NearbyDealSearchService.shared.search(businessName: store.name, chain: store.chain,
                                                                            address: store.address, forceRefresh: force)
            candidates = response.results
            message = response.message
        } catch { message = error.localizedDescription }
    }
    private func action(_ candidate: NearbyDealCandidate, save: Bool) async {
        busyIDs.insert(candidate.id)
        defer { busyIDs.remove(candidate.id) }
        do {
            if save { try await NearbyDealSearchService.shared.save(candidate); savedIDs.insert(candidate.id) }
            else {
                try await NearbyDealSearchService.shared.feedback(candidate, store: store.chain, action: "dismissed")
                hiddenIDs.insert(candidate.id)
            }
            message = save ? "Offer saved. Check the terms before using it." : "Thanks. This will help improve your recommendations."
        } catch { message = error.localizedDescription }
    }
}
