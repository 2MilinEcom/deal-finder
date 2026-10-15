import Foundation

struct NearbyDealCandidate: Codable, Identifiable, Hashable {
    let id: String; let title: String; let summary: String; let url: String
    let sourceType: String; let sourceLabel: String; let score: Double; let publishedDate: String?
    let expirationDate: String?; let verificationStatus: String?; let caveat: String?
    enum CodingKeys: String, CodingKey {
        case id, title, summary, url, score, caveat
        case sourceType = "source_type", sourceLabel = "source_label", publishedDate = "published_date"
        case expirationDate = "expiration_date", verificationStatus = "verification_status"
    }
    var safeURL: URL? {
        guard let u = URL(string: url), ["http", "https"].contains(u.scheme ?? ""), u.host != nil else { return nil }
        return u
    }
}
struct NearbyDealSearchResponse: Codable {
    let businessName: String; let chain: String?; let address: String?; let searchedAt: String
    let resultCount: Int; let results: [NearbyDealCandidate]
    let message: String?; let broadSearchConfigured: Bool?
    enum CodingKeys: String, CodingKey {
        case businessName = "business_name", chain, address, searchedAt = "searched_at", resultCount = "result_count", results, message, broadSearchConfigured = "broad_search_configured"
    }
}

@MainActor
final class NearbyDealSearchService {
    static let shared = NearbyDealSearchService()
    private var memory: [String: (Date, NearbyDealSearchResponse)] = [:]
    private var inFlight: [String: Task<NearbyDealSearchResponse, Error>] = [:]
    private init() {}
    func search(businessName: String, chain: String?, address: String?, forceRefresh: Bool = false, interests: String = "") async throws -> NearbyDealSearchResponse {
        let key = [BackendConfiguration.address, businessName, chain ?? "", address ?? "", interests].joined(separator: "|")
        if !forceRefresh, let cached = memory[key], Date().timeIntervalSince(cached.0) < 2700 { return cached.1 }
        if let task = inFlight[key] { return try await task.value }
        let task = Task<NearbyDealSearchResponse, Error> {
            let payload: [String: Any] = ["business_name": businessName, "chain": chain ?? businessName,
                                           "address": address ?? "", "force_refresh": forceRefresh, "interests": String(interests.prefix(300))]
            return try await BackendClient.shared.send("nearby-deals/search", method: "POST",
                                                       body: JSONSerialization.data(withJSONObject: payload), timeout: 30)
        }
        inFlight[key] = task
        defer { inFlight.removeValue(forKey: key) }
        let response = try await task.value; memory[key] = (.now, response)
        return response
    }
    func save(_ candidate: NearbyDealCandidate) async throws {
        struct Saved: Decodable { let ok: Bool }
        let response: Saved = try await BackendClient.shared.send("candidates/save", method: "POST",
                           body: JSONEncoder().encode(["candidate_id": candidate.id]))
        guard response.ok else { throw BackendError.invalidData }
        await DealRepository.shared.refresh(force: true)
    }
    func feedback(_ candidate: NearbyDealCandidate, store: String, action: String) async throws {
        struct Result: Decodable { let ok: Bool }
        let result: Result = try await BackendClient.shared.send("feedback", method: "POST", body: JSONEncoder().encode(
            ["candidate_id": candidate.id, "store": store, "title": candidate.title, "action": action]))
        guard result.ok else { throw BackendError.invalidData }
    }
}
