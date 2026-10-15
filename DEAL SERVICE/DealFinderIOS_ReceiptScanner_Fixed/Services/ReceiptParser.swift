import Foundation

struct ParsedReceiptItem: Identifiable {
    var id = UUID()
    var name: String
    var price: Double
    var quantity: Double = 1
    var rawLine: String
}
struct ParsedReceiptBenefit: Identifiable {
    var id = UUID()
    var description: String
    var value: Double
    var kind: String
    var rawLine: String
    var percentValue: Double = 0
    var expiration: Date?
    var available = false
}
struct ParsedReceipt {
    var store = ""
    var date: Date?
    var subtotal: Double?
    var tax: Double?
    var total: Double?
    var items: [ParsedReceiptItem] = []
    var benefits: [ParsedReceiptBenefit] = []
    var warnings: [String] = []
}

struct ReceiptParser {
    static func parse(_ text: String, storeHint: String? = nil) -> ParsedReceipt {
        if (storeHint == "Target" || text.localizedCaseInsensitiveContains("Target")),
           text.localizedCaseInsensitiveContains("Purchase details"), text.localizedCaseInsensitiveContains("Purchased on") {
            return parseTargetPurchase(text)
        }
        var result = ParsedReceipt()
        let lines = normalizedLines(text)
        var totals: [Double] = []; var subtotals: [Double] = []; var taxes: [Double] = []
        let merchants = ["CVS", "Walgreens", "Jack in the Box", "Costco", "Target", "Walmart", "Safeway", "Starbucks"]
        for line in lines.prefix(8) {
            if let merchant = merchants.first(where: { line.localizedCaseInsensitiveContains($0) }) { result.store = merchant; break }
        }
        if result.store.isEmpty {
            result.store = lines.prefix(3).first(where: { $0.rangeOfCharacter(from: .letters) != nil && !$0.contains(":") && !$0.contains("$") }) ?? ""
        }
        let footer = receiptOffers(lines)
        result.benefits = footer.benefits
        for (index, line) in lines.enumerated() {
            if footer.indices.contains(index) { continue }
            if result.date == nil, let match = firstMatch(#"\b(\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2})\b"#, line) {
                for format in ["MM/dd/yyyy", "MM/dd/yy", "MM-dd-yyyy", "MM-dd-yy", "yyyy-MM-dd"] {
                    let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = format; f.isLenient = false
                    if let date = f.date(from: match), date <= Date() { result.date = date; break }
                }
            }
            // A trailing decimal amount is required. Ambiguous column layouts stay in the OCR text for review.
            guard let amountText = firstMatch(#"-?\$?\s*\d+(?:,\d{3})*\.\d{2}\s*-?\s*(?:[A-Z])?$"#, line) else { continue }
            let rawNumber = amountText.replacingOccurrences(of: "$", with: "").replacingOccurrences(of: ",", with: "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let negative = rawNumber.contains("-")
            let cleaned = rawNumber.filter { $0.isNumber || $0 == "." }
            guard let value = Double(cleaned), value.isFinite else { continue }
            let lower = line.lowercased()
            let label = String(line.dropLast(amountText.count)).trimmingCharacters(in: .whitespaces)
            let summaryLabel = label.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: ": "))
            if ["subtotal", "sub total", "order subtotal"].contains(summaryLabel) { if !negative { subtotals.append(value) }; continue }
            if ["tax", "sales tax", "estimated tax"].contains(summaryLabel) { if !negative { taxes.append(value) }; continue }
            if ["total", "grand total", "order total", "total paid", "amount due"].contains(summaryLabel) { if !negative { totals.append(value) }; continue }
            if ["coupon", "discount", "saving", "extrabuck", "reward", "cash earned"].contains(where: lower.contains) {
                let kind = lower.contains("earned") || lower.contains("extrabuck") || lower.contains("reward") ? "Reward (review)" : "Discount"
                result.benefits.append(.init(description: label, value: value, kind: kind, rawLine: line)); continue
            }
            if ["visa", "mastercard", "debit", "credit", "cash", "change", "balance", "payment", "tender", "total", "tax", "member", "points", "account", "auth"].contains(where: lower.contains) { continue }
            if summaryLabel.range(of: #"^(?:quantity|qty|unit price|regular price|reg(?:ular)?\.? price|original price|sale price|price each|shipping|delivery|handling|service fee|amount charged|charged|card ending)\b"#, options: .regularExpression) != nil { continue }
            guard !negative, !label.isEmpty, label.rangeOfCharacter(from: .letters) != nil else { continue }
            var name = label
            var quantity = 1.0
            if let quantityText = firstMatch(#"^\d+(?:\.\d+)?\s*[xX]\s+"#, label),
               let q = Double(quantityText.filter { $0.isNumber || $0 == "." }), q > 0 {
                quantity = q; name = String(label.dropFirst(quantityText.count))
            }
            result.items.append(.init(name: name, price: value, quantity: quantity, rawLine: line))
        }
        // Several different totals are ambiguous. Never let the last page amount silently win.
        result.total = uniqueAmount(totals); result.subtotal = uniqueAmount(subtotals); result.tax = uniqueAmount(taxes)
        return result
    }
    private static func parseTargetPurchase(_ text: String) -> ParsedReceipt {
        var result = ParsedReceipt(); result.store = "Target"
        let raw = text.components(separatedBy: .newlines).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        guard let start = raw.firstIndex(where: { $0.caseInsensitiveCompare("Purchase details") == .orderedSame }) else { return result }
        let end = raw.indices.first { $0 > start && ["recommended", "recommended for you", "you may also like", "more to consider", "footer"].contains(raw[$0].lowercased()) } ?? raw.count
        let lines = Array(raw[start..<end])
        if let purchased = lines.first(where: { $0.localizedCaseInsensitiveContains("Purchased on") }),
           let dateText = firstMatch(#"[A-Za-z]+\s+\d{1,2},\s+\d{4}"#, purchased) {
            for format in ["MMMM d, yyyy", "MMM d, yyyy"] {
                let formatter = DateFormatter(); formatter.locale = Locale(identifier: "en_US_POSIX")
                formatter.dateFormat = format; formatter.isLenient = false
                if let date = formatter.date(from: dateText), date <= Date() { result.date = date; break }
            }
        }
        var totals: [Double] = [], subtotals: [Double] = [], taxes: [Double] = []
        let normalized = normalizedLines(lines.joined(separator: "\n"))
        for line in normalized {
            guard let amount = firstMatch(#"\$?\s*\d+(?:,\d{3})*\.\d{2}$"#, line), let value = money(amount) else { continue }
            let label = String(line.dropLast(amount.count)).lowercased().trimmingCharacters(in: CharacterSet(charactersIn: ": "))
            if ["total", "grand total", "order total", "total paid"].contains(label) { totals.append(value) }
            if ["subtotal", "sub total", "order subtotal"].contains(label) { subtotals.append(value) }
            if ["tax", "sales tax"].contains(label) { taxes.append(value) }
        }
        result.total = uniqueAmount(totals); result.subtotal = uniqueAmount(subtotals); result.tax = uniqueAmount(taxes)
        // A Target product card has a name, a standalone price and an explicit quantity.
        // Do not pair arbitrary page labels with nearby prices or guess what a multi-unit price means.
        for index in lines.indices where index >= 2 {
            guard let qtyText = firstMatch(#"(?i)^qty\s+(\d+(?:\.\d+)?)$"#, lines[index]),
                  let qty = Double(qtyText.filter { $0.isNumber || $0 == "." }),
                  lines[index - 1].range(of: #"^\$\d+(?:,\d{3})*\.\d{2}$"#, options: .regularExpression) != nil,
                  let price = money(lines[index - 1]) else { continue }
            let name = lines[index - 2]
            guard name.rangeOfCharacter(from: .letters) != nil,
                  !["subtotal", "tax", "total", "payment summary", "store location"].contains(name.lowercased()) else { continue }
            guard qty == 1 else {
                result.warnings.append("The price for \(name) has quantity \(qty.formatted()). Check its line total before adding it.")
                continue
            }
            result.items.append(.init(name: name, price: price, quantity: qty, rawLine: lines[(index - 2)...index].joined(separator: "\n")))
        }
        if let subtotal = result.subtotal, let tax = result.tax, let total = result.total,
           abs(subtotal + tax - total) > 0.011 {
            result.warnings.append("Subtotal plus tax differs from the total. Check shipping, discounts or other charges.")
        }
        if result.items.isEmpty { result.warnings.append("Item prices could not be matched confidently. The original receipt text is preserved.") }
        return result
    }
    private static func money(_ text: String) -> Double? {
        guard let value = Double(text.replacingOccurrences(of: "$", with: "").replacingOccurrences(of: ",", with: "").trimmingCharacters(in: .whitespaces)), value.isFinite else { return nil }
        return value
    }
    private static func uniqueAmount(_ values: [Double]) -> Double? {
        let cents = Set(values.map { ($0 * 100).rounded() })
        guard cents.count == 1, let value = values.first else { return nil }
        return value
    }
    private static func normalizedLines(_ text: String) -> [String] {
        let raw = text.components(separatedBy: .newlines).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        var lines: [String] = []; var index = 0
        while index < raw.count {
            let label = raw[index].lowercased().trimmingCharacters(in: CharacterSet(charactersIn: ": "))
            // Only join known summary labels. A standalone item price cannot safely be assigned by proximity.
            if ["total", "grand total", "order total", "total paid", "amount due", "subtotal", "sub total", "order subtotal", "tax", "sales tax", "estimated tax"].contains(label),
               index + 1 < raw.count, raw[index + 1].range(of: #"^\$?\s*\d+(?:,\d{3})*\.\d{2}$"#, options: .regularExpression) != nil {
                lines.append(label + " " + raw[index + 1]); index += 2
            } else { lines.append(raw[index]); index += 1 }
        }
        return lines
    }
    private static func receiptOffers(_ lines: [String]) -> (benefits: [ParsedReceiptBenefit], indices: Set<Int>) {
        let totalIndex = lines.firstIndex { $0.range(of: #"^(?:grand\s+)?total\s+\$?\d"#, options: [.regularExpression, .caseInsensitive]) != nil }
        let starts = lines.indices.filter { index in
            if let totalIndex, index <= totalIndex { return false }
            return lines[index].range(of: #"^(?:\$\s*\d+(?:\.\d{2})?\s+(?:off|extra\s*bucks)|\d+(?:\.\d+)?\s*%\s*off)\b"#, options: [.regularExpression, .caseInsensitive]) != nil
        }
        var benefits: [ParsedReceiptBenefit] = []; var indices = Set<Int>()
        for (offset, start) in starts.enumerated() {
            let end = min(offset + 1 < starts.count ? starts[offset + 1] : lines.count, start + 12)
            let block = lines[start..<end].joined(separator: "\n")
            let amount = firstMatch(#"\d+(?:\.\d{2})?"#, lines[start]).flatMap(Double.init) ?? 0
            let percent = lines[start].contains("%")
            var expiry: Date?
            if let expiryLine = lines[start..<end].first(where: { $0.localizedCaseInsensitiveContains("exp") }),
               let dateText = firstMatch(#"\b(?:\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2})\b"#, expiryLine) {
                for format in ["MM/dd/yyyy", "MM/dd/yy", "MM-dd-yyyy", "MM-dd-yy", "yyyy-MM-dd"] {
                    let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = format; f.isLenient = false
                    if let date = f.date(from: dateText) { expiry = date; break }
                }
            }
            benefits.append(.init(description: lines[start], value: percent ? 0 : amount, kind: "Offer (review)", rawLine: block,
                                  percentValue: percent ? amount : 0, expiration: expiry))
            indices.formUnion(start..<end)
        }
        return (benefits, indices)
    }
    private static func firstMatch(_ pattern: String, _ text: String) -> String? {
        guard let range = text.range(of: pattern, options: .regularExpression) else { return nil }
        return String(text[range])
    }
    static func category(_ item: String) -> String? {
        let groups: [(String, [String])] = [
            ("Coffee", ["coffee", "espresso", "latte", "cappuccino"]),
            ("Household goods", ["detergent", "laundry", "paper towel", "dish soap"]),
            ("Toiletries", ["shampoo", "toothpaste", "deodorant", "toothbrush"]),
            ("Groceries", ["milk", "eggs", "bread", "rice", "banana"]),
            ("Pharmacy", ["prescription", "vitamin", "ibuprofen"]),
            ("Electronics", ["laptop", "charger", "headphones"]),
            ("Fuel", ["gasoline", "diesel", "fuel"]),
            ("Fast food", ["burger", "fries", "taco"])
        ]
        return groups.first { _, words in words.contains { word in
            item.range(of: "\\b" + NSRegularExpression.escapedPattern(for: word) + "\\b", options: [.regularExpression, .caseInsensitive]) != nil
        } }?.0
    }
}
