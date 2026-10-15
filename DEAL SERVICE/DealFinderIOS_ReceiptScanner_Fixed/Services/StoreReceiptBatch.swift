import Foundation
import SwiftData
import CryptoKit

/// Bounded, read-only discovery. No credential fields, cookie access, network interception or purchase actions.
struct StoreReceiptPage {
    struct Link: Equatable { let url: URL; let label: String }
    let text: String
    let url: URL
    let signingIn: Bool
    let links: [Link]
    let hasTargetLoadMore: Bool

    static let script = """
    (() => {
      const visible = e => e.getClientRects().length > 0;
      return {
        hasTargetLoadMore: Array.from(document.querySelectorAll('button,[role=button]')).some(e => visible(e) && (e.innerText || e.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ') === 'Load more purchases'),
        text: document.body ? document.body.innerText : '', url: location.href,
        signingIn: Array.from(document.querySelectorAll('input[type=password]')).some(visible),
        links: Array.from(document.querySelectorAll('a[href]')).filter(visible).slice(0, 500)
          .map(e => ({url: e.href, label: (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 200)}))
      };
    })()
    """
    init?(values: [String: Any], merchant: ReceiptMerchant) {
        guard let raw = values["url"] as? String, let url = URL(string: raw), merchant.permits(url),
              let text = values["text"] as? String, text.count <= 100000 else { return nil }
        self.url = url; self.text = text; signingIn = values["signingIn"] as? Bool == true
        hasTargetLoadMore = values["hasTargetLoadMore"] as? Bool == true
        links = (values["links"] as? [[String: String]] ?? []).compactMap { value in
            guard let raw = value["url"], let url = URL(string: raw), merchant.permits(url),
                  url.fragment == nil, let label = value["label"], !label.isEmpty else { return nil }
            return Link(url: url, label: label)
        }
    }
    static func kind(of link: Link) -> String? {
        let label = link.label.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        // Never follow actions that can alter purchases, account settings or rewards.
        let forbidden = ["cancel", "return item", "start a return", "reorder", "buy", "cart", "checkout", "pay", "delete", "prescription", "pharmacy", "refill", "gift receipt"]
        guard !forbidden.contains(where: label.contains) else { return nil }
        if ["purchase history", "order history", "orders & returns", "orders and returns", "digital receipts", "in-warehouse receipts", "in-store purchases"].contains(label) { return "history" }
        if ["view receipt", "print receipt", "receipt", "view order details", "order details", "view details", "purchase details"].contains(label) { return "receipt" }
        if ["next", "next page", "older purchases", "older orders"].contains(label) { return "next" }
        return nil
    }
    var receipt: ParsedReceipt? {
        guard !signingIn else { return nil }
        let path = url.path.lowercased()
        guard !["/help", "/faq", "/prescription", "/refill"].contains(where: path.contains),
              url.host?.hasPrefix("help.") != true, url.host?.hasPrefix("customerservice.") != true else { return nil }
        let textLower = text.lowercased()
        guard textLower.contains("receipt") || textLower.contains("order details") || textLower.contains("purchase details") else { return nil }
        // History summaries with several purchases must never be saved as a single receipt.
        let totalLines = text.components(separatedBy: .newlines).filter {
            $0.trimmingCharacters(in: .whitespaces).range(of: #"^(?:grand\s+)?total\b"#, options: [.regularExpression, .caseInsensitive]) != nil
        }
        guard totalLines.count == 1 else { return nil }
        let lines = text.components(separatedBy: .newlines).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        var normalizedLines: [String] = []; var index = 0
        while index < lines.count {
            // Websites often render a total label and amount in separate text nodes.
            if index + 1 < lines.count,
               lines[index].range(of: #"^(?:(?:grand\s+)?total|subtotal|sub total|sales tax|tax)$"#, options: [.regularExpression, .caseInsensitive]) != nil,
               lines[index + 1].range(of: #"^\$?\s*\d+(?:,\d{3})*\.\d{2}$"#, options: .regularExpression) != nil {
                normalizedLines.append(lines[index] + " " + lines[index + 1]); index += 2
            } else { normalizedLines.append(lines[index]); index += 1 }
        }
        let parsed = ReceiptParser.parse(normalizedLines.joined(separator: "\n"), storeHint: url.host?.hasSuffix("target.com") == true ? "Target" : nil)
        guard let total = parsed.total, total.isFinite, total >= 0, parsed.date != nil, !parsed.items.isEmpty else { return nil }
        return parsed
    }
}

@MainActor
struct StoreReceiptBatchSaver {
    static func targetFingerprint(text: String, url: URL? = nil) -> String? {
        var path: String?
        if let url, TargetStoreHistoryImporter.validPurchaseURL(url) { path = url.path.trimmingCharacters(in: CharacterSet(charactersIn: "/")) }
        else if let start = text.range(of: "Purchase details", options: .caseInsensitive) {
            let section = String(text[start.upperBound...].components(separatedBy: .newlines).prefix(8).joined(separator: "\n"))
            if let range = section.range(of: #"(?m)^#?\d+(?:-\d+){3}\s*$"#, options: .regularExpression) {
                let id = String(section[range]).trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: "#", with: "")
                path = "orders/stores/" + id
            }
        }
        guard let path else { return nil }
        return "target.com:purchase:" + SHA256.hash(data: Data(path.utf8)).map { String(format: "%02x", $0) }.joined()
    }
    static func save(page: StoreReceiptPage, merchant: ReceiptMerchant, context: ModelContext) throws -> Bool {
        guard let parsed = page.receipt, let date = parsed.date, let total = parsed.total else { return false }
        // Stable content fingerprint works across tracking URLs and repeat imports without keeping account URLs.
        let normalized = page.text.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }.joined(separator: " ")
        let fingerprint = targetFingerprint(text: page.text, url: page.url) ?? merchant.domain + ":" + SHA256.hash(data: Data(normalized.utf8)).map { String(format: "%02x", $0) }.joined()
        if merchant.name == "Target", TargetStoreHistoryImporter.validPurchaseURL(page.url) {
            let store = "Target"
            let existing = try context.fetch(FetchDescriptor<Receipt>(predicate: #Predicate { $0.store == store }))
            if existing.contains(where: { $0.importFingerprint == fingerprint || targetFingerprint(text: $0.ocrText) == fingerprint }) { return false }
        }
        let predicate = #Predicate<Receipt> { $0.importFingerprint == fingerprint }
        guard try context.fetch(FetchDescriptor<Receipt>(predicate: predicate)).isEmpty else { return false }
        let receipt = Receipt(store: merchant.name, purchaseDate: date, total: total,
                              notes: (["Imported from store history. Check any unclear details before relying on them."] + parsed.warnings).joined(separator: "\n"), ocrText: page.text)
        receipt.importFingerprint = fingerprint; receipt.subtotal = parsed.subtotal; receipt.tax = parsed.tax
        receipt.items = parsed.items.map { ReceiptItem(itemName: $0.name, price: $0.price, quantity: $0.quantity, rawLine: $0.rawLine, receipt: receipt) }
        receipt.benefits = parsed.benefits.map { ReceiptBenefit(direction: "Detected", benefitType: $0.kind, value: $0.value,
            percentValue: $0.percentValue, benefitDescription: $0.description, expirationDate: $0.expiration, rawLine: $0.rawLine, receipt: receipt) }
        context.insert(receipt)
        do { try context.save() }
        catch { context.delete(receipt); throw error }
        return true
    }
}
