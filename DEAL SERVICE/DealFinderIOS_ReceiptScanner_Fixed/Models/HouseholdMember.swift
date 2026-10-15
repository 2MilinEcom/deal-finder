import Foundation
import SwiftData

@Model
final class HouseholdMember {
    @Attribute(.unique) var id: UUID
    @Attribute(.unique) var name: String
    var createdAt: Date

    init(
        id: UUID = UUID(),
        name: String,
        createdAt: Date = .now
    ) {
        self.id = id
        self.name = name
        self.createdAt = createdAt
    }
}
