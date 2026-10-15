import Foundation
import Vision
import UIKit
import ImageIO

struct ReceiptOCRService {
    func recognizeText(from image: UIImage) async throws -> String {
        guard let cgImage = image.cgImage else { throw ReceiptOCRError.invalidImage }
        let orientation: CGImagePropertyOrientation
        switch image.imageOrientation {
        case .up: orientation = .up
        case .down: orientation = .down
        case .left: orientation = .left
        case .right: orientation = .right
        case .upMirrored: orientation = .upMirrored
        case .downMirrored: orientation = .downMirrored
        case .leftMirrored: orientation = .leftMirrored
        case .rightMirrored: orientation = .rightMirrored
        @unknown default: orientation = .up
        }
        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    let request = VNRecognizeTextRequest()
                    request.recognitionLevel = .accurate
                    request.usesLanguageCorrection = true
                    request.recognitionLanguages = ["en-US"]
                    try VNImageRequestHandler(cgImage: cgImage, orientation: orientation).perform([request])
                    let lines = (request.results ?? []).sorted {
                        if abs($0.boundingBox.midY - $1.boundingBox.midY) > 0.012 { return $0.boundingBox.midY > $1.boundingBox.midY }
                        return $0.boundingBox.minX < $1.boundingBox.minX
                    }.compactMap { $0.topCandidates(1).first?.string }
                    continuation.resume(returning: lines.joined(separator: "\n"))
                } catch { continuation.resume(throwing: error) }
            }
        }
    }
}
enum ReceiptOCRError: LocalizedError {
    case invalidImage
    var errorDescription: String? { "This image could not be read. Try another photo." }
}
