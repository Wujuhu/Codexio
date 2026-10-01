import Foundation

// Only decoded image bytes live here. Message rows and incremental cursors keep
// small file references, and eviction never touches a user's original files.
final class RequestMediaStore {
    let directory: URL
    private let lock = NSLock()
    private var entries: [String:(bytes: Int,used: Date)]?
    private let maximumBytes = 134_217_728
    private let maximumFiles = 256
    init(database: URL) { directory = database.deletingLastPathComponent().appendingPathComponent("request-media",isDirectory:true) }
    func materialize(_ source: String) -> URL? {
        guard source.utf8.count <= 12_000_000, let comma = source.firstIndex(of:",") else { return nil }
        let header = source[..<comma].lowercased(), mime = String(header.dropFirst(5).split(separator:";").first ?? "")
        guard header.hasPrefix("data:image/"), header.hasSuffix(";base64"),
              let ext = RequestMedia.extensions[mime], let data = Data(base64Encoded:String(source[source.index(after:comma)...]),options:.ignoreUnknownCharacters),
              !data.isEmpty, data.count <= 8_388_608 else { return nil }
        lock.lock(); defer { lock.unlock() }
        let manager = FileManager.default
        do {
            try manager.createDirectory(at:directory,withIntermediateDirectories:true)
            guard directory.resolvingSymlinksInPath() == directory.standardizedFileURL else { return nil }
            if entries == nil {
                entries = [:]
                for file in try manager.contentsOfDirectory(at:directory,includingPropertiesForKeys:[.isRegularFileKey,.isSymbolicLinkKey,.fileSizeKey,.contentModificationDateKey]) {
                    guard Self.ownsName(file.lastPathComponent), let info = try? file.resourceValues(forKeys:[.isRegularFileKey,.isSymbolicLinkKey,.fileSizeKey,.contentModificationDateKey]),
                          info.isRegularFile == true, info.isSymbolicLink != true else { continue }
                    entries?[file.lastPathComponent] = (info.fileSize ?? 0,info.contentModificationDate ?? .distantPast)
                }
            }
            let name = digest(data)+"."+ext, file = directory.appendingPathComponent(name)
            guard file.resolvingSymlinksInPath() == file.standardizedFileURL else { return nil }
            if !manager.fileExists(atPath:file.path) { try data.write(to:file,options:.atomic) }
            entries?[name] = (data.count,Date())
            var total = entries?.values.reduce(0) {$0+$1.bytes} ?? 0
            for (old,entry) in (entries ?? [:]).sorted(by:{$0.value.used < $1.value.used}) {
                if (entries?.count ?? 0) <= maximumFiles && total <= maximumBytes { break }
                guard old != name else { continue }
                try? manager.removeItem(at:directory.appendingPathComponent(old))
                total -= entry.bytes; entries?.removeValue(forKey:old)
            }
            return file
        } catch { return nil }
    }
    func missingIDs(_ detail: Object) -> [String] {
        ["attachments","final_attachments","generated_attachments"].flatMap { detail.objects($0) }.compactMap { image in
            let path = image.string("path")
            return path.hasPrefix(directory.path+"/") && !FileManager.default.fileExists(atPath:path) ? image.string("id") : nil
        }.sorted()
    }
    private static func ownsName(_ name: String) -> Bool {
        name.range(of:#"^[a-f0-9]{64}\.(png|jpg|gif|webp|heic|heif|avif|tiff|bmp|svg)$"#,options:.regularExpression) != nil
    }
}

enum RequestMedia {
    static let schema = 1
    static let limit = 32
    static let extensions = ["image/png":"png","image/jpeg":"jpg","image/gif":"gif","image/webp":"webp","image/heic":"heic","image/heif":"heif","image/avif":"avif","image/tiff":"tiff","image/bmp":"bmp","image/svg+xml":"svg"]
    private static let imageTypes: Set<String> = ["image","input_image","output_image","image_url","local_image","inputimage","outputimage","localimage","imageurl"]
    private static let markdown = try! NSRegularExpression(pattern:#"!\[((?:\\.|[^\]\\])*)\]\(\s*(?:<([^<>\r\n]+)>|((?:\\.|[^\r\n\\()"']|\((?:\\.|[^\\()])*\))+?))\s*(?:"[^"\r\n]*"|'[^'\r\n]*'|\([^\)\r\n]*\))?\s*\)"#)
    private static let html = try! NSRegularExpression(pattern:#"(?is)<img\b[^>]*?\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))[^>]*>"#)
    private static let xml = try! NSRegularExpression(pattern:#"(?is)<image\b[^>]*(?:/>|>.*?</image\s*>|>)"#)
    private static let attributes = try! NSRegularExpression(pattern:#"(?is)\b(?:src|path|file_path|url)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))"#)
    private static let wrapper = try! NSRegularExpression(pattern:#"(?m)^## ([^\r\n]+?):[ \t]+([^\r\n]+)$"#)
    private static let dataURI = try! NSRegularExpression(pattern:#"(?i)data:image/[a-z0-9.+-]+;base64,[a-z0-9+/=\r\n]+"#)
    private static let code = try! NSRegularExpression(pattern:#"(?ms)^[ \t]{0,3}(?:`{3,}[^\n]*\n.*?^[ \t]{0,3}`{3,}[ \t]*$|~{3,}[^\n]*\n.*?^[ \t]{0,3}~{3,}[ \t]*$)|(`+)[^`]*\1"#)
    private struct ImageMatch { let range: NSRange; let sourceRange: NSRange; let source: String; let name: String }

    static func rewritingImages(in text: String,sources: Set<String>,target: String,name: String) -> String {
        guard !sources.isEmpty, target.hasPrefix("codexio-image://"),
              target.rangeOfCharacter(from:.whitespacesAndNewlines.union(CharacterSet(charactersIn:")<>\"'"))) == nil else { return text }
        let normalized = Set(sources.filter {!$0.isEmpty}.map(comparableSource))
        var result = text
        for match in imageMatches(in:text).reversed() where normalized.contains(comparableSource(match.source)) {
            let label = safeImageName(match.name.isEmpty ? name : match.name)
            if let range = Range(match.range,in:result) { result.replaceSubrange(range,with:"!["+label+"]("+target+")") }
        }
        return result
    }

    private static func imageMatches(in text: String) -> [ImageMatch] {
        let range = NSRange(text.startIndex...,in:text), string = text as NSString
        let codeRanges = code.matches(in:text,range:range).map(\.range)
        var matches: [ImageMatch] = []
        for (regex,groups) in [(markdown,[2,3]),(html,[1,2,3])] {
            for match in regex.matches(in:text,range:range).prefix(128) {
                guard !codeRanges.contains(where:{NSLocationInRange(match.range.location,$0)}) else { continue }
                var previous = match.range.location-1, escapes = 0
                while previous >= 0, string.character(at:previous) == 92 { escapes += 1; previous -= 1 }
                guard escapes%2 == 0, let group = groups.first(where:{match.range(at:$0).location != NSNotFound}) else { continue }
                matches.append(ImageMatch(range:match.range,sourceRange:match.range(at:group),source:capture(match,group,in:text),name:regex === markdown ? capture(match,1,in:text) : ""))
            }
        }
        var result: [ImageMatch] = []
        for match in matches.sorted(by:{$0.range.location == $1.range.location ? $0.range.length > $1.range.length : $0.range.location < $1.range.location}) {
            if let previous = result.last, NSIntersectionRange(previous.range,match.range).length > 0 { continue }
            result.append(match)
        }
        return result
    }

    private static func decodedSource(_ source: String) -> String {
        var value = source.trimmingCharacters(in:CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn:"<>`")))
        if value.count >= 2, (value.first == "\"" && value.last == "\"") || (value.first == "'" && value.last == "'") { value = String(value.dropFirst().dropLast()) }
        return value.replacingOccurrences(of:#"\\([\\()\[\] <>])"#,with:"$1",options:.regularExpression)
            .replacingOccurrences(of:"&amp;",with:"&").replacingOccurrences(of:"&quot;",with:"\"").replacingOccurrences(of:"&#39;",with:"'")
            .replacingOccurrences(of:"&apos;",with:"'").replacingOccurrences(of:"&lt;",with:"<").replacingOccurrences(of:"&gt;",with:">")
    }
    private static func comparableSource(_ source: String) -> String {
        let decoded = decodedSource(source), path = localPath(decoded,cwd:"")
        if !path.isEmpty { return path }
        if let url = URL(string:decoded), url.scheme != nil { return url.absoluteString }
        return ((decoded.removingPercentEncoding ?? decoded) as NSString).standardizingPath
    }
    private static func safeImageName(_ name: String) -> String {
        String(name.split(whereSeparator:{$0.isWhitespace}).joined(separator:" ").prefix(255))
            .replacingOccurrences(of:"\\",with:"\\\\").replacingOccurrences(of:"[",with:"\\[").replacingOccurrences(of:"]",with:"\\]")
    }

    static func content(_ raw: Any?,metadata: Object = [:]) -> [Object] {
        var parts: [Object]
        if let text = raw as? String { parts = [["type":"text","text":text]] }
        else if let values = raw as? [Any] { parts = values.prefix(256).compactMap { ($0 as? Object) ?? ($0 as? String).map {["type":"text","text":$0]} } }
        else if let value = raw as? Object { parts = [value] }
        else { parts = [] }
        for key in ["images","local_images"] {
            for image in (metadata[key] as? [Any] ?? []).prefix(limit) {
                if let source = image as? String { parts.append(["type":"image","image_url":source]) }
                else if var image = image as? Object { if image.string("type").isEmpty { image["type"] = "image" }; parts.append(image) }
            }
        }
        return Array(parts.prefix(256))
    }
    static func isImagePart(_ part: Object) -> Bool { imageTypes.contains(part.string("type").lowercased()) }
    static func preview(_ text: String) -> String {
        dataURI.stringByReplacingMatches(in:text,range:NSRange(text.startIndex...,in:text),withTemplate:"[Image]")
    }
    static func userFiles(_ text: String) -> [Object] {
        var files: [Object] = []
        if let start = text.range(of:"# Files mentioned by the user:") {
            var section = String(text[start.upperBound...])
            if let end = section.range(of:"## My request") { section = String(section[..<end.lowerBound]) }
            for match in wrapper.matches(in:section,range:NSRange(section.startIndex...,in:section)).prefix(limit) {
                files.append(["type":"file","name":capture(match,1,in:section),"path":capture(match,2,in:section)])
            }
        }
        for match in xml.matches(in:text,range:NSRange(text.startIndex...,in:text)).prefix(limit) {
            let tag = capture(match,0,in:text)
            var source = ""
            if let attribute = attributes.firstMatch(in:tag,range:NSRange(tag.startIndex...,in:tag)) {
                source = (1...3).map {capture(attribute,$0,in:tag)}.first(where:{!$0.isEmpty}) ?? ""
            } else if let open = tag.firstIndex(of:">"), let end = tag.range(of:"</image",options:.caseInsensitive) {
                source = String(tag[tag.index(after:open)..<end.lowerBound]).trimmingCharacters(in:.whitespacesAndNewlines)
                source = source.replacingOccurrences(of:#"(?is)^<(?:path|url)>(.*?)</(?:path|url)>$"#,with:"$1",options:.regularExpression)
            }
            if !source.isEmpty { files.append(["type":"image","path":source]) }
        }
        return Array(files.prefix(limit))
    }
    static func extract(text: inout String,parts: [Object],extra: [Object],placement: String,cwd: String,store: RequestMediaStore?) -> (attachments: [Object],complete: Bool) {
        var attachments: [Object] = [], complete = true, replacements: [(NSRange,String)] = []
        func add(_ source: String,name: String = "",mime: String = "",image: Bool,reference: String? = nil) -> Object? {
            guard attachments.count < 128 else { complete = false; return nil }
            var source = decodedSource(source)
            var ref = reference
            if source.lowercased().hasPrefix("data:image/") {
                if let file = store?.materialize(source) { source = file.absoluteString }
                else { source = "codexio-image-unavailable://"+identity(source); complete = false }
                if reference != nil { ref = source }
            }
            let decoded = source
            let path = localPath(decoded,cwd:cwd)
            let normalized = path.isEmpty ? decoded : path
            let sourceKey = normalized.isEmpty ? identity(["unavailable",placement,attachments.count]) : identity(normalized)
            let fileName = name.isEmpty ? (!path.isEmpty ? URL(fileURLWithPath:path).lastPathComponent : URL(string:decoded)?.lastPathComponent ?? "") : name
            let inferred = mimeType(path.isEmpty ? fileName : path)
            var value: Object = ["id":identity([placement,sourceKey]),"name":String((fileName.isEmpty ? L("图片", "Image") : fileName).prefix(255)),"path":path,"mime":mime.hasPrefix("image/") ? mime : inferred == "application/octet-stream" && image ? "image/unknown" : inferred,"placement":placement,"source_key":sourceKey]
            if let ref, !ref.isEmpty, ref.utf8.count <= 4096 { value["reference"] = ref; value["references"] = [ref] }
            attachments.append(value)
            return value
        }
        // Text references come first so deduplication keeps the inline target
        // when the same source is also present in a wrapper or typed content.
        for match in imageMatches(in:text) {
            if let added = add(match.source,name:match.name,image:true,reference:match.source), added.string("reference") != match.source, !added.string("reference").isEmpty {
                replacements.append((match.sourceRange,added.string("reference")))
            }
        }
        for (range,value) in replacements.sorted(by:{$0.0.location > $1.0.location}) {
            if let range = Range(range,in:text) { text.replaceSubrange(range,with:value) }
        }
        for part in (parts+extra).prefix(256) where isImagePart(part) || ["file","input_file"].contains(part.string("type")) {
            let origin = part.object("source").isEmpty ? part.object("image") : part.object("source")
            let imageURL = part["image_url"] as? String ?? part.object("image_url").string("url",part.string("image"))
            var source = part.string("path",part.string("file_path",part.string("url",part.string("uri",imageURL.isEmpty ? origin.string("url") : imageURL))))
            let mime = part.string("mime",part.string("mime_type",part.string("mimeType",origin.string("media_type",origin.string("mimeType"))))).lowercased()
            let bytes = part.string("data",part.string("b64_json",part.string("blob",origin.string("data"))))
            if source.isEmpty, !bytes.isEmpty, isImagePart(part), mime.hasPrefix("image/") { source = "data:"+mime+";base64,"+bytes }
            _ = add(source,name:part.string("filename",part.string("name")),mime:mime,image:isImagePart(part),reference:isImagePart(part) ? source : nil)
        }
        let unique = deduplicated(attachments)
        return (Array(unique.prefix(limit)),complete && unique.count <= limit)
    }
    static func deduplicated(_ attachments: [Object]) -> [Object] {
        var result: [Object] = [], indices: [String:Int] = [:]
        for var value in attachments.prefix(4096) {
            let placement = value.string("placement","user")
            value["placement"] = placement
            let key = sourceKey(value)
            if let index = indices[key] {
                let refs = (result[index]["references"] as? [String] ?? [result[index].string("reference")])+(value["references"] as? [String] ?? [value.string("reference")])
                var seen = Set<String>()
                let kept = Array(refs.filter {!$0.isEmpty && $0.utf8.count <= 4096 && seen.insert($0).inserted}.prefix(16))
                if let first = kept.first { result[index]["reference"] = first; result[index]["references"] = kept }
                if result[index].string("mime") == "application/octet-stream", value.string("mime").hasPrefix("image/") { result[index]["mime"] = value["mime"] }
                if let created = value.number("created_at"), created > 0 { result[index]["created_at"] = min(result[index].number("created_at") ?? created,created) }
            } else { indices[key] = result.count; result.append(value) }
        }
        return result
    }
    static func preservingCreationDates(_ attachments: [Object],from previous: [Object]) -> [Object] {
        var dates: [String:Double] = [:]
        for value in previous {
            if let date = value.number("created_at"), date > 0 { let key = sourceKey(value); dates[key] = min(dates[key] ?? date,date) }
        }
        return attachments.map { value in
            var value = value
            if let old = dates[sourceKey(value)] { value["created_at"] = min(value.number("created_at") ?? old,old) }
            return value
        }
    }
    static func imageTool(_ name: String) -> Bool {
        let name = name.lowercased()
        return name.contains("imagegen") || name.contains("image_gen") || name.contains("image_generation") || name == "generate_image"
    }
    static func toolParts(_ output: Any?) -> [Object] {
        var result: [Object] = [], nodes = 0
        func visit(_ value: Any?,depth: Int) {
            guard depth <= 6, nodes < 256, result.count < 128 else { return }; nodes += 1
            if let text = value as? String {
                if depth == 0, text.utf8.count <= 16_000_000, let json = try? JSONSerialization.jsonObject(with:Data(text.utf8)) { visit(json,depth:depth+1) }
                else if text.hasPrefix("data:image/") || (mimeType(text).hasPrefix("image/") && (text.hasPrefix("/") || text.hasPrefix("file://") || !text.contains(where:{$0.isWhitespace}))) { result.append(["type":"image","path":text]) }
                else { result.append(["type":"text","text":text]) }
            } else if let values = value as? [Any] { for part in values.prefix(128) { visit(part,depth:depth+1) } }
            else if var part = value as? Object {
                if isImagePart(part) { result.append(part); return }
                if ["text","input_text","output_text"].contains(part.string("type")) { result.append(part); return }
                if part.string("type") == "image_generation_call", let encoded = part["result"] as? String, !encoded.isEmpty {
                    let format = part.string("output_format","png").lowercased()
                    if encoded.hasPrefix("data:image/") { result.append(["type":"image","image_url":encoded]) }
                    else { result.append(["type":"image","data":encoded,"mime":format == "jpeg" || format == "jpg" ? "image/jpeg" : "image/"+format]) }
                    return
                }
                if part["image_url"] != nil || part["b64_json"] != nil || part.string("mimeType",part.string("mime_type")).hasPrefix("image/") || mimeType(part.string("path",part.string("file_path"))).hasPrefix("image/") {
                    part["type"] = "image"; if part["b64_json"] != nil && part["mime"] == nil { part["mime"] = "image/png" }; result.append(part)
                }
                for key in ["content","output","images","artifacts","result","resource"] where part[key] != nil { visit(part[key],depth:depth+1) }
            }
        }
        visit(output,depth:0)
        return result
    }
    private static func localPath(_ source: String,cwd: String) -> String {
        guard !source.isEmpty, source.utf8.count <= 4096, !source.contains("\u{0}") else { return "" }
        if let url = URL(string:source), let scheme = url.scheme {
            guard scheme.lowercased() == "file", url.host == nil || url.host == "" || url.host == "localhost" else { return "" }
            return url.standardizedFileURL.path
        }
        let path = (source.removingPercentEncoding ?? source) as NSString
        if path.isAbsolutePath || source.hasPrefix("~/") { return URL(fileURLWithPath:path.expandingTildeInPath).standardizedFileURL.path }
        guard (cwd as NSString).isAbsolutePath else { return "" }
        return URL(fileURLWithPath:cwd,isDirectory:true).appendingPathComponent(path as String).standardizedFileURL.path
    }
    private static func mimeType(_ source: String) -> String {
        let ext = (URL(string:source)?.pathExtension ?? (source as NSString).pathExtension).lowercased()
        if ["jpg","jpeg"].contains(ext) { return "image/jpeg" }
        if ext == "tif" { return "image/tiff" }
        return extensions.first(where:{$0.value == ext})?.key ?? "application/octet-stream"
    }
    private static func sourceKey(_ value: Object) -> String {
        let path = value.string("path")
        let source = value.string("source_key",path.isEmpty ? value.string("reference",value.string("id")) : URL(fileURLWithPath:path).standardizedFileURL.path)
        return identity([value.string("placement","user"),source])
    }
    private static func capture(_ match: NSTextCheckingResult,_ group: Int,in text: String) -> String {
        Range(match.range(at:group),in:text).map {String(text[$0])} ?? ""
    }
}
