import Foundation
import Combine
import UIKit

@MainActor
final class ReceiptScannerViewModel: ObservableObject {
    @Published var recognizedText = ""
    @Published var isReading = false
    @Published var errorMessage: String?
    @Published var scannedImages: [UIImage] = []

    private let ocr = ReceiptOCRService()

    func acceptScan(
        images: [UIImage]
    ) async {
        recognizedText = ""
        scannedImages = images
        await read(images: images)
    }

    func read(
        images: [UIImage]
    ) async {
        guard !images.isEmpty else {
            errorMessage = "No receipt pages were scanned."
            return
        }

        isReading = true
        errorMessage = nil

        do {
            var pageTexts: [String] = []

            for image in images {
                let text = try await ocr.recognizeText(
                    from: image
                )

                if !text.trimmingCharacters(
                    in: .whitespacesAndNewlines
                ).isEmpty {
                    pageTexts.append(text)
                }
            }

            recognizedText = pageTexts.joined(
                separator: "\n\n--- PAGE ---\n\n"
            )

            if recognizedText.isEmpty {
                errorMessage = "The receipt was scanned, but no text was recognized."
            }
        } catch {
            errorMessage = error.localizedDescription
        }

        isReading = false
    }

    func reset() {
        recognizedText = ""
        errorMessage = nil
        scannedImages = []
    }
}
