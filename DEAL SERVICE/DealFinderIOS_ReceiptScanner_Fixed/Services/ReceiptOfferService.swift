import Foundation

@MainActor
enum ReceiptOfferService {
    static func save(receipt: Receipt, benefit: ReceiptBenefit) async throws {
        struct Request: Encodable {
            let receipt_id: UUID; let benefit_id: UUID; let store: String; let title: String
            let value: Double; let percent_value: Double; let expiration_date: String?
            let terms: String; let household_member: String; let confirmed_available = true
        }
        struct Response: Decodable { let ok: Bool; let deal_id: Int }
        guard benefit.benefitType == "Offer (review)", benefit.direction == "Available" else {
            throw BackendError.rejected("Review this offer and confirm it is unused before adding it to Deals.")
        }
        let formatter = DateFormatter(); formatter.locale = Locale(identifier: "en_US_POSIX"); formatter.dateFormat = "yyyy-MM-dd"
        let request = Request(receipt_id: receipt.id, benefit_id: benefit.id, store: receipt.store,
                              title: benefit.benefitDescription, value: benefit.value, percent_value: benefit.percentValue,
                              expiration_date: benefit.expirationDate.map(formatter.string), terms: benefit.rawLine,
                              household_member: receipt.householdMemberName)
        let _: Response = try await BackendClient.shared.send("receipt-offers/save", method: "POST", body: JSONEncoder().encode(request))
        await DealRepository.shared.refresh(force: true)
    }
}
