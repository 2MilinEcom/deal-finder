import Foundation

struct ReceiptProfile: Encodable {
    let store: String
    let items: [String]
}
struct Recommendation: Decodable, Identifiable {
    var id: Int { deal.id }
    let deal: RemoteDeal
    let why: [String]
    let score: Double
    let distanceMeters: Double?
    enum Keys: String, CodingKey { case why, score; case distanceMeters = "distance_meters" }
    init(from decoder: Decoder) throws {
        deal = try RemoteDeal(from: decoder)
        let c = try decoder.container(keyedBy: Keys.self)
        why = try c.decode([String].self, forKey: .why); score = try c.decode(Double.self, forKey: .score)
        distanceMeters = try c.decodeIfPresent(Double.self, forKey: .distanceMeters)
    }
    init(deal: RemoteDeal, why: [String], score: Double, distance: Double?) {
        self.deal = deal; self.why = why; self.score = score; distanceMeters = distance
    }
}
struct PossibleStack: Decodable, Identifiable {
    var id: String { componentIDs.map(String.init).joined(separator: "-") }
    let store: String; let label: String; let components: [String]; let terms: String; let componentIDs: [Int]
    enum CodingKeys: String, CodingKey { case store, label, components, terms; case componentIDs = "component_ids" }
}
struct ForYouResponse: Decodable {
    let recommendations: [Recommendation]; let stacks: [PossibleStack]
}

@MainActor
struct ForYouService {
    func recommendations(receipts: [ReceiptProfile], nearby: [StoreLocation]) async throws -> ForYouResponse {
        struct Business: Encodable {
            let name: String; let chain: String; let distance_meters: Double
        }
        struct Request: Encodable {
            let receipts: [ReceiptProfile]; let nearby: [Business]; let limit = 8
        }
        let request = Request(receipts: Array(receipts.prefix(100)), nearby: Array(nearby.prefix(25)).map {
            Business(name: $0.name, chain: $0.chain, distance_meters: $0.distanceMeters)
        })
        return try await BackendClient.shared.send("for-you", method: "POST", body: JSONEncoder().encode(request))
    }
    func local(deals: [RemoteDeal], receipts: [ReceiptProfile], nearby: [StoreLocation]) -> [Recommendation] {
        let history = deals.filter { $0.used == 1 }
        return deals.filter(\.isActive).map { deal in
            var score = 20.0
            var reasons: [String] = []
            let visits = receipts.filter { $0.store.caseInsensitiveCompare(deal.store) == .orderedSame }.count
            if visits > 0 { score += Double(min(visits * 5, 20)); reasons.append("You shop at this business") }
            let category = ReceiptParser.category(deal.title + " " + (deal.notes ?? ""))
            if let category, receipts.contains(where: { $0.items.contains { ReceiptParser.category($0) == category } }) {
                score += 18; reasons.append("Matches items in your receipts")
            }
            if history.contains(where: { $0.store.caseInsensitiveCompare(deal.store) == .orderedSame }) {
                score += 12; reasons.append("You have used offers here before")
            }
            let dismissals = deals.filter { $0.status == "dismissed" && category != nil && ReceiptParser.category($0.title + " " + ($0.notes ?? "")) == category }.count
            score -= Double(min(dismissals * 12, 48))
            score += min(deal.dollarValue ?? 0, 20)
            let distance = nearby.filter { $0.chain.caseInsensitiveCompare(deal.store) == .orderedSame }.map(\.distanceMeters).min()
            if let distance { score += max(0, 15 - distance / 200); reasons.append("Close to your current location") }
            if deal.status == "saved" { score += 8; reasons.append("You saved this offer") }
            if let expiration = deal.expiration, expiration.timeIntervalSinceNow < 3 * 86400 {
                score += 6; reasons.append("Expires soon")
            }
            if deal.sourceType == "official" { score += 6 }
            return Recommendation(deal: deal, why: reasons.isEmpty ? ["An active offer from your saved deals"] : reasons,
                                  score: score, distance: distance)
        }.sorted { $0.score > $1.score }.prefix(8).map { $0 }
    }
}
