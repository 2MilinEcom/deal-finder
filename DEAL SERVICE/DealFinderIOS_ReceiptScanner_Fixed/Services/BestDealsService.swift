import Foundation

struct BestDealPick: Codable, Identifiable {
    let id: String
    let verdict: String
    let label: String
    let reason: String
    let catchText: String
    let confidence: String
    let score: Double
    let personalReasons: [String]
    enum CodingKeys: String, CodingKey {
        case id, verdict, label, reason, confidence, score
        case catchText = "catch", personalReasons = "personal_reasons"
    }
}
struct BestDealsResponse: Codable {
    let mode: String
    let summary: String
    let picks: [BestDealPick]
    let eligibleIDs: [String]
    let checkedAt: String
    enum CodingKeys: String, CodingKey { case mode, summary, picks; case eligibleIDs = "eligible_ids", checkedAt = "checked_at" }
}

@MainActor
struct BestDealsService {
    static func compare(_ offers: [FreshNearbyOffer], interests: String) async throws -> BestDealsResponse {
        struct Candidate: Encodable { let id: String; let distance_meters: Double? }
        struct Request: Encodable { let candidates: [Candidate]; let interests: String }
        let payload = Request(candidates: offers.filter(\.isFresh).prefix(32).map { Candidate(id: $0.id, distance_meters: $0.distance) }, interests: String(interests.prefix(300)))
        return try await BackendClient.shared.send("best-deals", method: "POST", body: JSONEncoder().encode(payload), timeout: 25)
    }
}
