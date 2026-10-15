import Foundation
import Combine

@MainActor
final class DealRepository: ObservableObject {
    static let shared = DealRepository()
    @Published private(set) var deals: [RemoteDeal] = []
    @Published private(set) var isLoading = false
    @Published private(set) var message: String?
    @Published private(set) var lastUpdated: Date?
    @Published private(set) var pending: [Int: String] = [:]
    @Published private(set) var isConnected = false
    private var cacheURL: URL?
    private var cachedServer = ""
    private struct Snapshot: Codable {
        let deals: [RemoteDeal]; let pending: [Int: String]; let updated: Date?; let server: String?
    }
    init(storageDirectory: URL? = nil) {
        do {
            let directory = try storageDirectory ?? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                        appropriateFor: nil, create: true)
            cacheURL = directory.appendingPathComponent("deals-cache.json")
            if let cacheURL, let data = try? Data(contentsOf: cacheURL) {
                let snapshot = try JSONDecoder().decode(Snapshot.self, from: data)
                deals = snapshot.deals; pending = snapshot.pending; lastUpdated = snapshot.updated
                cachedServer = snapshot.server ?? BackendConfiguration.address
            } else { cachedServer = BackendConfiguration.address }
        } catch { message = "Saved deals could not be loaded. Your receipts remain stored on this iPhone." }
    }
    var active: [RemoteDeal] { deals.filter(\.isActive) }
    private func persist() throws {
        guard let cacheURL else { throw BackendError.rejected("Local storage is unavailable.") }
        let data = try JSONEncoder().encode(Snapshot(deals: deals, pending: pending, updated: lastUpdated, server: cachedServer))
        try data.write(to: cacheURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }
    func refresh(force: Bool = false) async {
        guard !isLoading else { return }
        guard cachedServer == BackendConfiguration.address else {
            message = "The server address changed. Restore the previous address or reset the downloaded cache in Settings before syncing."
            return
        }
        if !force, isConnected, let lastUpdated, Date().timeIntervalSince(lastUpdated) < 300 { return }
        isLoading = true; message = nil
        defer { isLoading = false }
        do {
            for (id, state) in pending.sorted(by: { $0.key < $1.key }) {
                do { try await PythonSyncService.shared.setState(id: id, status: state) }
                catch BackendError.conflict(let reason) {
                    if pending[id] == state { pending.removeValue(forKey: id) }
                    message = reason; try persist(); continue
                }
                catch BackendError.unknownDeal {
                    if pending[id] == state { pending.removeValue(forKey: id) }
                    message = BackendError.unknownDeal.localizedDescription; try persist(); continue
                }
                // A user may update the same offer while this request is in flight.
                if pending[id] == state { pending.removeValue(forKey: id) }
                try persist()
            }
            var fresh = try await PythonSyncService.shared.fetchDeals(includeHistory: true)
            for index in fresh.indices {
                if let state = pending[fresh[index].id] {
                    fresh[index].status = state; fresh[index].used = state == "used" ? 1 : 0
                }
            }
            deals = fresh; lastUpdated = .now; isConnected = true
            try persist()
        } catch is CancellationError { }
        catch { isConnected = false; message = error.localizedDescription }
    }
    func setState(_ deal: RemoteDeal, _ state: String) async {
        guard let index = deals.firstIndex(where: { $0.id == deal.id }), !isLoading else { return }
        let oldDeals = deals, oldPending = pending
        deals[index].status = state; deals[index].used = state == "used" ? 1 : 0
        deals[index].redeemedAt = state == "used" ? ISO8601DateFormatter().string(from: .now) : nil
        pending[deal.id] = state
        do { try persist() }
        catch { deals = oldDeals; pending = oldPending; message = "Could not save this change on your iPhone. Please try again."; return }
        await refresh(force: true)
        if !pending.isEmpty { message = "Your change is saved on this iPhone and will sync when your Mac is available." }
    }
    func switchServer() throws {
        guard pending.isEmpty else { throw BackendError.rejected("Sync your pending deal changes with the previous server before switching.") }
        cachedServer = BackendConfiguration.address; deals = []; lastUpdated = nil; isConnected = false
        try persist()
    }
}
