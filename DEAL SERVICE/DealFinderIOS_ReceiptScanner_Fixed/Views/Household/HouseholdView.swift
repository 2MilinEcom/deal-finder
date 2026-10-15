import SwiftUI
import SwiftData

struct HouseholdView: View {
    @Environment(\.modelContext)
    private var modelContext

    @Query(sort: \HouseholdMember.name)
    private var members: [HouseholdMember]

    @State
    private var newName = ""

    @State private var saveError: String?

    var body: some View {
        List {
            Section("Members") {
                ForEach(members) { member in
                    Text(member.name)
                }
            }

            if let saveError { Text(saveError).foregroundStyle(.red) }

            Section("Add member") {
                TextField("Name", text: $newName)

                Button("Add") {
                    let trimmed = newName
                        .trimmingCharacters(in: .whitespacesAndNewlines)

                    guard !trimmed.isEmpty else {
                        return
                    }

                    guard !members.contains(where: { $0.name.caseInsensitiveCompare(trimmed) == .orderedSame }) else {
                        saveError = "That household member already exists."; return
                    }
                    modelContext.insert(HouseholdMember(name: trimmed))
                    do { try modelContext.save(); newName = ""; saveError = nil }
                    catch { modelContext.rollback(); saveError = "The household member could not be saved." }
                }
            }
        }
        .navigationTitle("Household")
        .task {
            seedDefaultMemberIfNeeded()
        }
    }

    private func seedDefaultMemberIfNeeded() {
        guard members.isEmpty else {
            return
        }

        modelContext.insert(HouseholdMember(name: "Me"))
        do { try modelContext.save() }
        catch { modelContext.rollback(); saveError = "The default member could not be saved." }
    }
}
