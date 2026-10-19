// Local OCR only: native PDFKit / Vision, no network or document transmission.
import Foundation
import Vision
import PDFKit
import AppKit
import ImageIO

func recognize(_ image: CGImage, orientation: CGImagePropertyOrientation = .up) throws -> String {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["es", "en"]
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage: image, orientation: orientation, options: [:]).perform([request])
    return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
}
guard CommandLine.arguments.count == 2 else { exit(2) }
do {
let url = URL(fileURLWithPath: CommandLine.arguments[1])
if url.pathExtension == "pdf" {
    guard let doc = PDFDocument(url: url), !doc.isLocked, doc.pageCount <= 20 else { exit(2) }
    for index in 0..<doc.pageCount {
        guard let page = doc.page(at: index) else { exit(2) }
        let text = page.string ?? ""
        if text.trimmingCharacters(in: .whitespacesAndNewlines).count > 30 { print(text); continue }
        let image = page.thumbnail(of: NSSize(width: 1800, height: 2400), for: .mediaBox)
        var rect = CGRect(origin: .zero, size: image.size)
        guard let cg = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else { exit(2) }
        print(try recognize(cg))
    }
} else {
    guard let source = CGImageSourceCreateWithURL(url as CFURL,nil),
          let props = CGImageSourceCopyPropertiesAtIndex(source,0,nil) as? [CFString:Any],
          let width = props[kCGImagePropertyPixelWidth] as? Int,
          let height = props[kCGImagePropertyPixelHeight] as? Int,
          width > 0, height > 0, width <= 20000, height <= 20000, width * height <= 40000000,
          let cg = CGImageSourceCreateImageAtIndex(source,0,nil) else { exit(2) }
    let orientation = CGImagePropertyOrientation(rawValue: (props[kCGImagePropertyOrientation] as? NSNumber)?.uint32Value ?? 1) ?? .up
    print(try recognize(cg, orientation: orientation))
}

} catch {
    // Preserve review fallback without crashing or printing document contents.
    fputs("Local document recognition failed.\n", stderr)
    exit(2)
}
