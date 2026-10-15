import SwiftUI
import WebKit
import Combine
import SwiftData
import UIKit

struct ReceiptMerchant: Identifiable {
    let name: String
    let domain: String
    let instructions: String
    var id: String { domain }
    var url: URL? { URL(string: "https://www." + domain) }
    func permits(_ url: URL?) -> Bool {
        guard let url, url.scheme?.lowercased() == "https", let host = url.host?.lowercased() else { return false }
        return host == domain || host.hasSuffix("." + domain)
    }
    static let all: [ReceiptMerchant] = [
        .init(name: "Target", domain: "target.com", instructions: "Sign in, then tap Import Target store receipts. Deal Finder opens store Purchase History, loads more purchases and reads them for you. Keep this screen open during import."),
        .init(name: "CVS", domain: "cvs.com", instructions: "Sign in and open ExtraCare Savings & Rewards. For a digital receipt sent by email, use its PDF, screenshot or copied text."),
        .init(name: "Safeway", domain: "safeway.com", instructions: "Sign in and look for purchases or order history. If your receipt is available only in the Safeway app, import its screenshot or file."),
        .init(name: "Costco", domain: "costco.com", instructions: "Sign in and open one receipt in Orders & Returns. Costco may ask you to verify your membership."),
        .init(name: "Walmart", domain: "walmart.com", instructions: "Sign in and open your purchase history. Select one purchase and its receipt before importing."),
        .init(name: "Walgreens", domain: "walgreens.com", instructions: "Sign in and open your orders or rewards. Use receipt emails or screenshots if a receipt is unavailable on the website."),
        .init(name: "Kroger", domain: "kroger.com", instructions: "Sign in and look for purchase history or digital receipts. Receipt availability depends on your account and purchases.")
    ]
}

struct StoreAccountsView: View {
    @Environment(\.dismiss) private var dismiss
    var onImport: ((String, String) -> Void)? = nil
    @State private var selected: ReceiptMerchant?
    @State private var draft: (text: String, store: String)?
    @State private var showReview = false
    @State private var confirmSignOut = false
    var body: some View {
        List {
            Section {
                Text("Sign in to a store and open a receipt, then choose Import this page.")
                Text("Target now has a store-purchase batch importer. Other stores support individual receipt import while their history connectors are being developed.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Section("Sign in to your stores") {
                ForEach(ReceiptMerchant.all) { merchant in
                    Button { draft = nil; selected = merchant } label: {
                        HStack {
                            Label(merchant.name, systemImage: "storefront")
                            Spacer()
                            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            Section("Your privacy") {
                Text("Store sessions stay in the on-device browser. Receipt text is read when you start an import; passwords and form-field values are not collected. Saved receipts stay on this iPhone. Detected offers need confirmation before they become available deals.")
                    .font(.footnote).foregroundStyle(.secondary)
                Text("For sites that require their own app or Safari, save a PDF or screenshot there and import it using Add receipt.")
                    .font(.footnote).foregroundStyle(.secondary)
                Button("Clear store sign-ins on this iPhone", role: .destructive) { confirmSignOut = true }
            }
        }.navigationTitle("Store accounts")
            .sheet(item: $selected, onDismiss: finishImport) { merchant in
                MerchantReceiptBrowser(merchant: merchant) { text in
                    draft = (text, merchant.name); selected = nil
                }
            }
            .navigationDestination(isPresented: $showReview) {
                ReceiptScanView(initialText: draft?.text, initialStore: draft?.store)
            }
            .confirmationDialog("Sign out of the stores in Deal Finder’s browser? Your saved receipts stay on this iPhone.", isPresented: $confirmSignOut, titleVisibility: .visible) {
                Button("Clear store sign-ins", role: .destructive) {
                    WKWebsiteDataStore.default().removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast) { }
                }
            }
    }
    private func finishImport() {
        guard let draft else { return }
        if let onImport { onImport(draft.text, draft.store); self.draft = nil; dismiss() }
        else { showReview = true }
    }
}

@MainActor
final class MerchantBrowserModel: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate {
    let merchant: ReceiptMerchant
    let webView: WKWebView
    @Published var loading = false
    @Published var importing = false
    @Published var message: String?
    @Published var currentHost = ""
    @Published var batchProgress: String?
    func importHistory(context: ModelContext) async {
        guard merchant.name == "Target" else { message = "Automatic history import is not available for this store yet."; return }
        importing = true; defer { importing = false; batchProgress = nil }
        let collector = TargetStoreHistoryImporter()
        do {
            batchProgress = "Opening Target purchase history…"
            if webView.url?.path != "/orders" {
                guard let historyURL = URL(string: "https://www.target.com/orders") else { return }
                try await visit(historyURL)
            }
            let history = try await waitForTargetPage(history: true)
            let result = try await collector.run(history: history, expandHistory: { oldCount in
                try await self.expandTargetHistory(oldCount: oldCount)
            }, readPurchase: { url in
                try await self.visit(url)
                return try await self.waitForTargetPage(history: false)
            }, save: { page in
                try StoreReceiptBatchSaver.save(page: page, merchant: self.merchant, context: context)
            }, progress: { self.batchProgress = $0 })
            message = "Saved \(result.saved) Target receipts · \(result.duplicates) already saved · \(result.unreadable) unreadable."
                + (result.limited ? " The import limit was reached; older purchases may remain." : "")
        } catch is CancellationError {
            message = "Import stopped. \(collector.result.saved) receipts saved so far."
        } catch {
            message = "Saved \(collector.result.saved) receipts before import stopped. " + error.localizedDescription
        }
    }
    private func waitForTargetPage(history: Bool) async throws -> StoreReceiptPage {
        let deadline = Date().addingTimeInterval(15)
        var page = try await snapshot()
        while !page.signingIn && (history ? TargetStoreHistoryImporter.purchaseLinks(page).isEmpty : page.receipt == nil) && Date() < deadline {
            try Task.checkCancellation(); try await Task.sleep(for: .milliseconds(300))
            page = try await snapshot()
        }
        return page
    }
    static let targetLoadMoreScript = """
        (() => {
          if(location.pathname.replace(/\\/$/, '') !== '/orders') return false;
          const button = Array.from(document.querySelectorAll('button,[role=button]')).find(e =>
            e.getClientRects().length > 0 && !e.disabled && e.getAttribute('aria-disabled') !== 'true' &&
            (e.innerText || e.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ') === 'Load more purchases');
          if(!button) return false; button.click(); return true;
        })()
        """
    private func expandTargetHistory(oldCount: Int) async throws -> StoreReceiptPage {
        try Task.checkCancellation()
        guard merchant.name == "Target", merchant.permits(webView.url), webView.url?.path == "/orders",
              try await webView.evaluateJavaScript(Self.targetLoadMoreScript) as? Bool == true else {
            throw BackendError.rejected("Target’s Load more purchases control is not ready. Try again later.")
        }
        let deadline = Date().addingTimeInterval(20)
        try await Task.sleep(for: .milliseconds(500))
        var page = try await snapshot()
        while TargetStoreHistoryImporter.purchaseLinks(page).count <= oldCount && page.hasTargetLoadMore && !page.signingIn && Date() < deadline {
            try Task.checkCancellation(); try await Task.sleep(for: .milliseconds(300)); page = try await snapshot()
        }
        return page
    }
    private func snapshot() async throws -> StoreReceiptPage {
        guard merchant.permits(webView.url), let values = try await webView.evaluateJavaScript(StoreReceiptPage.script) as? [String: Any],
              let page = StoreReceiptPage(values: values, merchant: merchant) else {
            throw BackendError.rejected("This page cannot be read as a store receipt page.")
        }
        return page
    }
    private func visit(_ url: URL) async throws {
        try Task.checkCancellation()
        guard merchant.permits(url) else { throw BackendError.rejected("Receipt navigation left the store website.") }
        webView.load(URLRequest(url: url))
        // Let the navigation start, then wait for the site to finish. Every wait supports cancellation.
        try await Task.sleep(for: .milliseconds(600))
        let deadline = Date().addingTimeInterval(20)
        while webView.isLoading && Date() < deadline {
            try Task.checkCancellation(); try await Task.sleep(for: .milliseconds(250))
        }
        guard !webView.isLoading, merchant.permits(webView.url) else {
            throw BackendError.rejected("The store did not finish loading its receipt page. Try again later.")
        }
        // Account sites may render receipt content after the initial navigation finishes.
        try await Task.sleep(for: .seconds(2))
    }
    init(merchant: ReceiptMerchant) {
        self.merchant = merchant
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        webView = WKWebView(frame: .zero, configuration: configuration)
        super.init()
        webView.navigationDelegate = self; webView.uiDelegate = self
        if let url = merchant.url { webView.load(URLRequest(url: url)) }
    }
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) { loading = true; message = nil }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { loading = false; currentHost = webView.url?.host ?? "" }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { failed(error) }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { failed(error) }
    private func failed(_ error: Error) {
        loading = false
        if (error as NSError).code != NSURLErrorCancelled { message = "This store page could not load. Try Safari or import a receipt file." }
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard navigationAction.targetFrame?.isMainFrame != false else { decisionHandler(.allow); return }
        guard merchant.permits(navigationAction.request.url) else {
            decisionHandler(.cancel)
            message = "This step opens another website. Use Open in Safari to finish signing in, then import a PDF or screenshot."
            return
        }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if merchant.permits(navigationAction.request.url) { webView.load(navigationAction.request) }
        return nil
    }
    static let layoutReportScript = """
        (() => {
          const redact = s => String(s).replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}/gi, '[email]')
            .replace(/\\b[0-9a-f]{8}-[0-9a-f-]{27,}\\b/gi, '[id]').replace(/\\d{4,}/g, '[number]').slice(0, 180);
          const path = s => redact(s).replace(/\\/[A-Za-z0-9_-]{24,}(?=\\/|$)/g, '/[id]');
          const controls = Array.from(document.querySelectorAll('a[href],button,[role=button]'))
            .filter(e => e.getClientRects().length > 0).map(e => {
              const label = (e.innerText || e.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
              const test = e.getAttribute('data-test') || e.getAttribute('data-testid') || '';
              let route = ''; try { if(e.tagName === 'A') route = new URL(e.href).pathname; } catch (_) {}
              return {tag: e.tagName.toLowerCase(), label: redact(label), test: redact(test), path: path(route),
                relevant: /purchase|order|receipt|history|next|older|details/i.test(label + ' ' + test + ' ' + route)};
            }).filter(e => e.relevant).slice(0, 60).map(({relevant, ...e}) => e);
          return {version: 1, path: path(location.pathname), controls};
        })()
        """
    func copyLayoutReport() async throws {
        guard merchant.permits(webView.url) else { throw BackendError.rejected("Open the store’s purchase history first.") }
        let result = try await webView.evaluateJavaScript(Self.layoutReportScript)
        guard var report = result as? [String: Any] else { throw BackendError.rejected("This page could not provide an import report.") }
        report["store"] = merchant.name
        let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
        guard data.count <= 20000, let text = String(data: data, encoding: .utf8) else { throw BackendError.rejected("The import report is too large.") }
        UIPasteboard.general.string = text
        message = "Import report copied. Paste it in the Deal Finder chat so the history navigation can be repaired. It excludes form values, passwords, cookies and URL query values."
    }
    static let pageImportScript = """
        (() => ({
          text: document.body ? document.body.innerText : '',
          url: location.href,
          signingIn: Array.from(document.querySelectorAll('input[type=password]')).some(e => e.getClientRects().length > 0)
        }))()
        """
    func importPage() async throws -> String {
        guard merchant.permits(webView.url) else { throw BackendError.rejected("Open a receipt on the store’s official website first.") }
        importing = true; defer { importing = false }
        // Only rendered text is imported. Never read input values, cookies or credentials.
        let result = try await webView.evaluateJavaScript(Self.pageImportScript)
        guard let values = result as? [String: Any], values["signingIn"] as? Bool != true,
              let rawURL = values["url"] as? String, merchant.permits(URL(string: rawURL)),
              let text = values["text"] as? String, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw BackendError.rejected("Finish signing in and open one receipt or offer before importing.")
        }
        guard text.count <= 100000 else { throw BackendError.rejected("Open one receipt rather than the full purchase history, then import it.") }
        return text
    }
}
private struct MerchantWebView: UIViewRepresentable {
    let model: MerchantBrowserModel
    func makeUIView(context: Context) -> WKWebView { model.webView }
    func updateUIView(_ view: WKWebView, context: Context) { }
}
private struct MerchantReceiptBrowser: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.modelContext) private var context
    @State private var batchTask: Task<Void, Never>?
    let merchant: ReceiptMerchant
    let onImport: (String) -> Void
    @StateObject private var model: MerchantBrowserModel
    @State private var showGuide = true
    init(merchant: ReceiptMerchant, onImport: @escaping (String) -> Void) {
        self.merchant = merchant; self.onImport = onImport
        _model = StateObject(wrappedValue: MerchantBrowserModel(merchant: merchant))
    }
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if showGuide {
                    HStack(alignment: .top) {
                        Text(merchant.instructions).font(.footnote)
                        Button { showGuide = false } label: { Image(systemName: "xmark.circle.fill") }.accessibilityLabel("Hide receipt instructions")
                    }.padding().background(.thinMaterial)
                }
                if model.loading { ProgressView().progressViewStyle(.linear) }
                if let message = model.message { Text(message).font(.caption).foregroundStyle(.secondary).padding(8) }
                MerchantWebView(model: model).allowsHitTesting(!model.importing)
                VStack(spacing: 8) {
                    if merchant.name == "Target" {
                        Text("Imports store purchases. Online orders are not supported yet.").font(.caption).foregroundStyle(.secondary)
                        if let progress = model.batchProgress { Text(progress).font(.caption) }
                        Button("Import Target store receipts") { batchTask = Task { await model.importHistory(context: context) } }
                            .buttonStyle(.borderedProminent).disabled(model.loading || model.importing)
                        if model.importing && model.batchProgress != nil {
                            Button("Stop import") { batchTask?.cancel(); model.webView.stopLoading() }
                        }
                    } else {
                        Text("Automatic history import unavailable for this store yet").font(.caption).foregroundStyle(.secondary)
                    }
                    Button {
                        Task {
                            do { onImport(try await model.importPage()) }
                            catch { model.message = error.localizedDescription }
                        }
                    } label: { Label(model.importing ? "Reading page…" : "Import this page", systemImage: "doc.badge.arrow.up") }
                        .buttonStyle(.bordered).disabled(model.loading || model.importing)
                    Button("Copy import report") {
                        Task { do { try await model.copyLayoutReport() } catch { model.message = error.localizedDescription } }
                    }.font(.caption).disabled(model.loading || model.importing)
                    Text("Read one receipt and check the imported total. Missing or conflicting totals need your confirmation.").font(.caption).foregroundStyle(.secondary)
                }.padding().frame(maxWidth: .infinity).background(.bar)
            }.onDisappear { batchTask?.cancel(); model.webView.stopLoading() }.navigationTitle(merchant.name).navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
                    ToolbarItem(placement: .primaryAction) {
                        if let url = model.webView.url ?? merchant.url { Link("Open in Safari", destination: url).disabled(model.importing) }
                    }
                }
        }
    }
}
