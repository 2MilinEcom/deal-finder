import XCTest
import SwiftData
import UIKit
import UniformTypeIdentifiers
import WebKit
import CoreLocation
@testable import DEAL_SERVICE

@MainActor
final class DealFinderTests: XCTestCase {
    func testBestDealsResolveOnlyEligibleOffersAndRespectAIOptOut() async throws {
        let suite = "best-deals-qa-" + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite)); defer { defaults.removePersistentDomain(forName: suite) }
        var judged = 0
        let response = try JSONDecoder().decode(BestDealsResponse.self, from: Data("""
        {"mode":"ai","summary":"AI compared offers","eligible_ids":["Domino's"],"checked_at":"2026-10-10","picks":[{"id":"Domino's","verdict":"great","label":"Potential steal","reason":"Large discount","catch":"Compare your usual price","confidence":"medium","score":85,"personal_reasons":["Close by"]}]}
        """.utf8))
        let service = ProactiveDealService(defaults: defaults, judge: { offers, interests in
            judged += 1; XCTAssertEqual(offers.count, 2); return response
        }) { name, chain, address, force, interests in
            let candidate = NearbyDealCandidate(id: chain, title: "50% off pizza", summary: "Check terms", url: "https://www.dominos.com/deals", sourceType: "official", sourceLabel: "Public", score: 1, publishedDate: nil, expirationDate: nil, verificationStatus: "unverified", caveat: nil)
            return NearbyDealSearchResponse(businessName: name, chain: chain, address: address, searchedAt: "2026-10-10", resultCount: 1, results: [candidate], message: nil, broadSearchConfigured: true)
        }
        let store = StoreLocation(chain: "Domino's", name: "Domino's", coordinate: .init(latitude: 37, longitude: -122), address: "Test", distanceMeters: 100)
        await service.check(stores: [store], force: true)
        XCTAssertEqual(judged, 1); XCTAssertEqual(service.currentOffers.map(\.id), ["Domino's"])
        XCTAssertEqual(service.bestOffers.first?.1.label, "Potential steal")
        service.aiEnabled = false; await service.compareOffers()
        XCTAssertEqual(judged, 1); XCTAssertNil(service.shortlist)
    }
    func testProactiveCheckSearchesMultipleStoresAndDeliveryWithoutSaving() async throws {
        let suite = "proactive-qa-" + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite)); defer { defaults.removePersistentDomain(forName: suite) }
        var chains: [String] = []
        let service = ProactiveDealService(defaults: defaults, judge: { _, _ in throw BackendError.offline }) { name, chain, address, force, interests in
            chains.append(chain); XCTAssertTrue(interests.contains("hair"))
            if chain == "CVS" { throw BackendError.rejected("Blocked merchant") }
            let offer = NearbyDealCandidate(id: chain, title: "$5 offer", summary: "Check terms", url: "https://example.com/deal", sourceType: "official", sourceLabel: "Public", score: 1, publishedDate: nil, expirationDate: nil, verificationStatus: "unverified", caveat: "Check terms")
            return NearbyDealSearchResponse(businessName: name, chain: chain, address: address, searchedAt: "2026-10-10", resultCount: 1, results: [offer], message: nil, broadSearchConfigured: false)
        }
        let stores = ["CVS", "Domino's", "Cafe"].enumerated().map { index, chain in
            StoreLocation(chain: chain, name: chain, coordinate: .init(latitude: 37 + Double(index) / 100, longitude: -122), address: "Test address", distanceMeters: Double(index + 1) * 100)
        }
        await service.check(stores: stores, force: true)
        XCTAssertEqual(chains, ["CVS", "Domino's", "Cafe", "DoorDash"])
        XCTAssertEqual(service.currentOffers.count, 3)
        XCTAssertNil(service.currentOffers.first { $0.store == "DoorDash" }?.distance)
        XCTAssertTrue(service.status?.contains("Search setup") == true)
        XCTAssertNotNil(service.lastChecked); XCTAssertFalse(service.isChecking)
        await service.check(stores: stores)
        XCTAssertEqual(chains.count, 4, "Opening Home again must reuse the recent neighborhood check")
        service.enabled = false
        await service.check(stores: stores, force: true)
        XCTAssertEqual(chains.count, 4, "Opting out must stop discovery")
    }
    func testProactivePlaceSelectionPrioritizesNearbyChainsAndDeduplicates() {
        func store(_ chain: String, _ distance: Double) -> StoreLocation {
            StoreLocation(chain: chain, name: chain, coordinate: .init(latitude: 37, longitude: -122), address: "Test", distanceMeters: distance)
        }
        let picked = ProactiveDealService.selectedPlaces([store("Cafe", 50), store("CVS", 500), store("CVS", 900), store("Domino's", 1000), store("Target", 1200), store("Walgreens", 1300)])
        XCTAssertEqual(picked.map(\.chain), ["CVS", "Domino's", "Walgreens", "Target"])
        XCTAssertEqual(picked.first?.distanceMeters, 500)
        XCTAssertEqual(StoreLocatorService.chain("Domino’s Pizza"), "Domino's")
    }
    func testFreshOffersRejectExpiredAndOldCache() throws {
        let expiredSaved = try JSONDecoder().decode(RemoteDeal.self, from: Data("{\"id\":1,\"store\":\"CVS\",\"title\":\"Old sale\",\"used\":0,\"status\":\"expired\"}".utf8))
        XCTAssertFalse(expiredSaved.isActive)
        let expired = NearbyDealCandidate(id: "a", title: "Offer", summary: "Terms", url: "https://www.dominos.com/deals", sourceType: "official", sourceLabel: "Public", score: 1, publishedDate: nil, expirationDate: "2000-01-01", verificationStatus: "unverified", caveat: nil)
        XCTAssertFalse(FreshNearbyOffer(candidate: expired, store: "Domino's", address: "", distance: 100, checkedAt: .now).isFresh)
        let current = NearbyDealCandidate(id: "b", title: "Offer", summary: "Terms", url: "https://www.doordash.com/promos", sourceType: "delivery", sourceLabel: "Public", score: 1, publishedDate: nil, expirationDate: nil, verificationStatus: "unverified", caveat: nil)
        XCTAssertFalse(FreshNearbyOffer(candidate: current, store: "DoorDash", address: "", distance: nil, checkedAt: .now.addingTimeInterval(-90000)).isFresh)
        let fresh = FreshNearbyOffer(candidate: current, store: "DoorDash", address: "", distance: nil, checkedAt: .now)
        XCTAssertTrue(fresh.isFresh)
        XCTAssertNil(try JSONDecoder().decode(FreshNearbyOffer.self, from: JSONEncoder().encode(fresh)).distance)
    }
    func testTargetLoadMoreClicksOnlyReportedHistoryControl() async throws {
        let configuration = WKWebViewConfiguration(); configuration.websiteDataStore = .nonPersistent()
        let webView = WKWebView(frame: CGRect(x: 0, y: 0, width: 320, height: 640), configuration: configuration)
        let loaded = expectation(description: "Target pagination fixture loaded")
        let delegate = ReceiptPageTestDelegate { loaded.fulfill() }; webView.navigationDelegate = delegate
        webView.loadHTMLString("""
        <html><body><button onclick="document.body.dataset.bad='yes'">Add a card</button>
        <a href="https://www.target.com/orders/stores/1111-2222-3333-4444">View purchase</a>
        <button onclick="document.body.dataset.loaded='yes';this.remove()">Load more purchases</button>
        <input type="password" style="display:none" value="PRIVATE_PASSWORD"></body></html>
        """, baseURL: URL(string: "https://www.target.com/orders"))
        await fulfillment(of: [loaded], timeout: 15)
        let before = try await webView.evaluateJavaScript(StoreReceiptPage.script) as? [String: Any]
        XCTAssertEqual(before?["hasTargetLoadMore"] as? Bool, true)
        let clicked = try await webView.evaluateJavaScript(MerchantBrowserModel.targetLoadMoreScript)
        XCTAssertEqual(clicked as? Bool, true)
        let loadedValue = try await webView.evaluateJavaScript("document.body.dataset.loaded || ''")
        XCTAssertEqual(loadedValue as? String, "yes")
        let bad = try await webView.evaluateJavaScript("document.body.dataset.bad || ''")
        XCTAssertEqual(bad as? String, "")
        let after = try await webView.evaluateJavaScript(StoreReceiptPage.script) as? [String: Any]
        XCTAssertEqual(after?["hasTargetLoadMore"] as? Bool, false)
    }
    func testTargetHistoryCollectorUsesReportedLinksLoadsMoreAndSkipsManualDuplicates() async throws {
        let merchant = try XCTUnwrap(ReceiptMerchant.all.first { $0.name == "Target" })
        let first = URL(string: "https://www.target.com/orders/stores/1111-2222-3333-4444")!
        let second = URL(string: "https://www.target.com/orders/stores/5555-6666-7777-8888")!
        func history(_ urls: [URL], more: Bool) throws -> StoreReceiptPage {
            try XCTUnwrap(StoreReceiptPage(values: ["url": "https://www.target.com/orders", "text": "Store purchases", "hasTargetLoadMore": more,
                "links": urls.map { ["url": $0.absoluteString, "label": "View purchase"] } + [["url": "https://www.target.com/account/payments/new", "label": "View purchase"]]], merchant: merchant))
        }
        func text(_ url: URL) -> String {
            "Ask Target\nPurchase details\n#" + url.lastPathComponent + "\nPurchased on September 25, 2026 4:00 PM\nSubtotal\n$45.48\nTax\n$2.58\nTotal\n$48.06\nOlive oil\n$21.49\nQty 1\nPre-workout powder\n$23.99\nQty 1"
        }
        let schema = Schema([Receipt.self, ReceiptItem.self, ReceiptBenefit.self])
        let container = try ModelContainer(for: schema, configurations: ModelConfiguration(isStoredInMemoryOnly: true))
        let context = container.mainContext
        let manual = Receipt(store: "Target", total: 48.06, ocrText: text(second)); context.insert(manual); try context.save()
        var expanded = 0; var read: [URL] = []
        let collector = TargetStoreHistoryImporter()
        let result = try await collector.run(history: history([first], more: true), expandHistory: { count in
            XCTAssertEqual(count, 1); expanded += 1; return try history([first, second], more: false)
        }, readPurchase: { url in
            read.append(url)
            return try XCTUnwrap(StoreReceiptPage(values: ["url": url.absoluteString, "text": text(url)], merchant: merchant))
        }, save: { try StoreReceiptBatchSaver.save(page: $0, merchant: merchant, context: context) }, progress: { _ in })
        XCTAssertEqual(expanded, 1); XCTAssertEqual(read, [first, second])
        XCTAssertEqual(result.saved, 1); XCTAssertEqual(result.duplicates, 1); XCTAssertEqual(result.unreadable, 0)
        XCTAssertFalse(result.limited)
        XCTAssertEqual(try context.fetch(FetchDescriptor<Receipt>()).count, 2)
        let changed = try XCTUnwrap(StoreReceiptPage(values: ["url": first.absoluteString, "text": "Changed header\n" + text(first)], merchant: merchant))
        XCTAssertFalse(try StoreReceiptBatchSaver.save(page: changed, merchant: merchant, context: context))
    }
    func testTargetHistoryRejectsWrongRoutesAndDetectsStalledPagination() async throws {
        let merchant = try XCTUnwrap(ReceiptMerchant.all.first { $0.name == "Target" })
        let url = URL(string: "https://www.target.com/orders/stores/1111-2222-3333-4444")!
        for bad in ["https://target.com.evil.org/orders/stores/1111-2222-3333-4444", "http://www.target.com/orders/stores/1111-2222-3333-4444", "https://www.target.com/account/payments/new"] {
            XCTAssertFalse(TargetStoreHistoryImporter.validPurchaseURL(URL(string: bad)!))
        }
        let page = try XCTUnwrap(StoreReceiptPage(values: ["url": "https://www.target.com/orders", "text": "Store purchases", "hasTargetLoadMore": true,
            "links": [["url": url.absoluteString, "label": "View purchase"]]], merchant: merchant))
        do {
            _ = try await TargetStoreHistoryImporter().run(history: page, expandHistory: { _ in page }, readPurchase: { _ in XCTFail("Should not navigate stalled history"); return page }, save: { _ in XCTFail("Should not save history"); return true }, progress: { _ in })
            XCTFail("Expected stalled pagination error")
        } catch { XCTAssertTrue(error.localizedDescription.contains("did not reveal")) }
    }
    func testStoreLayoutReportExcludesCredentialsAndQueryValues() async throws {
        let configuration = WKWebViewConfiguration(); configuration.websiteDataStore = .nonPersistent()
        let webView = WKWebView(frame: CGRect(x: 0, y: 0, width: 320, height: 640), configuration: configuration)
        let loaded = expectation(description: "History controls fixture loaded")
        let delegate = ReceiptPageTestDelegate { loaded.fulfill() }; webView.navigationDelegate = delegate
        webView.loadHTMLString("""
        <html><body><a href="https://www.target.com/account/orders/6268-2767-0071-9011?token=PRIVATE_QUERY" data-test="order-card">View your receipt</a>
        <button data-test="purchase-history">Purchase history</button>
        <button style="display:none">PRIVATE_HIDDEN_RECEIPT</button>
        <input type="password" value="PRIVATE_PASSWORD"><input value="PRIVATE_ACCOUNT_VALUE">
        <a href="https://www.target.com/account/orders">Orders person@example.com</a>
        </body></html>
        """, baseURL: nil)
        await fulfillment(of: [loaded], timeout: 15)
        let result = try await webView.evaluateJavaScript(MerchantBrowserModel.layoutReportScript)
        let values = try XCTUnwrap(result as? [String: Any])
        let json = String(decoding: try JSONSerialization.data(withJSONObject: values), as: UTF8.self)
        XCTAssertFalse(json.contains("PRIVATE_"))
        XCTAssertFalse(json.contains("6268"))
        XCTAssertFalse(json.contains("person@example.com"))
        XCTAssertTrue(json.contains("order-card"))
        XCTAssertTrue(json.contains("View your receipt"))
        XCTAssertTrue(json.contains("purchase-history"))
    }
    func testTargetPurchaseLayoutFromReportedScreenshots() throws {
        let text = """
        skip to main contentskip to footer
        Ship to 94000
        Store Mode
        Ask Target
        Orders/
        Purchase details
        #TEST-PURCHASE
        2 items
        Purchased on September 25, 2026 4:00 PM
        Start a return
        View your receipt
        Get a gift receipt
        $48.06
        Subtotal
        $45.48
        Fulfillment
        Free
        Tax
        $2.58
        Total
        $48.06
        Payment Summary
        $48.06
        Purchased
        Sep 25, 3:59 PM
        Store Location
        Terra Delyssa 100% Organic Extra Virgin Olive Oil
        $21.49
        Qty 1
        C4 Sport Pre-Workout Powder
        $23.99
        Qty 1
        Recommended for you
        Unrelated product
        $12.99
        Qty 1
        """
        let receipt = ReceiptParser.parse(text, storeHint: "Target")
        XCTAssertEqual(receipt.store, "Target")
        XCTAssertEqual(receipt.total, 48.06)
        XCTAssertEqual(receipt.subtotal, 45.48)
        XCTAssertEqual(receipt.tax, 2.58)
        XCTAssertEqual(receipt.items.map(\.price), [21.49, 23.99])
        XCTAssertEqual(receipt.items.map(\.quantity), [1, 1])
        let date = try XCTUnwrap(receipt.date)
        XCTAssertEqual(Calendar.current.component(.month, from: date), 9)
        XCTAssertEqual(Calendar.current.component(.day, from: date), 25)
        XCTAssertEqual(Calendar.current.component(.year, from: date), 2026)
        XCTAssertTrue(receipt.warnings.isEmpty)
        let multiple = ReceiptParser.parse(text.replacingOccurrences(of: "Qty 1", with: "Qty 2"), storeHint: "Target")
        XCTAssertTrue(multiple.items.isEmpty)
        XCTAssertFalse(multiple.warnings.isEmpty)
    }
    func testReceiptMoneyLabelsDoNotGuessChargesOrOverwriteConflictingTotals() {
        let receipt = ReceiptParser.parse("Target\n10/01/2026\nMILK 4.00\nSubtotal\n$4.00\nTax\n$0.40\nTotal\n$4.40\nAmount charged 4.40\nRegular price 6.00\nQty 1 4.00")
        XCTAssertEqual(receipt.total, 4.4)
        XCTAssertEqual(receipt.subtotal, 4)
        XCTAssertEqual(receipt.tax, 0.4)
        XCTAssertEqual(receipt.items.count, 1)
        XCTAssertNil(ReceiptParser.parse("Target\nTotal 4.40\nTotal 20.00").total)
        XCTAssertEqual(ReceiptParser.parse("Target\nTotal 4.40\nTotal 4.40").total, 4.4)
        XCTAssertNil(ReceiptParser.parse("Target\nAmount charged 4.40").total)
    }
    func testBatchImportRejectsHistorySummariesAndDeduplicatesReceipts() throws {
        let merchant = try XCTUnwrap(ReceiptMerchant.all.first { $0.name == "Target" })
        let url = "https://www.target.com/account/orders/receipt/123"
        let text = "Target Receipt\n10/01/2026\nMILK 4.00\nTOTAL 4.00\n$2.00 off next purchase\nExpires 10/31/2026"
        let page = try XCTUnwrap(StoreReceiptPage(values: ["url": url, "text": text, "signingIn": false], merchant: merchant))
        XCTAssertNotNil(page.receipt)
        let separateTotal = StoreReceiptPage(values: ["url": url, "text": text.replacingOccurrences(of: "TOTAL 4.00", with: "TOTAL\n$4.00"), "signingIn": false], merchant: merchant)
        XCTAssertEqual(separateTotal?.receipt?.total, 4)
        let schema = Schema([Receipt.self, ReceiptItem.self, ReceiptBenefit.self])
        let container = try ModelContainer(for: schema, configurations: ModelConfiguration(isStoredInMemoryOnly: true))
        let context = container.mainContext
        XCTAssertTrue(try StoreReceiptBatchSaver.save(page: page, merchant: merchant, context: context))
        XCTAssertFalse(try StoreReceiptBatchSaver.save(page: page, merchant: merchant, context: context))
        let saved = try context.fetch(FetchDescriptor<Receipt>())
        XCTAssertEqual(saved.count, 1)
        XCTAssertEqual(saved[0].benefits.first?.direction, "Detected")
        XCTAssertNotNil(saved[0].importFingerprint)
        let summary = StoreReceiptPage(values: ["url": url, "text": text + "\nTOTAL 10.00", "signingIn": false], merchant: merchant)
        XCTAssertNil(summary?.receipt)
        let login = StoreReceiptPage(values: ["url": url, "text": text, "signingIn": true], merchant: merchant)
        XCTAssertNil(login?.receipt)
        let help = StoreReceiptPage(values: ["url": "https://www.target.com/help/receipts", "text": text, "signingIn": false], merchant: merchant)
        XCTAssertNil(help?.receipt)
        XCTAssertNil(StoreReceiptPage(values: ["url": "https://target.com.example.org", "text": text], merchant: merchant))
        for dangerous in ["Reorder", "Start a return", "Buy now", "Prescription", "Gift receipt"] {
            XCTAssertNil(StoreReceiptPage.kind(of: .init(url: URL(string: url)!, label: dangerous)))
        }
        XCTAssertEqual(StoreReceiptPage.kind(of: .init(url: URL(string: url)!, label: "View receipt")), "receipt")
    }
    func testBatchDiscoveryReadsVisibleLinksWithoutSecrets() async throws {
        let configuration = WKWebViewConfiguration(); configuration.websiteDataStore = .nonPersistent()
        let webView = WKWebView(frame: CGRect(x: 0, y: 0, width: 320, height: 640), configuration: configuration)
        let loaded = expectation(description: "History fixture loaded")
        let delegate = ReceiptPageTestDelegate { loaded.fulfill() }; webView.navigationDelegate = delegate
        webView.loadHTMLString("""
        <html><body>Target purchase history
        <a href="https://www.target.com/account/orders/123">View receipt</a>
        <a href="https://www.target.com/account/orders/456" style="display:none">HIDDEN</a>
        <input type="password" value="PRIVATE_PASSWORD" style="display:none">
        <input value="PRIVATE_ACCOUNT_VALUE"></body></html>
        """, baseURL: nil)
        await fulfillment(of: [loaded], timeout: 15)
        let result = try await webView.evaluateJavaScript(StoreReceiptPage.script)
        let values = try XCTUnwrap(result as? [String: Any])
        XCTAssertFalse((values["text"] as? String ?? "").contains("PRIVATE_"))
        XCTAssertEqual((values["links"] as? [[String: String]])?.count, 1)
        XCTAssertEqual(values["signingIn"] as? Bool, false)
    }
    func testStoreBrowserReadsVisibleTextWithoutFormValues() async throws {
        let configuration = WKWebViewConfiguration(); configuration.websiteDataStore = .nonPersistent()
        let webView = WKWebView(frame: CGRect(x: 0, y: 0, width: 320, height: 640), configuration: configuration)
        let loaded = expectation(description: "Receipt fixture loaded")
        let delegate = ReceiptPageTestDelegate { loaded.fulfill() }
        webView.navigationDelegate = delegate
        webView.loadHTMLString("""
        <html><body><h1>CVS Receipt</h1><p>TOOTHPASTE 6.00</p><p>TOTAL 6.00</p>
        <input type="text" value="PRIVATE_ACCOUNT_VALUE">
        <input type="password" value="PRIVATE_PASSWORD" style="display:none">
        <div style="display:none">HIDDEN_ACCOUNT_TEXT</div></body></html>
        """, baseURL: nil)
        await fulfillment(of: [loaded], timeout: 15)
        let result = try await webView.evaluateJavaScript(MerchantBrowserModel.pageImportScript)
        let values = try XCTUnwrap(result as? [String: Any])
        let text = try XCTUnwrap(values["text"] as? String)
        XCTAssertTrue(text.contains("CVS Receipt"))
        XCTAssertTrue(text.contains("TOTAL 6.00"))
        XCTAssertFalse(text.contains("PRIVATE_"))
        XCTAssertFalse(text.contains("HIDDEN_ACCOUNT_TEXT"))
        XCTAssertEqual(values["signingIn"] as? Bool, false)
        _ = try await webView.evaluateJavaScript("document.querySelector('input[type=password]').style.display='block'")
        let login = try await webView.evaluateJavaScript(MerchantBrowserModel.pageImportScript)
        XCTAssertEqual((login as? [String: Any])?["signingIn"] as? Bool, true)
    }
    func testStoreAccountsRestrictImportsToOfficialHTTPSDomains() {
        XCTAssertTrue(["Target", "CVS", "Safeway", "Costco"].allSatisfy { name in ReceiptMerchant.all.contains { $0.name == name } })
        for merchant in ReceiptMerchant.all {
            XCTAssertTrue(merchant.permits(merchant.url))
            XCTAssertTrue(merchant.permits(URL(string: "https://signin." + merchant.domain)))
            XCTAssertFalse(merchant.permits(URL(string: "http://www." + merchant.domain)))
            XCTAssertFalse(merchant.permits(URL(string: "https://" + merchant.domain + ".example.org")))
            XCTAssertFalse(merchant.permits(URL(string: "https://fake" + merchant.domain)))
        }
    }
    func testDigitalCVSReceiptAndOffersRequireAvailabilityReview() async throws {
        let text = """
        CVS PHARMACY
        10/01/2026
        TOOTHPASTE 6.00
        Coupon -1.00
        SUBTOTAL 5.00
        TAX 0.40
        TOTAL 5.40
        $4.00 off your next $20 purchase
        ExtraCare required. Expires 10/31/2026
        20% off shampoo
        Expires 11/01/2026
        """
        let imported = try await DigitalReceiptImporter.read(data: Data(text.utf8), type: .plainText)
        let parsed = ReceiptParser.parse(imported.text)
        XCTAssertEqual(parsed.store, "CVS")
        XCTAssertEqual(parsed.total, 5.4)
        XCTAssertEqual(parsed.items.count, 1)
        let offers = parsed.benefits.filter { $0.kind == "Offer (review)" }
        XCTAssertEqual(offers.count, 2)
        XCTAssertEqual(offers[0].value, 4)
        XCTAssertEqual(offers[1].percentValue, 20)
        XCTAssertNotNil(offers[0].expiration)
        XCTAssertFalse(offers[0].available)
        XCTAssertTrue(offers[0].rawLine.contains("$20"))
        let renderer = UIGraphicsPDFRenderer(bounds: CGRect(x: 0, y: 0, width: 640, height: 800))
        let pdf = renderer.pdfData { context in
            context.beginPage()
            (text as NSString).draw(in: CGRect(x: 20, y: 20, width: 600, height: 750), withAttributes: [.font: UIFont.systemFont(ofSize: 20)])
        }
        let pdfReceipt = try await DigitalReceiptImporter.read(data: pdf, type: .pdf)
        XCTAssertEqual(pdfReceipt.images.count, 1)
        XCTAssertEqual(ReceiptParser.parse(pdfReceipt.text).total, 5.4)
        do { _ = try await DigitalReceiptImporter.read(data: Data("not a pdf".utf8), type: .pdf); XCTFail("Expected invalid PDF") }
        catch { XCTAssertTrue(error.localizedDescription.contains("PDF")) }
    }
    func testReceiptParsingRequiresReviewAndPreservesUnknowns() {
        let text = """
        COSTCO WHOLESALE
        10/01/2026
        2 x DETERGENT 12.00
        ZXQ SKU 6.50
        Coupon -2.00
        Reward earned 3.00
        SUBTOTAL 16.50
        TAX 1.25
        TOTAL 17.75
        VISA 17.75
        """
        let parsed = ReceiptParser.parse(text)
        XCTAssertEqual(parsed.store, "Costco")
        XCTAssertNotNil(parsed.date)
        XCTAssertEqual(parsed.items.count, 2)
        XCTAssertEqual(parsed.items[0].quantity, 2)
        XCTAssertEqual(parsed.items[0].price, 12)
        XCTAssertEqual(parsed.subtotal, 16.5)
        XCTAssertEqual(parsed.tax, 1.25)
        XCTAssertEqual(parsed.total, 17.75)
        XCTAssertEqual(parsed.benefits.count, 2)
        XCTAssertNil(ReceiptParser.category("ZXQ SKU"))
        XCTAssertNil(ReceiptParser.parse("Unreadable receipt").total)
        XCTAssertNil(ReceiptParser.parse("TOTAL SAVINGS 9.00").total)
    }
    func testSwiftDataReceiptPersistenceAndCascade() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let schema = Schema([Deal.self, Receipt.self, ReceiptItem.self, ReceiptBenefit.self, HouseholdMember.self])
        let configuration = ModelConfiguration(url: folder.appendingPathComponent("test.store"))
        func write() throws {
            let container = try ModelContainer(for: schema, configurations: [configuration])
            let context = ModelContext(container)
            let receipt = Receipt(store: "Costco", total: 17.75)
            receipt.subtotal = 16.5; receipt.tax = 1.25
            context.insert(receipt)
            receipt.items = [ReceiptItem(itemName: "Detergent", price: 12, quantity: 2, receipt: receipt)]
            receipt.benefits = [ReceiptBenefit(value: 2, benefitDescription: "Coupon", receipt: receipt)]
            try context.save()
        }
        try write()
        let reopened = try ModelContainer(for: schema, configurations: [configuration])
        let context = ModelContext(reopened)
        let receipt = try XCTUnwrap(context.fetch(FetchDescriptor<Receipt>()).first)
        XCTAssertEqual(receipt.items.count, 1)
        XCTAssertEqual(receipt.tax, 1.25)
        receipt.items[0].itemName = "Laundry detergent"
        try context.save()
        XCTAssertEqual(try context.fetch(FetchDescriptor<ReceiptItem>()).first?.itemName, "Laundry detergent")
        context.delete(receipt); try context.save()
        XCTAssertTrue(try context.fetch(FetchDescriptor<ReceiptItem>()).isEmpty)
        XCTAssertTrue(try context.fetch(FetchDescriptor<ReceiptBenefit>()).isEmpty)
    }
    func testAPIContractAndOfflineOutbox() async throws {
        let old = BackendConfiguration.address
        defer { BackendConfiguration.address = old; BackendClient.session = .shared }
        BackendConfiguration.address = "http://127.0.0.1:18012"
        let deals = try await PythonSyncService.shared.fetchDeals(includeHistory: true)
        XCTAssertFalse(deals.isEmpty)
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let repo = DealRepository(storageDirectory: folder)
        await repo.refresh(force: true)
        let deal = try XCTUnwrap(repo.active.first)
        await repo.setState(deal, "saved")
        XCTAssertEqual(repo.deals.first(where: { $0.id == deal.id })?.status, "saved")
        let offlineConfiguration = URLSessionConfiguration.ephemeral
        offlineConfiguration.protocolClasses = [OfflineProtocol.self]
        BackendClient.session = URLSession(configuration: offlineConfiguration)
        await repo.setState(deal, "used")
        XCTAssertEqual(repo.pending[deal.id], "used")
        XCTAssertEqual(repo.deals.first(where: { $0.id == deal.id })?.used, 1)
        let offlineReopened = DealRepository(storageDirectory: folder)
        XCTAssertEqual(offlineReopened.pending[deal.id], "used")
        BackendClient.session = .shared
        await repo.refresh(force: true)
        XCTAssertTrue(repo.pending.isEmpty)
        let used = try await PythonSyncService.shared.fetchDeals(includeHistory: true)
        XCTAssertEqual(used.first(where: { $0.id == deal.id })?.used, 1)
        await repo.setState(deal, "active")
        // Refused local port produces a genuine offline URLSession failure.
        BackendConfiguration.address = "http://127.0.0.1:1"
        do { _ = try await PythonSyncService.shared.health(); XCTFail("Expected offline error") }
        catch { XCTAssertTrue(error.localizedDescription.contains("offline")) }
        BackendConfiguration.address = "http://127.0.0.1:18012"
        // Disk cache survives repository recreation and retains used history.
        let reopened = DealRepository(storageDirectory: folder)
        XCTAssertEqual(reopened.deals, repo.deals)
        let response = try await ForYouService().recommendations(receipts: [.init(store: "Costco", items: ["laundry detergent"])], nearby: [])
        XCTAssertFalse(response.recommendations.isEmpty)
        XCTAssertTrue(response.recommendations.contains { $0.why.contains("Matches items in your receipts") })
    }
    func testVisionOCRFromReceiptImage() async throws {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 640, height: 500))
        let image = renderer.image { context in
            UIColor.white.setFill(); context.fill(CGRect(x: 0, y: 0, width: 640, height: 500))
            let lines = ["COSTCO", "10/01/2026", "DETERGENT 12.00", "SUBTOTAL 12.00", "TAX 1.25", "TOTAL 13.25"]
            for (index, line) in lines.enumerated() {
                (line as NSString).draw(at: CGPoint(x: 30, y: 30 + index * 65), withAttributes: [.font: UIFont.systemFont(ofSize: 32), .foregroundColor: UIColor.black])
            }
        }
        let text = try await ReceiptOCRService().recognizeText(from: image)
        XCTAssertTrue(text.contains("COSTCO"))
        let parsed = ReceiptParser.parse(text)
        XCTAssertEqual(parsed.total, 13.25)
        XCTAssertFalse(parsed.items.isEmpty)
    }
    func testConfigurationRejectsCredentialsAndBadURLs() {
        XCTAssertThrowsError(try BackendConfiguration.validatedURL("ftp://example.com"))
        XCTAssertThrowsError(try BackendConfiguration.validatedURL("https://key:secret@example.com"))
        XCTAssertThrowsError(try BackendConfiguration.validatedURL("http://server/api?key=secret"))
        XCTAssertNoThrow(try BackendConfiguration.validatedURL("http://127.0.0.1:8001"))
    }
}

private final class ReceiptPageTestDelegate: NSObject, WKNavigationDelegate {
    let finished: () -> Void
    init(finished: @escaping () -> Void) { self.finished = finished }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { finished() }
}

private final class OfflineProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() { client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet)) }
    override func stopLoading() { }
}
