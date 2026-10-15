import Foundation

@MainActor
enum BackendConfiguration {
    // One configurable address; no provider credentials ever leave the Mac.
    static let defaultURL = "http://127.0.0.1:8001"
    static var address: String {
        get { UserDefaults.standard.string(forKey: "backendAddress") ?? defaultURL }
        set { UserDefaults.standard.set(newValue, forKey: "backendAddress") }
    }
    static func validatedURL(_ value: String) throws -> URL {
        guard let url = URL(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""), url.host != nil,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.isEmpty || url.path == "/" else { throw BackendError.invalidAddress }
        return url
    }
}

enum BackendError: LocalizedError {
    case invalidAddress, offline, invalidData, rejected(String), conflict(String), unknownDeal
    var errorDescription: String? {
        switch self {
        case .invalidAddress: return "Enter your Mac server address, such as http://127.0.0.1:8001."
        case .offline: return "Your Mac Deal Finder server is offline. Showing your most recent saved deals."
        case .invalidData: return "Your Mac returned data this app could not read. Check that both versions are up to date."
        case .rejected(let message), .conflict(let message): return message
        case .unknownDeal: return "This deal was removed from your Mac. Your other saved offers are still available."
        }
    }
}

private struct APIError: Decodable { let detail: String }

@MainActor
struct BackendClient {
    static let shared = BackendClient()
    static var session: URLSession = .shared
    func send<T: Decodable>(_ path: String, method: String = "GET", body: Data? = nil,
                             query: [URLQueryItem] = [], timeout: Double = 25) async throws -> T {
        let base = try BackendConfiguration.validatedURL(BackendConfiguration.address)
        guard var components = URLComponents(url: base.appendingPathComponent(path), resolvingAgainstBaseURL: false) else {
            throw BackendError.invalidAddress
        }
        components.queryItems = query.isEmpty ? nil : query
        guard let url = components.url else { throw BackendError.invalidAddress }
        var request = URLRequest(url: url)
        request.httpMethod = method; request.httpBody = body; request.timeoutInterval = timeout
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if let token = ServerCredential.token(for: BackendConfiguration.address) {
            request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        }
        let data: Data
        let response: URLResponse
        do { (data, response) = try await Self.session.data(for: request) }
        catch is CancellationError { throw CancellationError() }
        catch { if (error as? URLError)?.code == .cancelled { throw CancellationError() }; throw BackendError.offline }
        guard let http = response as? HTTPURLResponse else { throw BackendError.invalidData }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONDecoder().decode(APIError.self, from: data))?.detail
            if http.statusCode == 409 { throw BackendError.conflict(detail ?? "This offer cannot be used anymore.") }
            if http.statusCode == 404 && path.hasPrefix("deals/") { throw BackendError.unknownDeal }
            throw BackendError.rejected(detail ?? "Your Mac could not complete this request. Please try again.")
        }
        do { return try JSONDecoder().decode(T.self, from: data) }
        catch { throw BackendError.invalidData }
    }
}
