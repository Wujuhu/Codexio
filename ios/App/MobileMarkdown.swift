import SwiftUI
import UIKit
import ImageIO

// Read-only native Markdown. Parsing occurs once per changed message off the
// main actor. Images resolve only through the paired Mac's image descriptors.
private enum MobileMarkdownFragment {
    case text(AttributedString)
    case image(String,String)
}
private struct MobileMarkdownBlock: Identifiable {
    enum Kind { case paragraph, heading(Int), list(String,Int), quote, code, table, rule }
    let id: Int
    var kind: Kind
    var fragments: [MobileMarkdownFragment] = []
    var code = ""
    var rows: [[AttributedString]] = []
}
private enum MobileMarkdownParser {
    private static func codeRanges(_ source: String) -> [Range<String.Index>] {
        guard let expression = try? NSRegularExpression(pattern:#"(?<!`)(`+)(?!`)[\s\S]*?(?<!`)\1(?!`)"#) else { return [] }
        return expression.matches(in:source,range:NSRange(source.startIndex...,in:source)).compactMap { Range($0.range,in:source) }
    }
    private static func fragments(_ source: String) -> [MobileMarkdownFragment] {
        guard let expression = try? NSRegularExpression(pattern:#"(?<!\\)!\[((?:\\.|[^\]\\])*)\]\(\s*<?codexio-image://([0-9a-fA-F]{64})>?(?:\s+\"[^\"]*\")?\s*\)"#) else { return [.text(inline(source))] }
        let code = codeRanges(source)
        var result: [MobileMarkdownFragment] = [], cursor = source.startIndex
        for match in expression.matches(in:source,range:NSRange(source.startIndex...,in:source)) {
            guard let range = Range(match.range,in:source), let caption = Range(match.range(at:1),in:source),
                  let identifier = Range(match.range(at:2),in:source), !code.contains(where:{$0.contains(range.lowerBound)}) else { continue }
            if cursor < range.lowerBound { result.append(.text(inline(String(source[cursor..<range.lowerBound])))) }
            result.append(.image(String(source[identifier]).lowercased(),String(source[caption])))
            cursor = range.upperBound
        }
        if cursor < source.endIndex { result.append(.text(inline(String(source[cursor...])))) }
        return result.isEmpty ? [.text(inline(source))] : result
    }
    static func inline(_ source: String) -> AttributedString {
        var text = source
        let code = codeRanges(source)
        if let expression = try? NSRegularExpression(pattern:#"(!?)\[([^\]]*)\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)"#) {
            let matches = expression.matches(in:text,range:NSRange(text.startIndex...,in:text))
            for match in matches.reversed() {
                guard let range = Range(match.range,in:text), let image = Range(match.range(at:1),in:text),
                      let label = Range(match.range(at:2),in:text), let target = Range(match.range(at:3),in:text),
                      !code.contains(where:{$0.contains(range.lowerBound)}) else { continue }
                let destination = String(text[target]).trimmingCharacters(in:CharacterSet(charactersIn:"<>")), caption = String(text[label])
                let scheme = URL(string:destination)?.scheme?.lowercased()
                let isImage = !text[image].isEmpty
                if isImage || (scheme != "https" && scheme != "http") {
                    let path = destination.removingPercentEncoding ?? destination
                    let filename = URL(fileURLWithPath:path).lastPathComponent.replacingOccurrences(of:#":\d+(?::\d+)?$"#,with:"",options:.regularExpression)
                    let name = filename.isEmpty ? (caption.isEmpty ? "附件" : caption) : filename
                    text.replaceSubrange(range,with:isImage ? "［图片：\(name)］" : name)
                }
            }
        }
        var result = (try? AttributedString(markdown:text,options:.init(interpretedSyntax:.inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
        // Also remove reference-style/autolink schemes that bypass the inline
        // syntax above. File names remain text and cannot trigger downloads.
        for run in result.runs {
            if let url = run.link, !["https","http"].contains(url.scheme?.lowercased() ?? "") { result[run.range].link = nil }
        }
        return result
    }
    private static func cells(_ line: String) -> [String] {
        var values: [String] = [], value = "", escaped = false, code = false
        for character in line {
            if escaped { value.append(character); escaped = false; continue }
            if character == "\\" { escaped = true; value.append(character); continue }
            if character == "`" { code.toggle() }
            if character == "|", !code { values.append(value); value = "" } else { value.append(character) }
        }
        values.append(value)
        if values.first?.trimmingCharacters(in:.whitespaces).isEmpty == true { values.removeFirst() }
        if values.last?.trimmingCharacters(in:.whitespaces).isEmpty == true { values.removeLast() }
        return values.map {$0.trimmingCharacters(in:.whitespaces)}
    }
    static func parse(_ text: String) -> [MobileMarkdownBlock] {
        let lines = text.components(separatedBy:.newlines)
        var blocks: [MobileMarkdownBlock] = [], index = 0, paragraph: [String] = []
        func flush() {
            if !paragraph.isEmpty { blocks.append(.init(id:blocks.count,kind:.paragraph,fragments:fragments(paragraph.joined(separator:"\n")))); paragraph.removeAll() }
        }
        while index < lines.count {
            let line = lines[index], trimmed = line.trimmingCharacters(in:.whitespaces)
            if trimmed.isEmpty { flush(); index += 1; continue }
            if trimmed.hasPrefix("```") || trimmed.hasPrefix("~~~") {
                flush(); let fence = String(trimmed.prefix(3)); index += 1; var code: [String] = []
                while index < lines.count, !lines[index].trimmingCharacters(in:.whitespaces).hasPrefix(fence) { code.append(lines[index]); index += 1 }
                blocks.append(.init(id:blocks.count,kind:.code,code:code.joined(separator:"\n"))); if index < lines.count { index += 1 }; continue
            }
            if index+1 < lines.count, trimmed.contains("|") {
                let separators = cells(lines[index+1])
                if !separators.isEmpty, separators.allSatisfy({$0.range(of:#"^:?-{3,}:?$"#,options:.regularExpression) != nil}) {
                    flush(); var rows = [cells(line).map(inline)]; index += 2
                    while index < lines.count, !lines[index].trimmingCharacters(in:.whitespaces).isEmpty, lines[index].contains("|") { rows.append(cells(lines[index]).map(inline)); index += 1 }
                    blocks.append(.init(id:blocks.count,kind:.table,rows:rows)); continue
                }
            }
            let hashes = trimmed.prefix(while:{$0 == "#"}).count
            if (1...6).contains(hashes), trimmed.dropFirst(hashes).first == " " {
                flush(); blocks.append(.init(id:blocks.count,kind:.heading(hashes),fragments:fragments(String(trimmed.dropFirst(hashes+1))))); index += 1; continue
            }
            if ["---","***","___"].contains(trimmed) { flush(); blocks.append(.init(id:blocks.count,kind:.rule)); index += 1; continue }
            if trimmed.hasPrefix(">") { flush(); blocks.append(.init(id:blocks.count,kind:.quote,fragments:fragments(String(trimmed.dropFirst()).trimmingCharacters(in:.whitespaces)))); index += 1; continue }
            if let range = trimmed.range(of:#"^(?:[-+*]|\d+[.)])\s+"#,options:.regularExpression) {
                flush(); let marker = String(trimmed[range]).trimmingCharacters(in:.whitespaces), depth = min(4,(line.count-trimmed.count)/2)
                blocks.append(.init(id:blocks.count,kind:.list(["-","+","*"].contains(marker) ? "•" : marker,depth),fragments:fragments(String(trimmed[range.upperBound...])))); index += 1; continue
            }
            paragraph.append(line); index += 1
        }
        flush(); return blocks
    }
}

struct MobileMarkdownView: View {
    let text: String
    var images: [MobileImageReference] = []
    @State private var blocks: [MobileMarkdownBlock] = []
    @State private var referencedImages = Set<String>()
    var body: some View {
        LazyVStack(alignment:.leading,spacing:10) {
            ForEach(blocks) { block in content(block) }
            // A text preview may stop before an image target or inside a code
            // fence. Add missing descriptors as views outside the parsed text.
            ForEach(images.filter {!referencedImages.contains($0.id)}) { reference in
                MobileInlineImageView(reference:reference)
            }
        }.frame(maxWidth:.infinity,alignment:.leading).textSelection(.enabled)
            .task(id:text) {
                let parsed = await Task.detached(priority:.utility) {
                    let blocks = MobileMarkdownParser.parse(text)
                    let ids = Set(blocks.flatMap(\.fragments).compactMap { fragment -> String? in
                        if case .image(let id,_) = fragment { return id }; return nil
                    })
                    return (blocks,ids)
                }.value
                if !Task.isCancelled { blocks = parsed.0; referencedImages = parsed.1 }
            }
    }
    @ViewBuilder private func content(_ block: MobileMarkdownBlock) -> some View {
        switch block.kind {
        case .paragraph: inlineContent(block,font:.body)
        case .heading(let level): inlineContent(block,font:level == 1 ? .title2.bold() : level == 2 ? .title3.bold() : .headline).padding(.top,3)
        case .list(let marker,let depth): HStack(alignment:.top,spacing:8) { Text(marker); inlineContent(block,font:.body) }.padding(.leading,CGFloat(depth)*12)
        case .quote: HStack(alignment:.top,spacing:10) { Rectangle().fill(.secondary.opacity(0.4)).frame(width:3); inlineContent(block,font:.body).foregroundStyle(.secondary) }.fixedSize(horizontal:false,vertical:true)
        case .code: ScrollView(.horizontal) { Text(block.code).font(.system(.caption,design:.monospaced)).fixedSize(horizontal:true,vertical:true).padding(12) }.background(.secondary.opacity(0.07),in:RoundedRectangle(cornerRadius:8))
        case .table:
            MobileMarkdownTable(rows:block.rows)
        case .rule: Divider()
        }
    }
    private func inlineContent(_ block: MobileMarkdownBlock, font: Font) -> some View {
        LazyVStack(alignment:.leading,spacing:8) {
            ForEach(Array(block.fragments.enumerated()),id:\.offset) { _,fragment in
                switch fragment {
                case .text(let value):
                    Text(value).font(font).fixedSize(horizontal:false,vertical:true)
                case .image(let id,let caption):
                    if let reference = images.first(where:{$0.id == id}) {
                        MobileInlineImageView(reference:reference).id(reference.id)
                    } else {
                        Label(caption.isEmpty ? "图片暂不可用" : caption,systemImage:"photo")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                }
            }
        }.frame(maxWidth:.infinity,alignment:.leading)
    }
}

private struct MobileMarkdownTable: View {
    let rows: [[AttributedString]]
    @ScaledMetric(relativeTo:.subheadline) private var columnWidth: CGFloat = 160
    private var columnCount: Int { rows.map(\.count).max() ?? 0 }
    var body: some View {
        ScrollView(.horizontal) {
            VStack(alignment:.leading,spacing:8) {
                ForEach(Array(rows.enumerated()),id:\.offset) { index,row in
                    HStack(alignment:.top,spacing:14) {
                        ForEach(0..<columnCount,id:\.self) { column in
                            // Constrain width before measuring the full wrapped
                            // height; the row then grows to its tallest cell.
                            Text(column < row.count ? row[column] : AttributedString(""))
                                .font(index == 0 ? .subheadline.weight(.semibold) : .subheadline)
                                .lineLimit(nil)
                                .fixedSize(horizontal:false,vertical:true)
                                .frame(width:columnWidth,alignment:.topLeading)
                        }
                    }
                    if index == 0 { Divider() }
                }
            }.fixedSize(horizontal:true,vertical:true).padding(10)
        }.fixedSize(horizontal:false,vertical:true)
            .background(.secondary.opacity(0.05),in:RoundedRectangle(cornerRadius:8))
    }
}

private enum MobileImageDecoder {
    static func decode(_ data: Data, maximumDimension: Int) -> UIImage? {
        guard data.count <= 1_048_576,
              let source = CGImageSourceCreateWithData(data as CFData,[kCGImageSourceShouldCache:false] as CFDictionary),
              let properties = CGImageSourceCopyPropertiesAtIndex(source,0,nil) as? [CFString:Any],
              let width = properties[kCGImagePropertyPixelWidth] as? NSNumber,
              let height = properties[kCGImagePropertyPixelHeight] as? NSNumber,
              width.intValue > 0, height.intValue > 0, width.intValue <= 2048, height.intValue <= 2048,
              let image = CGImageSourceCreateThumbnailAtIndex(source,0,[
                kCGImageSourceCreateThumbnailFromImageAlways:true,
                kCGImageSourceCreateThumbnailWithTransform:true,
                kCGImageSourceShouldCacheImmediately:true,
                kCGImageSourceThumbnailMaxPixelSize:max(1,min(2048,maximumDimension))
              ] as CFDictionary) else { return nil }
        return UIImage(cgImage:image)
    }
}

private struct MobileInlineImageView: View {
    @EnvironmentObject private var store: MobileStore
    @Environment(\.displayScale) private var displayScale
    let reference: MobileImageReference
    @State private var image: UIImage?
    @State private var displayWidth: CGFloat = 0
    @State private var decodeFailed = false
    @State private var deadlinePassed = false
    @State private var showImage = false
    @State private var displayedFor = ""
    private var expired: Bool { deadlinePassed || store.imageIsExpired(reference) }
    private var dimension: Int {
        let ratio = CGFloat(max(reference.width,reference.height))/CGFloat(max(1,reference.width))
        return max(1,min(2048,Int(ceil(displayWidth*displayScale*ratio))))
    }
    private var loadKey: String { store.selected+reference.id+reference.availability+String(reference.expires)+String(dimension)+String(store.imageValues[reference.id] != nil)+String(store.device?.invalid == true)+String(expired) }
    var body: some View {
        VStack(alignment:.leading,spacing:6) {
            if let image, !expired {
                Button { showImage = true } label: {
                    Image(uiImage:image).resizable().scaledToFit().frame(maxWidth:.infinity)
                        .clipShape(RoundedRectangle(cornerRadius:8))
                }.buttonStyle(.plain).accessibilityLabel("查看图片："+reference.name)
            } else {
                VStack(spacing:10) {
                    if expired { Label("图片已过期",systemImage:"photo") }
                    else if reference.availability != "available" { Label("图片暂不可用",systemImage:"photo") }
                    else if let error = store.imageErrors[reference.id] {
                        Text(error).multilineTextAlignment(.center)
                        Button("重新读取") { store.loadImage(reference,force:true) }
                    } else if decodeFailed { Label("图片无法显示",systemImage:"photo") }
                    else { ProgressView("正在读取图片") }
                }.font(.subheadline).foregroundStyle(.secondary)
                    .frame(maxWidth:.infinity,minHeight:140).padding(10)
                    .background(.secondary.opacity(0.06),in:RoundedRectangle(cornerRadius:8))
            }
        }.frame(maxWidth:.infinity,alignment:.leading)
            .onGeometryChange(for:CGFloat.self) { $0.size.width } action: { displayWidth = $0 }
            .task(id:loadKey) {
                let owner = store.selected+reference.id
                if displayedFor != owner { image = nil; decodeFailed = false; showImage = false; displayedFor = owner }
                guard !expired, reference.availability == "available", store.device?.invalid != true else {
                    image = nil; showImage = false; return
                }
                guard let bytes = store.imageValues[reference.id] else {
                    // Compressed cache eviction must not discard visible
                    // decoded pixels and trigger an endless download cycle.
                    if image == nil { store.loadImage(reference) }
                    return
                }
                guard displayWidth > 0 else { return }
                decodeFailed = false
                let size = dimension
                let decoded = await Task.detached(priority:.utility) { MobileImageDecoder.decode(bytes,maximumDimension:size) }.value
                guard !Task.isCancelled, !expired else { return }
                image = decoded; decodeFailed = decoded == nil
            }
            .task(id:reference.expires) {
                deadlinePassed = reference.expires <= Date().timeIntervalSince1970
                while !Task.isCancelled, !deadlinePassed {
                    let remaining = reference.expires-Date().timeIntervalSince1970
                    if remaining <= 0 { deadlinePassed = true; break }
                    do { try await Task.sleep(for:.seconds(remaining)) } catch { return }
                }
                if deadlinePassed { image = nil; showImage = false }
            }
            .onDisappear { if !showImage { image = nil; decodeFailed = false } }
            .fullScreenCover(isPresented:$showImage) {
                if let image { MobileImagePreview(image:image,name:reference.name,data:store.imageValues[reference.id],expires:reference.expires) }
            }
    }
}

struct MobileLegacyImageView: View {
    let attachment: MobileAttachment
    let expires: Double
    let load: () -> Void
    @State private var image: UIImage?
    @State private var showImage = false
    @State private var deadlinePassed = false
    private var expired: Bool { deadlinePassed || expires <= Date().timeIntervalSince1970 }
    var body: some View {
        Group {
            if let image, !expired {
                Button { showImage = true } label: {
                    Image(uiImage:image).resizable().scaledToFit().frame(maxWidth:.infinity)
                        .clipShape(RoundedRectangle(cornerRadius:8))
                }.buttonStyle(.plain).accessibilityLabel("查看图片："+attachment.name)
            } else {
                Label(expired ? "图片已过期" : "图片暂不可用",systemImage:"photo").font(.subheadline).foregroundStyle(.secondary)
            }
        }.task(id:attachment.thumbnail) {
            guard !expired else { return }
            guard let encoded = attachment.thumbnail else { load(); return }
            guard encoded.utf8.count <= 16_384 else { return }
            let decoded = await Task.detached(priority:.utility) { () -> UIImage? in
                guard let bytes = Data(base64Encoded:encoded), bytes.count <= 12_288 else { return nil }
                return MobileImageDecoder.decode(bytes,maximumDimension:240)
            }.value
            if !Task.isCancelled, !expired { image = decoded }
        }.task(id:expires) {
            deadlinePassed = expires <= Date().timeIntervalSince1970
            while !Task.isCancelled, !deadlinePassed {
                let remaining = expires-Date().timeIntervalSince1970
                if remaining <= 0 { deadlinePassed = true; break }
                do { try await Task.sleep(for:.seconds(remaining)) } catch { return }
            }
            if deadlinePassed { image = nil; showImage = false }
        }.fullScreenCover(isPresented:$showImage) {
            if let image { MobileImagePreview(image:image,name:attachment.name,expires:expires) }
        }
    }
}

private struct MobileImagePreview: View {
    @Environment(\.dismiss) private var dismiss
    let image: UIImage
    let name: String
    var data: Data? = nil
    var expires: Double? = nil
    @State private var enlarged: UIImage?
    var body: some View {
        NavigationStack {
            MobileImageZoomView(image:enlarged ?? image).background(.black)
                .navigationTitle(name).navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement:.topBarTrailing) { Button("完成") { dismiss() } } }
        }.task {
            guard let data else { return }
            let decoded = await Task.detached(priority:.utility) { MobileImageDecoder.decode(data,maximumDimension:2048) }.value
            if !Task.isCancelled { enlarged = decoded }
        }.task(id:expires) {
            guard let expires else { return }
            while !Task.isCancelled {
                let remaining = expires-Date().timeIntervalSince1970
                if remaining <= 0 { dismiss(); return }
                do { try await Task.sleep(for:.seconds(remaining)) } catch { return }
            }
        }
    }
}

private struct MobileImageZoomView: UIViewRepresentable {
    let image: UIImage
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> MobileImageScrollView {
        let view = MobileImageScrollView(frame:.zero)
        view.delegate = context.coordinator
        view.backgroundColor = .black
        view.display(image)
        return view
    }
    func updateUIView(_ view: MobileImageScrollView, context: Context) { view.display(image) }
    final class Coordinator: NSObject, UIScrollViewDelegate {
        func viewForZooming(in scrollView: UIScrollView) -> UIView? { (scrollView as? MobileImageScrollView)?.imageView }
        func scrollViewDidZoom(_ scrollView: UIScrollView) { (scrollView as? MobileImageScrollView)?.centerImage() }
    }
}

private final class MobileImageScrollView: UIScrollView {
    let imageView = UIImageView()
    private var fittedSize = CGSize.zero
    override init(frame: CGRect) {
        super.init(frame:frame)
        addSubview(imageView)
        showsHorizontalScrollIndicator = false; showsVerticalScrollIndicator = false
        contentInsetAdjustmentBehavior = .never
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    func display(_ image: UIImage) {
        guard imageView.image !== image else { return }
        minimumZoomScale = 1; maximumZoomScale = 1; zoomScale = 1
        imageView.image = image
        imageView.frame = CGRect(origin:.zero,size:image.size); contentSize = image.size
        fittedSize = .zero; setNeedsLayout()
    }
    override func layoutSubviews() {
        super.layoutSubviews()
        guard let image = imageView.image, bounds.width > 0, bounds.height > 0 else { return }
        let needsFit = fittedSize != bounds.size
        if needsFit {
            fittedSize = bounds.size
            let fit = min(bounds.width/image.size.width,bounds.height/image.size.height)
            minimumZoomScale = min(1,fit); maximumZoomScale = max(1,fit*6)
            minimumZoomScale = fit; zoomScale = fit
        }
        centerImage()
        if needsFit { contentOffset = CGPoint(x:-contentInset.left,y:-contentInset.top) }
    }
    func centerImage() {
        let horizontal = max(0,(bounds.width-contentSize.width)/2), vertical = max(0,(bounds.height-contentSize.height)/2)
        let inset = UIEdgeInsets(top:vertical,left:horizontal,bottom:vertical,right:horizontal)
        if contentInset != inset { contentInset = inset }
    }
}
