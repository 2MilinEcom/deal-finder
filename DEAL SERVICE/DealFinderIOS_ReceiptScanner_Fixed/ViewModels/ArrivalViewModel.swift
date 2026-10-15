import Foundation
import Combine
import CoreLocation

@MainActor
final class ArrivalViewModel: ObservableObject {
    @Published var nearbyStores: [StoreLocation] = []
    @Published var selectedStore: StoreLocation?
    @Published var storeDeals: [RemoteDeal] = []
    @Published var isLoadingStores = false
    @Published var isLoadingDeals = false
    @Published var errorMessage: String?
    @Published var updatingDealIDs: Set<Int> = []
    func refresh(using location: CLLocation?) async {
        guard let location else { errorMessage = "Find your location first."; return }
        guard !isLoadingStores else { return }
        isLoadingStores = true; errorMessage = nil
        defer { isLoadingStores = false }
        do {
            nearbyStores = try await StoreLocatorService().nearbyStores(around: location)
            selectedStore = nearbyStores.first(where: { $0.id == selectedStore?.id }) ?? nearbyStores.first
            if let selectedStore { await loadDeals(for: selectedStore) }
        } catch { errorMessage = error.localizedDescription }
    }
    func selectStore(_ store: StoreLocation) async { selectedStore = store; await loadDeals(for: store) }
    func loadDeals(for store: StoreLocation) async {
        storeDeals = []; isLoadingDeals = true
        defer { isLoadingDeals = false }
        await DealRepository.shared.refresh()
        guard selectedStore?.id == store.id else { return }
        storeDeals = DealRepository.shared.active.filter { $0.store.caseInsensitiveCompare(store.chain) == .orderedSame }
        errorMessage = DealRepository.shared.message
    }
    func refreshSelectedStoreDeals() async {
        await DealRepository.shared.refresh(force: true)
        if let selectedStore { await loadDeals(for: selectedStore) }
    }
    func markUsed(_ deal: RemoteDeal) async {
        guard !updatingDealIDs.contains(deal.id) else { return }
        updatingDealIDs.insert(deal.id)
        await DealRepository.shared.setState(deal, "used")
        updatingDealIDs.remove(deal.id)
        if let selectedStore { await loadDeals(for: selectedStore) }
    }
    var knownDollarValue: Double { storeDeals.compactMap(\.dollarValue).max() ?? 0 }
}
