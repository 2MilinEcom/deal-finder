import SwiftUI
import SwiftData
import VisionKit
import PhotosUI
import UIKit
import UniformTypeIdentifiers

struct ReceiptScanView: View {
    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss
    @Query(sort: \HouseholdMember.name) private var members: [HouseholdMember]
    @StateObject private var scanner = ReceiptScannerViewModel()
    var existing: Receipt? = nil
    var initialText: String? = nil
    var initialStore: String? = nil
    @State private var showingScanner = false
    @State private var photo: PhotosPickerItem?
    @State private var store = ""
    @State private var purchaseDate = Date()
    @State private var subtotal = 0.0
    @State private var tax = 0.0
    @State private var total = 0.0
    @State private var totalConfirmed = false
    @State private var hasSubtotal = false
    @State private var hasTax = false
    @State private var notes = ""
    @State private var member = "Me"
    @State private var items: [ParsedReceiptItem] = []
    @State private var benefits: [ParsedReceiptBenefit] = []
    @State private var message: String?
    @State private var dateDetected = false
    @State private var isImporting = false
    @State private var isSaving = false
    @State private var showingFile = false
    @State private var showingText = false
    @State private var pastedText = ""

    var body: some View {
        Form {
            if existing == nil {
                Section("Add a receipt") {
                    if VNDocumentCameraViewController.isSupported {
                        Button { showingScanner = true } label: { Label("Scan with camera", systemImage: "doc.viewfinder") }
                            .disabled(scanner.isReading || isImporting)
                    }
                    PhotosPicker(selection: $photo, matching: .images) { Label("Import a photo", systemImage: "photo") }
                        .disabled(scanner.isReading || isImporting)
                    Button { showingFile = true } label: { Label("Import receipt file", systemImage: "doc.badge.plus") }
                        .disabled(scanner.isReading || isImporting)
                    Button { showingText = true } label: { Label("Paste receipt text", systemImage: "doc.on.clipboard") }
                        .disabled(scanner.isReading || isImporting)
                    NavigationLink {
                        StoreAccountsView { text, merchant in
                            scanner.recognizedText = text; scanner.scannedImages = []; scanner.errorMessage = nil
                            applyParsedText(storeHint: merchant); store = merchant
                        }
                    } label: { Label("Store accounts", systemImage: "person.crop.circle") }
                    if scanner.isReading || isImporting { ProgressView("Reading on your iPhone…") }
                    if let image = scanner.scannedImages.first {
                        Image(uiImage: image).resizable().scaledToFit().frame(maxHeight: 220)
                            .clipShape(RoundedRectangle(cornerRadius: 12)).accessibilityLabel("Scanned receipt")
                        Text("\(scanner.scannedImages.count) page(s)").font(.caption).foregroundStyle(.secondary)
                    }
                    if let error = scanner.errorMessage { Text(error).foregroundStyle(.red) }
                }
            }
            Section("Review details") {
                if existing != nil && store == "Target" && !scanner.recognizedText.isEmpty {
                    Button("Re-read Target receipt text") {
                        let reviewedBenefits = benefits
                        applyParsedText(storeHint: "Target")
                        benefits = reviewedBenefits
                    }
                    Text("Re-read updates this draft. Save corrections after checking the amounts.").font(.caption).foregroundStyle(.secondary)
                }
                TextField("Store or business", text: $store)
                DatePicker("Purchase date", selection: $purchaseDate, in: ...Date(), displayedComponents: .date)
                if !dateDetected { Text("Check the date: it could not be detected confidently.").font(.caption).foregroundStyle(.secondary) }
                Toggle("Subtotal visible", isOn: $hasSubtotal)
                if hasSubtotal { TextField("Subtotal", value: $subtotal, format: .currency(code: "USD")).keyboardType(.decimalPad) }
                Toggle("Tax visible", isOn: $hasTax)
                if hasTax { TextField("Tax", value: $tax, format: .currency(code: "USD")).keyboardType(.decimalPad) }
                TextField("Total", value: Binding(get: { total }, set: { total = $0; totalConfirmed = true }), format: .currency(code: "USD")).keyboardType(.decimalPad)
                if !totalConfirmed {
                    Text("Total missing or conflicting. Enter the actual receipt total; the displayed zero is a placeholder.").font(.caption).foregroundStyle(.red)
                    Toggle("I checked the receipt and its total is $0.00", isOn: $totalConfirmed)
                }
                Picker("Household member", selection: $member) {
                    ForEach(Array(Set(members.map(\.name) + ["Me", member])).sorted(), id: \.self) { Text($0).tag($0) }
                }
                TextField("Notes", text: $notes, axis: .vertical)
                Text("OCR is a draft. Confirm the total and items before saving; unclear products stay unclassified.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Section("Items") {
                ForEach($items) { $item in
                    VStack(alignment: .leading) {
                        TextField("Item name", text: $item.name)
                        HStack {
                            TextField("Quantity", value: $item.quantity, format: .number).keyboardType(.decimalPad).accessibilityLabel("Quantity")
                            TextField("Line total", value: $item.price, format: .currency(code: "USD")).keyboardType(.decimalPad).accessibilityLabel("Item line total")
                        }
                        if let category = ReceiptParser.category(item.name) { Text(category).font(.caption).foregroundStyle(.secondary) }
                    }
                }.onDelete { items.remove(atOffsets: $0) }
                Button("Add item") { items.append(.init(name: "", price: 0, rawLine: "")) }
            }
            Section("Discounts and rewards") {
                ForEach($benefits) { $benefit in
                    VStack(alignment: .leading) {
                        TextField("Description", text: $benefit.description)
                        Picker("Type", selection: $benefit.kind) {
                            Text("Discount").tag("Discount")
                            Text("Reward (review)").tag("Reward (review)")
                            Text("Future offer (review)").tag("Offer (review)")
                        }
                        TextField("Printed dollar value", value: $benefit.value, format: .currency(code: "USD")).keyboardType(.decimalPad)
                        if benefit.kind == "Offer (review)" {
                            TextField("Percent off (if printed)", value: $benefit.percentValue, format: .number).keyboardType(.decimalPad)
                            Toggle("Expiration printed", isOn: Binding(get: { benefit.expiration != nil }, set: { benefit.expiration = $0 ? Date() : nil }))
                            if benefit.expiration != nil {
                                DatePicker("Offer expires", selection: Binding(get: { benefit.expiration ?? Date() }, set: { benefit.expiration = $0 }), displayedComponents: .date)
                            } else { Text("Expiration unknown — check CVS before using.").font(.caption).foregroundStyle(.secondary) }
                            Toggle("I confirmed this offer is unused and available", isOn: $benefit.available)
                            if !benefit.rawLine.isEmpty { Text(benefit.rawLine).font(.caption).textSelection(.enabled) }
                        }
                    }
                }.onDelete { benefits.remove(atOffsets: $0) }
                Button("Add discount or reward") { benefits.append(.init(description: "", value: 0, kind: "Discount", rawLine: "")) }
                Text("Reward values describe what is printed; confirm whether they were earned, used, or offered.").font(.caption).foregroundStyle(.secondary)
            }
            if !scanner.recognizedText.isEmpty {
                Section("Original recognized text") {
                    Text(scanner.recognizedText).font(.caption).textSelection(.enabled)
                    Button("Copy receipt text") { UIPasteboard.general.string = scanner.recognizedText }
                }
            }
            Section {
                Button(existing == nil ? "Save receipt" : "Save corrections", action: save)
                    .disabled(scanner.isReading || isImporting || isSaving || store.trimmingCharacters(in: .whitespaces).isEmpty)
                if let message { Text(message).foregroundStyle(.red) }
            }
        }
        .scrollDismissesKeyboard(.interactively)
        .navigationTitle(existing == nil ? "Review receipt" : "Edit receipt")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $showingScanner) {
            DocumentScannerView(onComplete: { images in
                showingScanner = false
                Task { await scanner.acceptScan(images: images); applyParsedText() }
            }, onCancel: { showingScanner = false }, onError: { error in
                showingScanner = false; scanner.errorMessage = error.localizedDescription
            })
        }
        .fileImporter(isPresented: $showingFile, allowedContentTypes: [.pdf, .image, .plainText]) { result in
            guard case .success(let url) = result else { return }
            Task {
                isImporting = true
                defer { isImporting = false }
                do {
                    let type = UTType(filenameExtension: url.pathExtension) ?? .data
                    let data = try await Task.detached {
                        let granted = url.startAccessingSecurityScopedResource()
                        defer { if granted { url.stopAccessingSecurityScopedResource() } }
                        let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                        guard size <= 20 * 1024 * 1024 else { throw BackendError.rejected("Choose a receipt smaller than 20 MB.") }
                        return try Data(contentsOf: url)
                    }.value
                    let receipt = try await DigitalReceiptImporter.read(data: data, type: type)
                    scanner.scannedImages = receipt.images; scanner.recognizedText = receipt.text; scanner.errorMessage = nil
                    applyParsedText()
                } catch { scanner.errorMessage = error.localizedDescription }
            }
        }
        .sheet(isPresented: $showingText) {
            NavigationStack {
                Form {
                    Section("Receipt or offer text") {
                        PasteButton(payloadType: String.self) { strings in pastedText = String(strings.joined(separator: "\n").prefix(100000)) }
                        TextEditor(text: $pastedText).frame(minHeight: 240).accessibilityLabel("Receipt text")
                    }
                    Button("Read receipt text") {
                        scanner.recognizedText = String(pastedText.prefix(100000)); scanner.scannedImages = []; scanner.errorMessage = nil
                        applyParsedText(); showingText = false
                    }.disabled(pastedText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }.navigationTitle("Import receipt text")
                    .toolbar { Button("Cancel") { showingText = false } }
            }
        }
        .onChange(of: photo) { _, selected in
            guard let selected else { return }
            Task {
                isImporting = true
                defer { isImporting = false; photo = nil }
                do {
                    guard let data = try await selected.loadTransferable(type: Data.self), let image = UIImage(data: data) else {
                        throw ReceiptOCRError.invalidImage
                    }
                    await scanner.acceptScan(images: [image]); applyParsedText()
                } catch { scanner.errorMessage = "This photo could not be imported. Try another image." }
            }
        }
        .task {
            guard let r = existing else {
                if let initialText {
                    scanner.recognizedText = initialText; applyParsedText(storeHint: initialStore)
                    if let initialStore { store = initialStore }
                }
                return
            }
            store = r.store; purchaseDate = r.purchaseDate; dateDetected = true
            total = r.total; totalConfirmed = true; subtotal = r.subtotal ?? 0; tax = r.tax ?? 0
            hasSubtotal = r.subtotal != nil; hasTax = r.tax != nil; notes = r.notes; member = r.householdMemberName
            scanner.recognizedText = r.ocrText
            items = r.items.map { .init(name: $0.itemName, price: $0.price, quantity: $0.quantity, rawLine: $0.rawLine) }
            benefits = r.benefits.map { .init(id: $0.id, description: $0.benefitDescription, value: $0.value, kind: $0.benefitType == "Offer (review)" ? "Offer (review)" : ($0.benefitType == "Discount" ? "Discount" : "Reward (review)"), rawLine: $0.rawLine, percentValue: $0.percentValue, expiration: $0.expirationDate, available: $0.direction == "Available") }
        }
    }
    private func applyParsedText(storeHint: String? = nil) {
        let parsed = ReceiptParser.parse(scanner.recognizedText, storeHint: storeHint)
        message = parsed.warnings.isEmpty ? nil : parsed.warnings.joined(separator: "\n")
        store = parsed.store; dateDetected = parsed.date != nil; purchaseDate = parsed.date ?? .now
        subtotal = parsed.subtotal ?? 0; tax = parsed.tax ?? 0; total = parsed.total ?? 0
        totalConfirmed = parsed.total != nil
        hasSubtotal = parsed.subtotal != nil; hasTax = parsed.tax != nil; items = parsed.items; benefits = parsed.benefits
    }
    private func save() {
        guard totalConfirmed else { message = "Enter and confirm the receipt’s actual total before saving."; return }
        guard total.isFinite, total >= 0, subtotal.isFinite, subtotal >= 0, tax.isFinite, tax >= 0,
              items.allSatisfy({ !$0.name.trimmingCharacters(in: .whitespaces).isEmpty && $0.quantity.isFinite && $0.quantity > 0 && $0.price.isFinite && $0.price >= 0 }),
              benefits.allSatisfy({ !$0.description.trimmingCharacters(in: .whitespaces).isEmpty && $0.value.isFinite && $0.value >= 0 && $0.percentValue.isFinite && (0...100).contains($0.percentValue) && !($0.percentValue > 0 && $0.value > 0) }) else {
            message = "Check item names, quantities and amounts before saving."; return
        }
        isSaving = true
        defer { isSaving = false }
        let receipt = existing ?? Receipt()
        if existing == nil { context.insert(receipt) }
        receipt.store = store.trimmingCharacters(in: .whitespacesAndNewlines)
        receipt.purchaseDate = purchaseDate; receipt.total = total; receipt.subtotal = hasSubtotal ? subtotal : nil
        receipt.tax = hasTax ? tax : nil; receipt.notes = notes; receipt.householdMemberName = member
        receipt.ocrText = scanner.recognizedText
        if receipt.store == "Target" { receipt.importFingerprint = StoreReceiptBatchSaver.targetFingerprint(text: receipt.ocrText) }
        if let first = scanner.scannedImages.first {
            receipt.imageData = first.jpegData(compressionQuality: 0.8)
            receipt.additionalImages = try? JSONEncoder().encode(scanner.scannedImages.dropFirst().compactMap { $0.jpegData(compressionQuality: 0.8) })
        }
        for item in receipt.items { context.delete(item) }
        for benefit in receipt.benefits { context.delete(benefit) }
        receipt.items = items.map { ReceiptItem(itemName: $0.name, price: $0.price, quantity: $0.quantity, rawLine: $0.rawLine, receipt: receipt) }
        receipt.benefits = benefits.map { ReceiptBenefit(id: $0.id, direction: $0.kind == "Offer (review)" && $0.available ? "Available" : "Reviewed", benefitType: $0.kind, value: $0.value, percentValue: $0.percentValue, benefitDescription: $0.description, expirationDate: $0.expiration, rawLine: $0.rawLine, receipt: receipt) }
        do { try context.save(); dismiss() }
        catch { context.rollback(); message = "This receipt could not be saved. Please try again." }
    }
}
