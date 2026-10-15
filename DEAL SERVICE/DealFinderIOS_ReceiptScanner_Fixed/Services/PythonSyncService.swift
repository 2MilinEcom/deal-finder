import Foundation

struct RemoteDeal: Identifiable, Codable, Hashable {
    let id: Int
    let store: String
    let title: String
    let dealType: String?
    let discountValue: Double?
    let minimumSpend: Double?
    let promoCode: String?
    let expirationDate: String?
    let source: String?
    let notes: String?
    var used: Int
    let householdMember: String?
    let createdAt: String?
    var status: String?
    var redeemedAt: String?
    let sourceURL: String?
    let verificationStatus: String?
    let sourceType: String?
    let discoveredAt: String?

    enum CodingKeys: String, CodingKey {
        case id, store, title, source, notes, used, status
        case dealType = "deal_type", discountValue = "discount_value", minimumSpend = "minimum_spend"
        case promoCode = "promo_code", expirationDate = "expiration_date", householdMember = "household_member"
        case createdAt = "created_at", redeemedAt = "redeemed_at", sourceURL = "source_url"
        case verificationStatus = "verification_status", sourceType = "source_type", discoveredAt = "discovered_at"
    }
    var memberDisplay: String { householdMember.flatMap { $0.isEmpty ? nil : $0 } ?? "Household" }
    var typeDisplay: String { dealType.flatMap { $0.isEmpty ? nil : $0 } ?? "Other" }
    var expiration: Date? {
        guard let expirationDate else { return nil }
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd"
        return f.date(from: String(expirationDate.prefix(10)))
    }
    var isExpired: Bool { expiration.map { Calendar.current.startOfDay(for: $0) < Calendar.current.startOfDay(for: .now) } ?? false }
    var isActive: Bool { used == 0 && status != "dismissed" && status != "expired" && !isExpired }
    var expirationDisplay: String { expiration.map { "Expires " + $0.formatted(date: .abbreviated, time: .omitted) } ?? "Expiration unknown" }
    var sourceDisplay: String {
        if verificationStatus == "verified" { return "Verified offer" }
        switch sourceType {
        case "receipt": return "Imported receipt · reviewed by you"
        case "official": return "Official source · verify terms"
        case "delivery": return "Delivery app · account eligibility varies"
        case "social": return "Social promotion · unverified"
        case "web": return "Web candidate · unverified"
        default: return "Saved Deal Finder deal · verify terms"
        }
    }
    // Percent, points, BOGO and rewards must not be displayed as cash savings.
    var dollarValue: Double? {
        let kind = typeDisplay.lowercased()
        guard kind.contains("$") || kind.contains("dollar") || kind == "cash back" else { return nil }
        return discountValue.flatMap { $0 > 0 ? $0 : nil }
    }
    var safeSourceURL: URL? {
        guard let raw = sourceURL ?? source, let url = URL(string: raw),
              ["https", "http"].contains(url.scheme ?? ""), url.host != nil else { return nil }
        return url
    }
}

struct DealMutationResponse: Decodable {
    let ok: Bool; let dealId: Int; let used: Bool
    enum CodingKeys: String, CodingKey { case ok, used; case dealId = "deal_id" }
}

@MainActor
struct PythonSyncService {
    static let shared = PythonSyncService()
    func health() async throws -> Bool {
        struct Health: Decodable { let ok: Bool }
        let value: Health = try await BackendClient.shared.send("health", timeout: 8)
        return value.ok
    }
    func fetchDeals(store: String? = nil, householdMember: String? = nil, includeHistory: Bool = false) async throws -> [RemoteDeal] {
        var query: [URLQueryItem] = []
        if let store { query.append(URLQueryItem(name: "store", value: store)) }
        if let householdMember { query.append(URLQueryItem(name: "household_member", value: householdMember)) }
        if includeHistory { query.append(URLQueryItem(name: "include_history", value: "true")) }
        return try await BackendClient.shared.send("deals", query: query, timeout: 12)
    }
    func markDealUsed(id: Int) async throws { try await setState(id: id, status: "used") }
    func setState(id: Int, status: String) async throws {
        let data = try JSONEncoder().encode(["status": status])
        let response: DealMutationResponse = try await BackendClient.shared.send("deals/\(id)/state", method: "POST", body: data, timeout: 12)
        guard response.ok else { throw BackendError.rejected("Your Mac did not confirm the update.") }
    }
}
