import Foundation
import SwiftData

@Model
final class Deal {
    @Attribute(.unique) var id: UUID
    var store: String
    var title: String
    var dealType: String
    var discountValue: Double
    var minimumSpend: Double
    var promoCode: String
    var expirationDate: Date?
    var source: String
    var notes: String
    var isUsed: Bool
    var householdMemberName: String
    var createdAt: Date

    init(
        id: UUID = UUID(),
        store: String,
        title: String,
        dealType: String = "Other",
        discountValue: Double = 0,
        minimumSpend: Double = 0,
        promoCode: String = "",
        expirationDate: Date? = nil,
        source: String = "",
        notes: String = "",
        isUsed: Bool = false,
        householdMemberName: String = "Me",
        createdAt: Date = .now
    ) {
        self.id = id
        self.store = store
        self.title = title
        self.dealType = dealType
        self.discountValue = discountValue
        self.minimumSpend = minimumSpend
        self.promoCode = promoCode
        self.expirationDate = expirationDate
        self.source = source
        self.notes = notes
        self.isUsed = isUsed
        self.householdMemberName = householdMemberName
        self.createdAt = createdAt
    }
}
