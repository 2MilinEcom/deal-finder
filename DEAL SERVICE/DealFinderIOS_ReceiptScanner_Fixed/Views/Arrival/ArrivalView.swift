import SwiftUI

struct ArrivalView: View {
    @StateObject private var location = LocationManager.shared
    @StateObject private var model = ArrivalViewModel()
    @ObservedObject private var repository = DealRepository.shared
    @State private var message: String?
    @State private var permissionBusy = false
    var body: some View {
        List {
            Section("Find a business") {
                Button { location.requestCurrentLocation() } label: { Label("Find nearby businesses", systemImage: "location.fill") }
                    .disabled(location.isLocating || model.isLoadingStores)
                if location.isLocating || model.isLoadingStores { ProgressView("Finding your neighborhood…") }
                Text("Choose one business for a gentle arrival reminder. Your saved offers remain available when your Mac is offline.")
                    .font(.footnote).foregroundStyle(.secondary)
                if let error = location.lastError ?? model.errorMessage ?? message { Text(error).font(.footnote).foregroundStyle(.secondary) }
            }
            Section("Nearby businesses") {
                if model.nearbyStores.isEmpty && !model.isLoadingStores {
                    ContentUnavailableView("Choose your next stop", systemImage: "mappin.and.ellipse", description: Text("Find nearby businesses using your current location."))
                }
                ForEach(model.nearbyStores) { store in
                    Button { Task { await model.selectStore(store) } } label: {
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(store.name).font(.headline)
                                Text(String(format: "%.1f mi · %@", store.distanceMeters / 1609.344, store.address)).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if model.selectedStore?.id == store.id { Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).accessibilityLabel("Selected") }
                        }
                    }.foregroundStyle(.primary)
                }
            }
            if let store = model.selectedStore {
                Section("Offers at \(store.chain)") {
                    if model.isLoadingDeals { ProgressView("Loading saved offers…") }
                    ForEach(model.storeDeals) { deal in DealCard(deal: deal, why: ["Saved offer at the business you selected"], distance: store.distanceMeters) }
                    if model.storeDeals.isEmpty && !model.isLoadingDeals { Text("No active saved offers here yet.").foregroundStyle(.secondary) }
                    NavigationLink("Discover current public offers") { NearbyBusinessDealsView(store: store) }
                }
                Section("Arrival reminder") {
                    if location.monitoredStore == store.chain {
                        Label("Reminder enabled for \(store.chain)", systemImage: "location.fill").foregroundStyle(.green)
                        Button("Stop arrival reminder", role: .destructive) { location.stopMonitoringAllRegions() }
                    } else {
                        Button("Remind me near \(store.chain)") {
                            permissionBusy = true
                            Task {
                                defer { permissionBusy = false }
                                guard await NotificationManager.shared.requestPermission() else {
                                    message = "Notifications are off. You can allow reminders in iPhone Settings."; return
                                }
                                location.startMonitoringStore(store: store.chain, center: store.coordinate)
                            }
                        }.disabled(permissionBusy)
                    }
                    Text("Always location access is needed for background reminders. Deal Finder monitors one business, stays quiet overnight, and limits repeated prompts. iOS controls when an arrival is detected.")
                        .font(.footnote).foregroundStyle(.secondary)
                    DisclosureGroup("Preview a reminder") {
                        Button("Send a preview notification") {
                            Task {
                                guard await NotificationManager.shared.requestPermission() else {
                                    message = "Allow notifications in iPhone Settings to preview a reminder."; return
                                }
                                await NotificationManager.shared.sendSmartArrivalNotification(store: store.chain, deals: model.storeDeals, test: true)
                                message = "Only active, trusted saved offers can trigger a reminder."
                            }
                        }
                    }
                }
            }
        }.navigationTitle("Arrival")
            .onChange(of: location.currentLocation) { _, new in Task { await model.refresh(using: new) } }
            .onChange(of: repository.deals) { _, _ in
                guard let store = model.selectedStore else { return }
                model.storeDeals = repository.active.filter { $0.store.caseInsensitiveCompare(store.chain) == .orderedSame }
            }
            .refreshable { location.requestCurrentLocation() }
            .task { if let current = location.currentLocation { await model.refresh(using: current) } }
    }
}
