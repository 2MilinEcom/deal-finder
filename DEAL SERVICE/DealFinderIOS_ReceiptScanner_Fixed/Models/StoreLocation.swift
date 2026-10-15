import Foundation
import CoreLocation
import MapKit

struct StoreLocation: Identifiable, Hashable {
    var id: String {
        "\(name.lowercased())|\(coordinate.latitude)|\(coordinate.longitude)"
    }
    let chain: String
    let name: String
    let coordinate: CLLocationCoordinate2D
    let address: String
    let distanceMeters: CLLocationDistance

    static func == (lhs: StoreLocation, rhs: StoreLocation) -> Bool {
        lhs.id == rhs.id
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }
}
