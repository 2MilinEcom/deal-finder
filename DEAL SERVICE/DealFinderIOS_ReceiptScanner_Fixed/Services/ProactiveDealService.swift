import Foundation
import Combine
import CoreLocation
import BackgroundTasks

struct FreshNearbyOffer: Codable, Identifiable {
    let candidate: NearbyDealCandidate
    let store: String
    let address: String
    let distance: Double?
    let checkedAt: Date
    var id: String { candidate.id }
    var isFresh: Bool {
        guard Date().timeIntervalSince(checkedAt) < 86400 else { return false }
        guard let date = candidate.expirationDate else { return true }
        let format = DateFormatter(); format.locale = Locale(identifier: "en_US_POSIX"); format.dateFormat = "yyyy-MM-dd"
        return format.date(from: date).map { Calendar.current.startOfDay(for: $0) >= Calendar.current.startOfDay(for: .now) } ?? true
    }
}

struct DiscoveryPlace: Codable {
    let name: String; let chain: String; let address: String
    let latitude: Double; let longitude: Double; let distance: Double
    init(_ store: StoreLocation) {
        name = store.name; chain = store.chain; address = store.address
        latitude = store.coordinate.latitude; longitude = store.coordinate.longitude; distance = store.distanceMeters
    }
    var storeLocation: StoreLocation {
        StoreLocation(chain: chain, name: name, coordinate: .init(latitude: latitude, longitude: longitude), address: address, distanceMeters: distance)
    }
}

@MainActor
final class ProactiveDealService: ObservableObject {
    static let shared = ProactiveDealService()
    static let backgroundID = "com.sampleguzmanpersonalteam.DEALSERVICE.offers"
    @Published private(set) var offers: [FreshNearbyOffer] = []
    @Published private(set) var isChecking = false
    @Published private(set) var shortlist: BestDealsResponse?
    @Published private(set) var isComparing = false
    @Published private(set) var comparisonMessage: String?
    @Published private(set) var status: String?
    @Published private(set) var lastChecked: Date?
    @Published var enabled: Bool {
        didSet {
            defaults.set(enabled, forKey: "proactive-enabled")
            if !enabled { LocationManager.shared.stopProactiveMonitoring(); BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: Self.backgroundID) }
        }
    }
    @Published var aiEnabled: Bool {
        didSet { defaults.set(aiEnabled, forKey: "proactive-ai-enabled"); if !aiEnabled { shortlist = nil } }
    }
    @Published var alerts: Bool { didSet { defaults.set(alerts, forKey: "proactive-alerts"); if !alerts { LocationManager.shared.stopProactiveMonitoring() } } }
    @Published var interests: String { didSet { defaults.set(String(interests.prefix(300)), forKey: "proactive-interests") } }
    private var places: [DiscoveryPlace] = []
    private var subscriptions = Set<AnyCancellable>()
    private var locationTask: Task<Void, Never>?
    private var lastSearchLocation: CLLocation?
    private var sessionAddress: String
    private let defaults: UserDefaults
    private let judge: ([FreshNearbyOffer], String) async throws -> BestDealsResponse
    private let fetch: (String, String, String, Bool, String) async throws -> NearbyDealSearchResponse
    init(defaults: UserDefaults = .standard,
         judge: @escaping ([FreshNearbyOffer], String) async throws -> BestDealsResponse = BestDealsService.compare,
         fetch: @escaping (String, String, String, Bool, String) async throws -> NearbyDealSearchResponse = { name, chain, address, force, interests in
             try await NearbyDealSearchService.shared.search(businessName: name, chain: chain, address: address, forceRefresh: force, interests: interests)
         }) {
        self.defaults = defaults; self.fetch = fetch; self.judge = judge
        enabled = defaults.object(forKey: "proactive-enabled") == nil ? true : defaults.bool(forKey: "proactive-enabled")
        aiEnabled = defaults.object(forKey: "proactive-ai-enabled") == nil ? true : defaults.bool(forKey: "proactive-ai-enabled")
        alerts = defaults.bool(forKey: "proactive-alerts")
        interests = defaults.string(forKey: "proactive-interests") ?? "pizza, food delivery, hair care, shampoo and conditioner"
        sessionAddress = BackendConfiguration.address
        loadCache()
    }
    var currentOffers: [FreshNearbyOffer] { offers.filter(\.isFresh) }
    func start() {
        guard subscriptions.isEmpty else { return }
        LocationManager.shared.$currentLocation.compactMap { $0 }.sink { [weak self] location in
            guard let self, self.enabled else { return }
            if let last = self.lastSearchLocation, last.distance(from: location) < 500,
               let checked = self.lastChecked, Date().timeIntervalSince(checked) < 2700 { return }
            self.locationTask = Task { await self.find(around: location) }
        }.store(in: &subscriptions)
        scheduleBackgroundRefresh()
        if enabled && alerts { LocationManager.shared.enableProactiveLocation() }
    }
    func find(around location: CLLocation, force: Bool = false) async {
        guard enabled, !isChecking else { return }
        do {
            let stores = try await StoreLocatorService().nearbyStores(around: location, radiusMeters: 5000)
            guard !Task.isCancelled else { return }
            lastSearchLocation = location
            await check(stores: stores, force: force)
        } catch { status = "Nearby places could not be loaded. Your recent finds are still here." }
    }
    static func selectedPlaces(_ stores: [StoreLocation]) -> [StoreLocation] {
        let ordered = stores.sorted {
            func priority(_ s: StoreLocation) -> Int { ["CVS", "Domino's", "Walgreens", "Target"].firstIndex(of: s.chain) ?? 10 }
            return priority($0) == priority($1) ? $0.distanceMeters < $1.distanceMeters : priority($0) < priority($1)
        }
        var seen = Set<String>()
        return Array(ordered.filter { seen.insert($0.chain.lowercased()).inserted }.prefix(4))
    }
    func check(stores: [StoreLocation], force: Bool = false) async {
        guard enabled, !isChecking else { return }
        if sessionAddress != BackendConfiguration.address {
            offers = []; places = []; lastChecked = nil; shortlist = nil; sessionAddress = BackendConfiguration.address
        }
        if !force, let checked = lastChecked, Date().timeIntervalSince(checked) < 2700,
           stores.first.map({ incoming in places.contains { saved in
               CLLocation(latitude: saved.latitude, longitude: saved.longitude).distance(from: CLLocation(latitude: incoming.coordinate.latitude, longitude: incoming.coordinate.longitude)) < 500
           } }) == true { return }
        shortlist = nil
        isChecking = true; status = "Checking nearby stores, restaurants and delivery promos…"
        defer { isChecking = false; scheduleBackgroundRefresh() }
        let selected = Self.selectedPlaces(stores)
        places = selected.map(DiscoveryPlace.init)
        var newOffers: [FreshNearbyOffer] = []
        var failures = 0; var broadConfigured = true; var successes = 0
        // DoorDash is a delivery service, never shown as a physical nearby store.
        let searches: [(String, String, String, Double?)] = selected.map { ($0.name, $0.chain, $0.address, Optional($0.distanceMeters)) }
            + [("DoorDash", "DoorDash", "", nil)]
        for (name, chain, address, distance) in searches {
            guard enabled, !Task.isCancelled else { return }
            do {
                let found = try await fetch(name, chain, address, force, interests)
                successes += 1; broadConfigured = broadConfigured && (found.broadSearchConfigured ?? true)
                if found.message != nil && found.results.isEmpty { failures += 1 }
                newOffers += found.results.prefix(4).map { FreshNearbyOffer(candidate: $0, store: chain, address: address, distance: distance, checkedAt: .now) }
                var publishedIDs = Set<String>()
                offers = newOffers.filter { $0.isFresh && publishedIDs.insert($0.id).inserted }
                saveCache()
            } catch is CancellationError { return }
            catch { failures += 1 }
        }
        guard enabled, !Task.isCancelled else { return }
        if successes == 0 { status = "Your Mac is unavailable. Automatic checks will retry when it reconnects."; return }
        var seen = Set<String>()
        offers = newOffers.filter { $0.isFresh && seen.insert($0.id).inserted }
        lastChecked = .now
        status = "Checked \(searches.count) nearby and delivery sources · \(offers.count) public offers found."
        if !broadConfigured { status! += " Broader store sales need Search setup on your Mac; some merchant pages block access." }
        else if failures > 0 { status! += " \(failures) sources could not be checked." }
        await compareOffers()
        saveCache()
        if alerts {
            updateArrivalMonitoring()
            await notifyNewOffers()
        }
    }
    func compareOffers() async {
        guard enabled, aiEnabled, !isComparing, !Task.isCancelled, !currentOffers.isEmpty else { return }
        isComparing = true; comparisonMessage = nil
        defer { isComparing = false }
        do {
            let result = try await judge(currentOffers, interests)
            guard !Task.isCancelled else { return }
            let eligible = Set(result.eligibleIDs)
            offers.removeAll { !eligible.contains($0.id) }
            shortlist = result; saveCache()
        } catch is CancellationError { return }
        catch { comparisonMessage = "AI selection could not connect. Your recent offers are still available." }
    }
    var bestOffers: [(FreshNearbyOffer, BestDealPick)] {
        guard let shortlist else { return [] }
        return shortlist.picks.compactMap { pick in
            currentOffers.first { $0.id == pick.id }.map { ($0, pick) }
        }
    }
    func refreshCached() async {
        guard enabled else { return }
        await check(stores: places.map(\.storeLocation))
    }
    func enableAlerts() async {
        alerts = await NotificationManager.shared.requestPermission()
        if alerts {
            LocationManager.shared.enableProactiveLocation()
            updateArrivalMonitoring()
            await notifyNewOffers()
        } else { status = "Allow notifications for Deal Finder in iPhone Settings to receive deal alerts." }
    }
    func updateArrivalMonitoring() {
        guard enabled, alerts else { return }
        LocationManager.shared.monitorProactiveStores(places.map(\.storeLocation).filter { store in
            currentOffers.contains { $0.store == store.chain && $0.distance != nil }
        })
    }
    func arrived(at store: String) async {
        guard enabled, alerts else { return }
        await notifyNewOffers(store: store)
    }
    private func notifyNewOffers(store: String? = nil) async {
        let hour = Calendar.current.component(.hour, from: .now)
        guard (8..<21).contains(hour), Date().timeIntervalSince1970 - defaults.double(forKey: "proactive-last-alert") >= 4 * 3600 else { return }
        let notified = Set(defaults.stringArray(forKey: "proactive-notified") ?? [])
        let preferredIDs = shortlist?.picks.filter { $0.verdict == "great" || $0.verdict == "good" }.map(\.id) ?? []
        let preferred = preferredIDs.compactMap { id in currentOffers.first { $0.id == id } }
        let fresh = preferred.filter { !notified.contains($0.id) && (store == nil || $0.store == store) }
        guard let first = fresh.first else { return }
        let sent = await NotificationManager.shared.sendPublicOfferNotification(title: first.store + " offer to check",
            body: first.candidate.title + ". Check location, account eligibility and terms in Deal Finder.")
        if sent {
            defaults.set(Date().timeIntervalSince1970, forKey: "proactive-last-alert")
            // Mark only the offer actually announced; other finds may be announced later.
            defaults.set(Array((Array(notified) + [first.id]).suffix(300)), forKey: "proactive-notified")
        }
    }
    func scheduleBackgroundRefresh() {
        guard enabled else { return }
        let request = BGAppRefreshTaskRequest(identifier: Self.backgroundID)
        request.earliestBeginDate = Date(timeIntervalSinceNow: 3600)
        try? BGTaskScheduler.shared.submit(request)
    }
    private func saveCache() {
        defaults.set(try? JSONEncoder().encode(offers), forKey: "proactive-offers")
        defaults.set(try? JSONEncoder().encode(places), forKey: "proactive-places")
        defaults.set(lastChecked, forKey: "proactive-checked")
        defaults.set(sessionAddress, forKey: "proactive-server")
        defaults.set(status, forKey: "proactive-status")
        defaults.set(2, forKey: "proactive-cache-version")
        defaults.set(try? JSONEncoder().encode(shortlist), forKey: "proactive-shortlist")
    }
    private func loadCache() {
        guard defaults.string(forKey: "proactive-server") == sessionAddress, defaults.integer(forKey: "proactive-cache-version") == 2 else { return }
        if let data = defaults.data(forKey: "proactive-offers") { offers = (try? JSONDecoder().decode([FreshNearbyOffer].self, from: data)) ?? [] }
        if let data = defaults.data(forKey: "proactive-places") { places = (try? JSONDecoder().decode([DiscoveryPlace].self, from: data)) ?? [] }
        lastChecked = defaults.object(forKey: "proactive-checked") as? Date
        status = defaults.string(forKey: "proactive-status")
        if let data = defaults.data(forKey: "proactive-shortlist") { shortlist = try? JSONDecoder().decode(BestDealsResponse.self, from: data) }
    }
}
