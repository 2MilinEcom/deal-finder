import Foundation
import SwiftData

@Model
final class Receipt {
    @Attribute(.unique) var id: UUID
    var store: String
    var purchaseDate: Date
    var total: Double
    var notes: String
    var ocrText: String
    var householdMemberName: String
    var createdAt: Date
    var subtotal: Double? = nil
    var tax: Double? = nil
    var additionalImages: Data? = nil
    var importFingerprint: String? = nil
    var imageData: Data?

    @Relationship(deleteRule: .cascade, inverse: \ReceiptItem.receipt)
    var items: [ReceiptItem] = []

    @Relationship(deleteRule: .cascade, inverse: \ReceiptBenefit.receipt)
    var benefits: [ReceiptBenefit] = []

    init(
        id: UUID = UUID(),
        store: String = "Other",
        purchaseDate: Date = .now,
        total: Double = 0,
        notes: String = "",
        ocrText: String = "",
        householdMemberName: String = "Me",
        createdAt: Date = .now,
        imageData: Data? = nil
    ) {
        self.id = id
        self.store = store
        self.purchaseDate = purchaseDate
        self.total = total
        self.notes = notes
        self.ocrText = ocrText
        self.householdMemberName = householdMemberName
        self.createdAt = createdAt
        self.imageData = imageData
    }
}
