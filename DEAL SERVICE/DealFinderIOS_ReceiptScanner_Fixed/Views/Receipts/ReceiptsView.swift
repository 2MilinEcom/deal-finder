import SwiftUI
import SwiftData
import UIKit

struct ReceiptsView: View {
    @Query(sort: \Receipt.purchaseDate, order: .reverse) private var receipts: [Receipt]
    var body: some View {
        List {
            Section("Digital receipts") {
                NavigationLink { StoreAccountsView() } label: { Label("Store accounts", systemImage: "person.crop.circle") }
            }
            if receipts.isEmpty {
                ContentUnavailableView("Your spending story starts here", systemImage: "receipt",
                                       description: Text("Scan or import a receipt. Review the details, then save it on your iPhone."))
            }
            ForEach(receipts) { receipt in
                NavigationLink { ReceiptDetailView(receipt: receipt) } label: {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(receipt.store).font(.headline)
                        Text(receipt.purchaseDate, format: .dateTime.month().day().year()).foregroundStyle(.secondary)
                        HStack { Text(receipt.total, format: .currency(code: "USD")); Spacer(); Text("\(receipt.items.count) items · \(receipt.householdMemberName)") }
                            .font(.caption)
                    }.padding(.vertical, 4)
                }
            }
        }
        .navigationTitle("Receipts")
        .toolbar { NavigationLink { ReceiptScanView() } label: { Label("Add receipt", systemImage: "plus") } }
    }
}

struct ReceiptDetailView: View {
    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss
    let receipt: Receipt
    @State private var confirmDelete = false
    @State private var error: String?
    @State private var savingOffer: UUID?
    @State private var savedOffers = Set<UUID>()
    @State private var offerToSave: ReceiptBenefit?
    var body: some View {
        List {
            Section("Purchase") {
                LabeledContent("Store", value: receipt.store)
                LabeledContent("Date", value: receipt.purchaseDate.formatted(date: .abbreviated, time: .omitted))
                if let value = receipt.subtotal { LabeledContent("Subtotal", value: value.formatted(.currency(code: "USD"))) }
                if let value = receipt.tax { LabeledContent("Tax", value: value.formatted(.currency(code: "USD"))) }
                LabeledContent("Total", value: receipt.total.formatted(.currency(code: "USD")))
                LabeledContent("Household member", value: receipt.householdMemberName)
                if !receipt.notes.isEmpty { Text(receipt.notes) }
            }
            Section("Items") {
                if receipt.items.isEmpty { Text("No items saved. Edit this receipt to add them.").foregroundStyle(.secondary) }
                ForEach(receipt.items) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        LabeledContent(item.itemName, value: item.price.formatted(.currency(code: "USD")))
                        Text("Quantity: \(item.quantity.formatted()) · \(ReceiptParser.category(item.itemName) ?? "Unclassified")").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if !receipt.benefits.isEmpty {
                Section("Discounts and rewards") {
                    ForEach(receipt.benefits) { benefit in
                        VStack(alignment: .leading) {
                            LabeledContent(benefit.benefitDescription, value: benefit.percentValue > 0 ? "\(benefit.percentValue.formatted())%" : benefit.value.formatted(.currency(code: "USD")))
                            Text(benefit.benefitType).font(.caption).foregroundStyle(.secondary)
                            if benefit.benefitType == "Offer (review)" {
                                if let expiration = benefit.expirationDate { Text("Expires " + expiration.formatted(date: .abbreviated, time: .omitted)).font(.caption) }
                                else { Text("Expiration unknown").font(.caption).foregroundStyle(.secondary) }
                                if benefit.direction == "Available" {
                                    Button(savedOffers.contains(benefit.id) ? "Saved to Deals" : "Save offer to Deals") { offerToSave = benefit }
                                        .disabled(savingOffer != nil || savedOffers.contains(benefit.id))
                                } else { Text("Correct this receipt to confirm the offer is unused before saving it to Deals.").font(.caption).foregroundStyle(.secondary) }
                                DisclosureGroup("Printed terms") { Text(benefit.rawLine).font(.caption).textSelection(.enabled) }
                            }
                        }
                    }
                }
            }
            Section("Receipt images") {
                ForEach(Array(images.enumerated()), id: \.offset) { _, image in
                    Image(uiImage: image).resizable().scaledToFit().accessibilityLabel("Receipt page")
                }
            }
            if !receipt.ocrText.isEmpty {
                DisclosureGroup("Original recognized text") { Text(receipt.ocrText).font(.caption).textSelection(.enabled) }
            }
            Section {
                NavigationLink("Correct receipt") { ReceiptScanView(existing: receipt) }
                Button("Delete receipt", role: .destructive) { confirmDelete = true }
                if let error { Text(error).foregroundStyle(.red) }
            }
        }.navigationTitle(receipt.store).navigationBarTitleDisplayMode(.inline)
            .confirmationDialog("Save this unused offer to Deals? Verify the printed restrictions before using it.", isPresented: Binding(get: { offerToSave != nil }, set: { if !$0 { offerToSave = nil } }), titleVisibility: .visible) {
                if let benefit = offerToSave {
                    Button("Save offer") {
                        savingOffer = benefit.id
                        Task {
                            defer { savingOffer = nil }
                            do { try await ReceiptOfferService.save(receipt: receipt, benefit: benefit); savedOffers.insert(benefit.id) }
                            catch { self.error = error.localizedDescription }
                        }
                    }
                }
            }
            .confirmationDialog("Delete this receipt and its items?", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Delete receipt", role: .destructive) {
                    context.delete(receipt)
                    do { try context.save(); dismiss() }
                    catch { context.rollback(); self.error = "The receipt could not be deleted. Try again." }
                }
            }
    }
    private var images: [UIImage] {
        var pages = [receipt.imageData].compactMap { $0 }.compactMap { UIImage(data: $0) }
        if let data = receipt.additionalImages, let extras = try? JSONDecoder().decode([Data].self, from: data) {
            pages += extras.compactMap { UIImage(data: $0) }
        }
        return pages
    }
}
