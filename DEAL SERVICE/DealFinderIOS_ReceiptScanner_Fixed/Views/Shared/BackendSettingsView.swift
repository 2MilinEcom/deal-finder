import SwiftUI

struct BackendSettingsView: View {
    @State private var address = BackendConfiguration.address
    @State private var token = ServerCredential.token(for: BackendConfiguration.address) ?? ""
    @State private var message: String?
    @State private var checking = false
    @StateObject private var repository = DealRepository.shared
    var body: some View {
        Form {
            Section("Your Mac server") {
                TextField("Server address", text: $address).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                Text("Connect your iPhone and Mac to Tailscale. The server address stays on this iPhone.").font(.footnote).foregroundStyle(.secondary)
                SecureField("Server access token (optional)", text: $token).textInputAutocapitalization(.never).autocorrectionDisabled()
                Button("Save and check connection") { Task { await save() } }.disabled(checking)
                if checking { ProgressView() }
                if let message { Text(message).font(.footnote) }
            }
            Section("Privacy") {
                Text("Receipt images and OCR stay on your iPhone. Recommendation requests send store names and reviewed item names to your Mac. Nearby search sends the selected business and address to your Mac and its search provider.")
                    .font(.footnote)
                Text("AI deal selection sends public offer details to OpenAI. Your receipts, nearby business addresses and precise location are kept out of that AI request. API keys stay on your Mac.").font(.footnote).foregroundStyle(.secondary)
            }
        }.navigationTitle("Settings")
    }
    private func save() async {
        checking = true
        defer { checking = false }
        let oldAddress = BackendConfiguration.address
        do {
            _ = try BackendConfiguration.validatedURL(address)
            let newAddress = address.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            if oldAddress != newAddress && !repository.pending.isEmpty {
                throw BackendError.rejected("Sync your pending deal changes before switching servers.")
            }
            try ServerCredential.save(token, for: newAddress)
            BackendConfiguration.address = newAddress
            guard try await PythonSyncService.shared.health() else { throw BackendError.rejected("Your Mac did not pass its connection check.") }
            if oldAddress != newAddress { try repository.switchServer() }
            await repository.refresh(force: true)
            message = repository.message ?? "Connected. Your server address is saved."
        } catch { BackendConfiguration.address = oldAddress; message = error.localizedDescription }
    }
}
