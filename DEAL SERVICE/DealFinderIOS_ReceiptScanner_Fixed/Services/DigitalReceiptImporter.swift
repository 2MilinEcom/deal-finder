import Foundation
import PDFKit
import UIKit
import UniformTypeIdentifiers

struct ImportedReceipt {
    let text: String
    let images: [UIImage]
}

@MainActor
enum DigitalReceiptImporter {
    static func read(data: Data, type: UTType) async throws -> ImportedReceipt {
        guard data.count <= 20 * 1024 * 1024 else { throw BackendError.rejected("Choose a receipt smaller than 20 MB.") }
        if type.conforms(to: .plainText) {
            guard let text = String(data: data, encoding: .utf8), !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  text.count <= 100000 else { throw BackendError.rejected("This text receipt could not be read. Use a PDF or screenshot instead.") }
            return ImportedReceipt(text: text, images: [])
        }
        if type.conforms(to: .pdf) {
            guard let document = PDFDocument(data: data), !document.isLocked, (1...20).contains(document.pageCount) else {
                throw BackendError.rejected("Choose an unlocked PDF with 1–20 pages.")
            }
            var texts: [String] = []; var images: [UIImage] = []
            for index in 0..<document.pageCount {
                guard let page = document.page(at: index) else { continue }
                let bounds = page.bounds(for: .mediaBox)
                guard bounds.width > 0, bounds.height > 0 else { continue }
                let scale = min(1800 / bounds.width, 1800 / bounds.height)
                let image = page.thumbnail(of: CGSize(width: bounds.width * scale, height: bounds.height * scale), for: .mediaBox)
                images.append(image)
                let text = (page.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                texts.append(text.isEmpty ? try await ReceiptOCRService().recognizeText(from: image) : text)
            }
            let text = texts.joined(separator: "\n\n--- PAGE ---\n\n")
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, text.count <= 100000 else {
                throw BackendError.rejected("No readable receipt text was found. Try a clear screenshot.")
            }
            return ImportedReceipt(text: text, images: images)
        }
        guard type.conforms(to: .image), let image = UIImage(data: data) else { throw ReceiptOCRError.invalidImage }
        return ImportedReceipt(text: try await ReceiptOCRService().recognizeText(from: image), images: [image])
    }
}
