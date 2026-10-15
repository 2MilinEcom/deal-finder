import Foundation
import Combine
import CoreLocation

@MainActor
final class LocationManager: NSObject, ObservableObject, CLLocationManagerDelegate {
    static let shared = LocationManager()
    @Published var authorizationStatus: CLAuthorizationStatus
    @Published var currentLocation: CLLocation?
    @Published var lastError: String?
    @Published var monitoredStore: String?
    @Published var isLocating = false
    private let manager = CLLocationManager()
    private var requestingLocation = false
    private var timeoutTask: Task<Void, Never>?
    override init() {
        authorizationStatus = manager.authorizationStatus
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        monitoredStore = manager.monitoredRegions.compactMap { Self.storeName(from: $0.identifier) }.first
    }
    func requestWhenInUsePermission() { requestingLocation = true; manager.requestWhenInUseAuthorization() }
    func requestAlwaysPermission() {
        if authorizationStatus == .notDetermined { manager.requestWhenInUseAuthorization() }
        else if authorizationStatus == .authorizedWhenInUse { manager.requestAlwaysAuthorization() }
    }
    func requestCurrentLocation() {
        lastError = nil; requestingLocation = true
        switch manager.authorizationStatus {
        case .notDetermined: manager.requestWhenInUseAuthorization()
        case .authorizedAlways, .authorizedWhenInUse:
            isLocating = true; manager.requestLocation()
            timeoutTask?.cancel()
            timeoutTask = Task { [weak self] in
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
                guard let self, self.isLocating else { return }
                self.isLocating = false; self.lastError = "Your location is taking longer than expected. Try again outside."
            }
        case .denied: lastError = "Location is off for Deal Finder. You can enable it in iPhone Settings."; isLocating = false
        case .restricted: lastError = "Location access is restricted on this iPhone."; isLocating = false
        @unknown default: lastError = "Location is unavailable."; isLocating = false
        }
    }
    func startMonitoringStore(store: String, center: CLLocationCoordinate2D, radius: CLLocationDistance = 200) {
        guard authorizationStatus == .authorizedAlways else {
            lastError = "Allow Always location for arrival reminders, then tap Monitor again."; requestAlwaysPermission(); return
        }
        guard CLLocationManager.isMonitoringAvailable(for: CLCircularRegion.self), CLLocationCoordinate2DIsValid(center),
              manager.maximumRegionMonitoringDistance > 0 else {
            lastError = "Arrival monitoring is unavailable on this device."; return
        }
        stopMonitoringAllRegions()
        let region = CLCircularRegion(center: center, radius: min(max(radius, 100), manager.maximumRegionMonitoringDistance),
                                      identifier: "deal-finder::" + (store.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? store))
        region.notifyOnEntry = true; region.notifyOnExit = false
        manager.startMonitoring(for: region)
        lastError = nil
    }
    func enableProactiveLocation() {
        guard ProactiveDealService.shared.enabled, ProactiveDealService.shared.alerts else { return }
        if authorizationStatus == .authorizedAlways {
            if CLLocationManager.significantLocationChangeMonitoringAvailable() { manager.startMonitoringSignificantLocationChanges() }
        } else { requestAlwaysPermission() }
    }
    func monitorProactiveStores(_ stores: [StoreLocation]) {
        guard authorizationStatus == .authorizedAlways,
              CLLocationManager.isMonitoringAvailable(for: CLCircularRegion.self), manager.maximumRegionMonitoringDistance > 0 else { return }
        for region in manager.monitoredRegions where region.identifier.hasPrefix("proactive::") { manager.stopMonitoring(for: region) }
        for store in stores.prefix(10) {
            let region = CLCircularRegion(center: store.coordinate, radius: min(250, manager.maximumRegionMonitoringDistance),
                identifier: "proactive::" + (store.chain.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? store.chain))
            region.notifyOnEntry = true; region.notifyOnExit = false; manager.startMonitoring(for: region)
        }
    }
    func stopProactiveMonitoring() {
        manager.stopMonitoringSignificantLocationChanges()
        for region in manager.monitoredRegions where region.identifier.hasPrefix("proactive::") { manager.stopMonitoring(for: region) }
    }
    func stopMonitoringAllRegions() {
        for region in manager.monitoredRegions where region.identifier.hasPrefix("deal-finder::") { manager.stopMonitoring(for: region) }
        monitoredStore = nil
    }
    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        authorizationStatus = manager.authorizationStatus
        if authorizationStatus == .authorizedAlways || authorizationStatus == .authorizedWhenInUse {
            if requestingLocation { requestCurrentLocation() }
            if authorizationStatus == .authorizedAlways { enableProactiveLocation(); ProactiveDealService.shared.updateArrivalMonitoring() }
        } else if authorizationStatus == .denied || authorizationStatus == .restricted {
            lastError = "Location access is unavailable. Saved deals still work."; isLocating = false
            timeoutTask?.cancel(); requestingLocation = false
        }
    }
    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let value = locations.last, value.horizontalAccuracy >= 0, abs(value.timestamp.timeIntervalSinceNow) < 300 else {
            lastError = "A recent location is not available. Try again."; isLocating = false; return
        }
        currentLocation = value; lastError = nil; isLocating = false; requestingLocation = false; timeoutTask?.cancel()
    }
    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        isLocating = false; requestingLocation = false; timeoutTask?.cancel()
        lastError = "Your location could not be found. Check location access and try again."
    }
    func locationManager(_ manager: CLLocationManager, didStartMonitoringFor region: CLRegion) { monitoredStore = Self.storeName(from: region.identifier) }
    func locationManager(_ manager: CLLocationManager, monitoringDidFailFor region: CLRegion?, withError error: Error) {
        monitoredStore = nil; lastError = "The arrival reminder could not be enabled. Try again."
    }
    func locationManager(_ manager: CLLocationManager, didEnterRegion region: CLRegion) {
        if region.identifier.hasPrefix("proactive::"), let store = String(region.identifier.dropFirst("proactive::".count)).removingPercentEncoding {
            Task { await ProactiveDealService.shared.arrived(at: store) }; return
        }
        guard let store = Self.storeName(from: region.identifier) else { return }
        // Region callbacks have limited execution time. Use persisted offers instead of waiting on the Mac.
        let offers = DealRepository.shared.active.filter { $0.store.caseInsensitiveCompare(store) == .orderedSame }
        Task { await NotificationManager.shared.sendSmartArrivalNotification(store: store, deals: offers) }
    }
    private static func storeName(from identifier: String) -> String? {
        guard identifier.hasPrefix("deal-finder::") else { return nil }
        return String(identifier.dropFirst("deal-finder::".count)).removingPercentEncoding
    }
}
