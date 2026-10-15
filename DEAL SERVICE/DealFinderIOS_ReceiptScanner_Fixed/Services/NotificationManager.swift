import Foundation
import UserNotifications

@MainActor
final class NotificationManager: NSObject, UNUserNotificationCenterDelegate {
    static let shared = NotificationManager()
    private override init() { super.init(); UNUserNotificationCenter.current().delegate = self }
    func requestPermission() async -> Bool {
        (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])) ?? false
    }
    func sendPublicOfferNotification(title: String, body: String) async -> Bool {
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        guard settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional else { return false }
        let content = UNMutableNotificationContent(); content.title = title; content.body = body; content.sound = .default
        do {
            try await center.add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
            return true
        } catch { return false }
    }
    func sendArrivalNotification(title: String, body: String) async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        guard settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional else { return }
        let content = UNMutableNotificationContent(); content.title = title; content.body = body; content.sound = .default
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        try? await UNUserNotificationCenter.current().add(request)
    }
    func sendSmartArrivalNotification(store: String, deals: [RemoteDeal], test: Bool = false) async {
        let active = deals.filter { $0.isActive && ($0.verificationStatus == "verified" || ($0.sourceType ?? "saved") == "saved" || ($0.sourceType == "receipt" && $0.verificationStatus == "user_reviewed")) }
        guard !active.isEmpty else { return }
        let now = Date(); let key = "arrival-last-" + store.lowercased()
        if !test {
            let hour = Calendar.current.component(.hour, from: now)
            guard (8..<21).contains(hour), now.timeIntervalSince1970 - UserDefaults.standard.double(forKey: key) >= 6 * 3600,
                  now.timeIntervalSince1970 - UserDefaults.standard.double(forKey: "arrival-last-any") >= 3600 else { return }
        }
        let value = active.compactMap(\.dollarValue).max()
        let body = "You have \(active.count) saved offer(s)." + (value.map { " Individual savings up to \($0.formatted(.currency(code: "USD")))." } ?? "") + " Want to review the terms?"
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        guard settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional else { return }
        await sendArrivalNotification(title: store + " nearby", body: body)
        if !test {
            UserDefaults.standard.set(now.timeIntervalSince1970, forKey: key)
            UserDefaults.standard.set(now.timeIntervalSince1970, forKey: "arrival-last-any")
        }
    }
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions { [.banner, .sound] }
}
