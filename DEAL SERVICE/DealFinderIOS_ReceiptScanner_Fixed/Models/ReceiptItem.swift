import Foundation
import SwiftData

@Model
final class ReceiptItem {
    @Attribute(.unique) var id: UUID
    var itemName: String
    var price: Double
    var quantity: Double
    var rawLine: String
    var receipt: Receipt?

    init(
        id: UUID = UUID(),
        itemName: String,
        price: Double = 0,
        quantity: Double = 1,
        rawLine: String = "",
        receipt: Receipt? = nil
    ) {
        self.id = id
        self.itemName = itemName
        self.price = price
        self.quantity = quantity
        self.rawLine = rawLine
        self.receipt = receipt
    }
}
