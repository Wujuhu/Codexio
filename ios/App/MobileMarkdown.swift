import SwiftUI
import UIKit
import ImageIO

// Read-only native Markdown. Parsing occurs once per changed message off the
// main actor. Links are restricted to web URLs; images never create URL loads.
private struct MobileMarkdownBlock: Identifiable {
    enum Kind { case paragraph, heading(Int), list(String,Int), quote, code, table, rule }
    let id: Int
    var kind: Kind
    var text: AttributedString = AttributedString("")
    var code = ""
    var rows: [[AttributedString]] = []
}
private enum MobileMarkdownParser {
    static func inline(_ source: String) -> AttributedString {
        var text = source
        if let expression = try? NSRegularExpression(pattern:#"(!?)\[([^\]]*)\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)"#) {
            let matches = expression.matches(in:text,range:NSRange(text.startIndex...,in:text))
            for match in matches.reversed() {
                guard let range = Range(match.range,in:text), let image = Range(match.range(at:1),in:text),
                      let label = Range(match.range(at:2),in:text), let target = Range(match.range(at:3),in:text) else { continue }
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
            if !paragraph.isEmpty { blocks.append(.init(id:blocks.count,kind:.paragraph,text:inline(paragraph.joined(separator:"\n")))); paragraph.removeAll() }
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
                flush(); blocks.append(.init(id:blocks.count,kind:.heading(hashes),text:inline(String(trimmed.dropFirst(hashes+1))))); index += 1; continue
            }
            if ["---","***","___"].contains(trimmed) { flush(); blocks.append(.init(id:blocks.count,kind:.rule)); index += 1; continue }
            if trimmed.hasPrefix(">") { flush(); blocks.append(.init(id:blocks.count,kind:.quote,text:inline(String(trimmed.dropFirst()).trimmingCharacters(in:.whitespaces)))); index += 1; continue }
            if let range = trimmed.range(of:#"^(?:[-+*]|\d+[.)])\s+"#,options:.regularExpression) {
                flush(); let marker = String(trimmed[range]).trimmingCharacters(in:.whitespaces), depth = min(4,(line.count-trimmed.count)/2)
                blocks.append(.init(id:blocks.count,kind:.list(["-","+","*"].contains(marker) ? "•" : marker,depth),text:inline(String(trimmed[range.upperBound...])))); index += 1; continue
            }
            paragraph.append(line); index += 1
        }
        flush(); return blocks
    }
}

struct MobileMarkdownView: View {
    let text: String
    @State private var blocks: [MobileMarkdownBlock] = []
    var body: some View {
        VStack(alignment:.leading,spacing:10) {
            ForEach(blocks) { block in content(block) }
        }.frame(maxWidth:.infinity,alignment:.leading).textSelection(.enabled)
            .task(id:text) {
                let parsed = await Task.detached(priority:.utility) { MobileMarkdownParser.parse(text) }.value
                if !Task.isCancelled { blocks = parsed }
            }
    }
    @ViewBuilder private func content(_ block: MobileMarkdownBlock) -> some View {
        switch block.kind {
        case .paragraph: Text(block.text).font(.body).fixedSize(horizontal:false,vertical:true)
        case .heading(let level): Text(block.text).font(level == 1 ? .title2.bold() : level == 2 ? .title3.bold() : .headline).padding(.top,3)
        case .list(let marker,let depth): HStack(alignment:.top,spacing:8) { Text(marker); Text(block.text).frame(maxWidth:.infinity,alignment:.leading) }.padding(.leading,CGFloat(depth)*12)
        case .quote: HStack(alignment:.top,spacing:10) { Rectangle().fill(.secondary.opacity(0.4)).frame(width:3); Text(block.text).foregroundStyle(.secondary) }.fixedSize(horizontal:false,vertical:true)
        case .code: ScrollView(.horizontal) { Text(block.code).font(.system(.caption,design:.monospaced)).fixedSize(horizontal:true,vertical:true).padding(12) }.background(.secondary.opacity(0.07),in:RoundedRectangle(cornerRadius:8))
        case .table:
            ScrollView(.horizontal) {
                Grid(alignment:.topLeading,horizontalSpacing:14,verticalSpacing:8) {
                    ForEach(Array(block.rows.enumerated()),id:\.offset) { index,row in
                        GridRow { ForEach(Array(row.enumerated()),id:\.offset) { _,cell in Text(cell).font(index == 0 ? .subheadline.weight(.semibold) : .subheadline).frame(minWidth:60,maxWidth:240,alignment:.leading).fixedSize(horizontal:false,vertical:true) } }
                        if index == 0 { Divider().gridCellUnsizedAxes(.horizontal) }
                    }
                }.padding(10)
            }.background(.secondary.opacity(0.05),in:RoundedRectangle(cornerRadius:8))
        case .rule: Divider()
        }
    }
}

struct MobileAttachmentView: View {
    let attachment: MobileAttachment
    @State private var image: UIImage?
    var body: some View {
        VStack(alignment:.leading,spacing:8) {
            Label(attachment.name,systemImage:(attachment.mime ?? "").hasPrefix("image/") ? "photo" : "doc").font(.subheadline)
            if let image { Image(uiImage:image).resizable().scaledToFit().frame(maxWidth:240,maxHeight:240).clipShape(RoundedRectangle(cornerRadius:8)) }
            else if (attachment.mime ?? "").hasPrefix("image/") { Text("缩略图未同步").font(.caption).foregroundStyle(.secondary) }
        }.task(id:attachment.thumbnail) {
            guard let encoded = attachment.thumbnail, encoded.utf8.count <= 16_384 else { image = nil; return }
            image = await Task.detached(priority:.utility) { () -> UIImage? in
                guard let data = Data(base64Encoded:encoded), data.count <= 12_288,
                      let source = CGImageSourceCreateWithData(data as CFData,[kCGImageSourceShouldCache:false] as CFDictionary),
                      let properties = CGImageSourceCopyPropertiesAtIndex(source,0,nil) as? [CFString:Any],
                      let width = properties[kCGImagePropertyPixelWidth] as? NSNumber,
                      let height = properties[kCGImagePropertyPixelHeight] as? NSNumber,
                      width.intValue > 0, height.intValue > 0, width.intValue <= 240, height.intValue <= 240 else { return nil }
                return UIImage(data:data)
            }.value
        }
    }
}
