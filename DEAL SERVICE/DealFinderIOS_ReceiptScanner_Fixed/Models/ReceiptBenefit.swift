import Foundation
import SwiftData

@Model
final class ReceiptBenefit {
    @Attribute(.unique) var id: UUID
    var direction: String
    var benefitType: String
    var value: Double
    var percentValue: Double
    var benefitDescription: String
    var expirationDate: Date?
    var rawLine: String
    var receipt: Receipt?

    init(
        id: UUID = UUID(),
        direction: String = "Detected",
        benefitType: String = "Other",
        value: Double = 0,
        percentValue: Double = 0,
        benefitDescription: String = "",
        expirationDate: Date? = nil,
        rawLine: String = "",
        receipt: Receipt? = nil
    ) {
        self.id = id
        self.direction = direction
        self.benefitType = benefitType
        self.value = value
        self.percentValue = percentValue
        self.benefitDescription = benefitDescription
        self.expirationDate = expirationDate
        self.rawLine = rawLine
        self.receipt = receipt
    }
}
