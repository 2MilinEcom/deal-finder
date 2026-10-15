import SwiftUI
import SwiftData
import BackgroundTasks

@main
struct DealFinderApp: App {
    @UIApplicationDelegateAdaptor(DealFinderLifecycle.self) private var lifecycle
    @StateObject private var location = LocationManager.shared
    init() {
        #if DEBUG
        // A local device setup can provision the Mac access token without embedding
        // it in the app or exposing it in chat. Release builds ignore this path.
        if let server = ProcessInfo.processInfo.environment["DEAL_FINDER_SETUP_SERVER"],
           let token = ProcessInfo.processInfo.environment["DEAL_FINDER_SETUP_TOKEN"],
           !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
           (try? BackendConfiguration.validatedURL(server)) != nil {
            do {
                try ServerCredential.save(token, for: server)
                BackendConfiguration.address = server
            } catch {
                // Settings remains available for manual provisioning if Keychain fails.
            }
        }
        if let server = ProcessInfo.processInfo.environment["DEAL_FINDER_TEST_SERVER"],
           (try? BackendConfiguration.validatedURL(server)) != nil {
            BackendConfiguration.address = server
        }
        #endif
        _ = NotificationManager.shared
    }
    var body: some Scene {
        WindowGroup {
            MainTabView()
                .task { ProactiveDealService.shared.start() }
        }
        .modelContainer(for: [
            Deal.self,
            HouseholdMember.self,
            Receipt.self,
            ReceiptItem.self,
            ReceiptBenefit.self
        ])
    }
}

@MainActor
final class DealFinderLifecycle: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        // Register before submitting; early submissions otherwise terminate the app.
        BGTaskScheduler.shared.register(forTaskWithIdentifier: ProactiveDealService.backgroundID, using: nil) { task in
            guard let refresh = task as? BGAppRefreshTask else { task.setTaskCompleted(success: false); return }
            let work = Task { @MainActor in
                await ProactiveDealService.shared.refreshCached()
            }
            refresh.expirationHandler = { work.cancel() }
            Task { @MainActor in
                await work.value
                refresh.setTaskCompleted(success: !work.isCancelled)
            }
        }
        ProactiveDealService.shared.start()
        return true
    }
}
