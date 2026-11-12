// Render launch/demo/cast.json to launch/demo/demo.gif with CoreText + ImageIO.
// macOS only, no dependencies:  swift launch/demo/render.swift
import AppKit
import Foundation
import ImageIO
import UniformTypeIdentifiers

let here = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
let castURL = here.appendingPathComponent("cast.json")
let outURL = here.appendingPathComponent("demo.gif")

struct Frame: Decodable { let lines: [[[String]]]; let delay: Double }
struct Cast: Decodable { let cols: Int; let rows: Int; let frames: [Frame] }

let cast = try JSONDecoder().decode(Cast.self, from: Data(contentsOf: castURL))

let font = NSFont(name: "Menlo", size: 14) ?? NSFont.monospacedSystemFont(ofSize: 14, weight: .regular)
let cellW = ("M" as NSString).size(withAttributes: [.font: font]).width
let lineH: CGFloat = 19
let pad: CGFloat = 16
let width = Int(ceil(CGFloat(cast.cols) * cellW + pad * 2))
let footerH: CGFloat = 26
let height = Int(ceil(CGFloat(cast.rows) * lineH + pad * 2 + 6 + footerH))

func rgb(_ hex: Int) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255, blue: CGFloat(hex & 255) / 255, alpha: 1)
}
let palette: [String: NSColor] = [
    "fg": rgb(0xD4D4D4), "bright": rgb(0xFFFFFF), "dim": rgb(0x8A8A8A), "prompt": rgb(0x9CDCFE),
    "accent": rgb(0xE8915B), "green": rgb(0x7FD18B), "red": rgb(0xF48771), "yellow": rgb(0xE5C07B),
]
let bg = rgb(0x1E1E1E)

guard let dest = CGImageDestinationCreateWithURL(outURL as CFURL, UTType.gif.identifier as CFString, cast.frames.count, nil) else {
    fatalError("cannot create \(outURL.path)")
}
CGImageDestinationSetProperties(dest, [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFLoopCount: 0]] as CFDictionary)

for frame in cast.frames {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: width, pixelsHigh: height, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    bg.setFill()
    NSRect(x: 0, y: 0, width: width, height: height).fill()
    for (i, line) in frame.lines.enumerated() {
        var x = pad
        let y = CGFloat(height) - pad - CGFloat(i + 1) * lineH + 4
        for seg in line {
            let text = seg[0]
            let color = palette[seg.count > 1 ? seg[1] : "fg"] ?? palette["fg"]!
            (text as NSString).draw(at: NSPoint(x: x, y: y), withAttributes: [.font: font, .foregroundColor: color])
            x += CGFloat(text.count) * cellW
        }
    }
    // Persistent label so no single frame can be mistaken for a screen capture.
    rgb(0x2A2A2A).setFill()
    NSRect(x: 0, y: 0, width: CGFloat(width), height: footerH).fill()
    let label = "compact-diff demo · replay of the real hook code on a synthetic session (not a screen capture)"
    let small = NSFont(name: "Menlo", size: 12) ?? font
    (label as NSString).draw(at: NSPoint(x: pad, y: 6), withAttributes: [.font: small, .foregroundColor: rgb(0x8A8A8A)])
    NSGraphicsContext.restoreGraphicsState()
    let props = [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFDelayTime: frame.delay,
                                                 kCGImagePropertyGIFUnclampedDelayTime: frame.delay]] as CFDictionary
    CGImageDestinationAddImage(dest, rep.cgImage!, props)
}
guard CGImageDestinationFinalize(dest) else { fatalError("GIF finalize failed") }
let size = (try? FileManager.default.attributesOfItem(atPath: outURL.path)[.size] as? Int) ?? 0
print("\(cast.frames.count) frames, \(width)x\(height), \(size / 1024) KB -> \(outURL.path)")
