import SwiftUI

struct MainTabView: View {
    var body: some View {
        TabView {
            NavigationStack { HomeView() }.tabItem { Label("Home", systemImage: "house.fill") }
            NavigationStack { DealsView() }.tabItem { Label("Deals", systemImage: "tag.fill") }
            NavigationStack { ArrivalHubView() }.tabItem { Label("Arrival", systemImage: "location.fill") }
            NavigationStack { ReceiptsView() }.tabItem { Label("Receipts", systemImage: "receipt") }
            NavigationStack { HouseholdView() }.tabItem { Label("Household", systemImage: "person.2.fill") }
        }.tint(.green)
    }
}
