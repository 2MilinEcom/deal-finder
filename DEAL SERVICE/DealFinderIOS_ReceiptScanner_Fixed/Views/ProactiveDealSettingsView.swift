import SwiftUI
import CoreLocation

struct ProactiveDealSettingsView: View {
    @StateObject private var service = ProactiveDealService.shared
    @StateObject private var location = LocationManager.shared
    var body: some View {
        Form {
            Section("Automatic discovery") {
                Toggle("Find deals automatically", isOn: $service.enabled)
                TextField("Things you want deals on", text: $service.interests, axis: .vertical)
                Text("For example: hair care, pizza, groceries or coffee. Nearby places determine which stores we check; these interests focus broader searches.").font(.footnote).foregroundStyle(.secondary)
                Button("Use my location and check now") {
                    service.enabled = true; location.requestCurrentLocation()
                    if let current = location.currentLocation { Task { await service.find(around: current, force: true) } }
                }.disabled(service.isChecking)
                if let status = service.status { Text(status).font(.footnote) }
            }
            Section("Best deals") {
                Toggle("Use AI to choose my best deals", isOn: $service.aiEnabled)
                Text("AI compares public offer details, advertised savings and restrictions. Your Mac adds interest and distance preferences. Receipts, business addresses and precise location are kept out of the AI request.").font(.footnote).foregroundStyle(.secondary)
            }
            Section("Let me know about deals") {
                if service.alerts {
                    Toggle("Deal notifications", isOn: $service.alerts)
                } else {
                    Button("Enable deal notifications") { Task { await service.enableAlerts() } }
                }
                Button("Allow nearby checks while away") { location.requestAlwaysPermission() }
                Text(location.authorizationStatus == .authorizedAlways ? "Background location is allowed." : "Choose Always location in iPhone Settings for checks as you move around.").font(.footnote).foregroundStyle(.secondary)
                Text("Checks run when you open the app, move to a new area, or iOS grants background time. Alerts favor newly found strong offers, at most once every four hours between 8 AM and 9 PM. Your Mac must be awake and connected to Tailscale.").font(.footnote).foregroundStyle(.secondary)
            }
            Section("Offer coverage") {
                Text("Public Domino’s and DoorDash offers can load without a search key. Broader searches, including CVS hair-product sales and other local stores, need Search setup on your Mac. Account-only offers still require checking the merchant’s app; public offers do not confirm your eligibility.").font(.footnote)
            }
        }.navigationTitle("Deal alerts")
    }
}

struct FreshOfferRow: View {
    let offer: FreshNearbyOffer
    var pick: BestDealPick? = nil
    @State private var message: String?
    @State private var busy = false
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(offer.store + (offer.distance.map { String(format: " · %.1f mi", $0 / 1609.344) } ?? " · Delivery")).font(.subheadline).foregroundStyle(.secondary)
            if let pick {
                Label(pick.label, systemImage: pick.verdict == "great" ? "bolt.fill" : "sparkles")
                    .font(.subheadline.weight(.semibold)).foregroundStyle(pick.verdict == "great" ? .green : .primary)
            }
            Text(offer.candidate.title).font(.headline)
            if let pick {
                Text(pick.reason).font(.subheadline)
                if !pick.personalReasons.isEmpty { Text(pick.personalReasons.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary) }
                Text("Watch for: " + pick.catchText).font(.footnote).foregroundStyle(.secondary)
            }
            if pick == nil { Text(offer.candidate.summary).font(.footnote).lineLimit(5) }
            if let expiration = offer.candidate.expirationDate { Text("Ends " + expiration).font(.caption) }
            if let url = offer.candidate.safeURL { Link("See offer and eligibility", destination: url) }
            Button("Save offer") {
                busy = true
                Task {
                    defer { busy = false }
                    do { try await NearbyDealSearchService.shared.save(offer.candidate); message = "Saved" }
                    catch { message = error.localizedDescription }
                }
            }.disabled(busy || message == "Saved")
            if let message { Text(message).font(.caption).foregroundStyle(.secondary) }
        }.padding(.vertical, 6)
    }
}
