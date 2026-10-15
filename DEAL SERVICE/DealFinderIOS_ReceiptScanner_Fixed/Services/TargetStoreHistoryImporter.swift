import Foundation

/// Uses the actual Target controls supplied by the user's on-device layout report.
@MainActor
final class TargetStoreHistoryImporter {
    struct Result {
        var saved = 0
        var duplicates = 0
        var unreadable = 0
        var limited = false
    }
    private(set) var result = Result()
    static func purchaseLinks(_ page: StoreReceiptPage) -> [URL] {
        var seen = Set<URL>()
        return page.links.compactMap { link in
            guard link.label.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() == "view purchase",
                  validPurchaseURL(link.url), seen.insert(link.url).inserted else { return nil }
            return link.url
        }
    }
    static func validPurchaseURL(_ url: URL) -> Bool {
        guard url.scheme == "https", let host = url.host?.lowercased(), host == "target.com" || host.hasSuffix(".target.com") else { return false }
        return url.path.range(of: #"^/orders/stores/\d+(?:-\d+){3}/?$"#, options: .regularExpression) != nil
    }
    func run(history: StoreReceiptPage,
             expandHistory: (Int) async throws -> StoreReceiptPage,
             readPurchase: (URL) async throws -> StoreReceiptPage,
             save: (StoreReceiptPage) throws -> Bool,
             progress: (String) -> Void,
             limit: Int = 200) async throws -> Result {
        guard !history.signingIn else { throw BackendError.rejected("Finish signing in to Target first.") }
        var page = history; var urls = Self.purchaseLinks(page); var seen = Set(urls)
        var expansions = 0
        while page.hasTargetLoadMore && urls.count < limit && expansions < 20 {
            try Task.checkCancellation()
            progress("Finding Target purchases · \(urls.count) found")
            let oldCount = urls.count
            page = try await expandHistory(oldCount); expansions += 1
            guard !page.signingIn else { throw BackendError.rejected("Target needs you to sign in again.") }
            for url in Self.purchaseLinks(page) where seen.insert(url).inserted { urls.append(url) }
            guard urls.count > oldCount || !page.hasTargetLoadMore else {
                throw BackendError.rejected("Target’s Load more purchases button did not reveal more purchases. Try again later.")
            }
        }
        result.limited = urls.count > limit || page.hasTargetLoadMore
        guard !urls.isEmpty else { throw BackendError.rejected("No View purchase links found. Open Target’s store Purchase History and try again.") }
        for (index, url) in urls.prefix(limit).enumerated() {
            try Task.checkCancellation()
            progress("Reading Target purchase \(index + 1) of \(min(urls.count, limit)) · \(result.saved) saved")
            let receiptPage = try await readPurchase(url)
            guard !receiptPage.signingIn else { throw BackendError.rejected("Target needs you to sign in again. Earlier receipts are saved.") }
            // Never save a login redirect, history summary, help page or a different purchase.
            guard receiptPage.url.path.trimmingCharacters(in: CharacterSet(charactersIn: "/")) == url.path.trimmingCharacters(in: CharacterSet(charactersIn: "/")), receiptPage.receipt != nil,
                  let identity = StoreReceiptBatchSaver.targetFingerprint(text: receiptPage.text),
                  identity == StoreReceiptBatchSaver.targetFingerprint(text: "", url: url) else {
                result.unreadable += 1; continue
            }
            if try save(receiptPage) { result.saved += 1 } else { result.duplicates += 1 }
        }
        return result
    }
}
