import Foundation
import MapKit
import CoreLocation

@MainActor
struct StoreLocatorService {
    private static var cached: (location: CLLocation, radius: Double, date: Date, stores: [StoreLocation])?
    private static var flight: Task<[StoreLocation], Error>?
    private let queries = ["Domino’s Pizza", "CVS Pharmacy", "restaurant", "coffee", "pharmacy", "grocery store", "convenience store", "department store", "shopping", "Costco", "Target", "Walmart", "Safeway"]
    func nearbyStores(around location: CLLocation, radiusMeters: CLLocationDistance = 2000) async throws -> [StoreLocation] {
        guard location.horizontalAccuracy >= 0, location.horizontalAccuracy <= 2000,
              abs(location.timestamp.timeIntervalSinceNow) <= 300 else {
            throw BackendError.rejected("A recent location is needed. Refresh your location and try again.")
        }
        let radius = min(max(radiusMeters, 500), 5000)
        if let cache = Self.cached, cache.location.distance(from: location) < 150,
           cache.radius == radius, Date().timeIntervalSince(cache.date) < 300 {
            return sorted(cache.stores, from: location)
        }
        if let flight = Self.flight { return sorted(try await flight.value, from: location) }
        let task = Task<[StoreLocation], Error> {
            var (stores, successes) = await searchAll(location, radius)
            if stores.count < 5 {
                let expanded = await searchAll(location, 5000); stores += expanded.0; successes += expanded.1
            }
            guard successes > 0 else { throw BackendError.rejected("Map search is unavailable. Try again shortly.") }
            return sorted(stores, from: location)
        }
        Self.flight = task
        defer { Self.flight = nil }
        let stores = try await task.value
        Self.cached = (location, radius, .now, stores)
        return stores
    }
    private func searchAll(_ location: CLLocation, _ radius: Double) async -> ([StoreLocation], Int) {
        await withTaskGroup(of: ([StoreLocation], Bool).self) { group in
            for query in queries {
                group.addTask { await search(query, location, radius) }
            }
            var stores: [StoreLocation] = []; var successes = 0
            for await result in group { stores += result.0; successes += result.1 ? 1 : 0 }
            return (stores, successes)
        }
    }
    private func search(_ query: String, _ location: CLLocation, _ radius: Double) async -> ([StoreLocation], Bool) {
        let request = MKLocalSearch.Request()
        request.naturalLanguageQuery = query; request.resultTypes = .pointOfInterest
        request.region = MKCoordinateRegion(center: location.coordinate, latitudinalMeters: radius * 2, longitudinalMeters: radius * 2)
        do {
            let response = try await MKLocalSearch(request: request).start()
            return (response.mapItems.compactMap { item in
                let place = item.placemark
                guard let point = place.location, let name = item.name else { return nil }
                let distance = location.distance(from: point)
                guard distance <= radius else { return nil }
                let address = [place.subThoroughfare, place.thoroughfare, place.locality, place.administrativeArea].compactMap { $0 }.joined(separator: " ")
                return StoreLocation(chain: Self.chain(name), name: name, coordinate: point.coordinate, address: address, distanceMeters: distance)
            }, true)
        } catch { return ([], false) }
    }
    private func sorted(_ stores: [StoreLocation], from location: CLLocation) -> [StoreLocation] {
        let updated = stores.map { store in
            StoreLocation(chain: store.chain, name: store.name, coordinate: store.coordinate, address: store.address,
                          distanceMeters: location.distance(from: CLLocation(latitude: store.coordinate.latitude, longitude: store.coordinate.longitude)))
        }.sorted { $0.distanceMeters < $1.distanceMeters }
        var unique: [StoreLocation] = []
        for store in updated {
            if !unique.contains(where: { other in
                other.name.caseInsensitiveCompare(store.name) == .orderedSame &&
                CLLocation(latitude: other.coordinate.latitude, longitude: other.coordinate.longitude).distance(
                    from: CLLocation(latitude: store.coordinate.latitude, longitude: store.coordinate.longitude)) < 75
            }) { unique.append(store) }
        }
        return Array(unique.prefix(25))
    }
    static func chain(_ name: String) -> String {
        for (needle, canonical) in [("domino", "Domino's"), ("cvs", "CVS"), ("walgreen", "Walgreens"), ("jack in the box", "Jack in the Box"),
                                     ("costco", "Costco"), ("target", "Target"), ("walmart", "Walmart"), ("wal-mart", "Walmart"), ("safeway", "Safeway")] {
            if name.localizedCaseInsensitiveContains(needle) { return canonical }
        }
        return name
    }
}
