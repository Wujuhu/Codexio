import AppKit
import CryptoKit
import ImageIO
import UniformTypeIdentifiers

// Owned by MobileSync.queue. Reuse RequestMediaStore's owned-file, atomic-write
// and bounded-cache policy; keep transport bytes on disk, not in UI snapshots.
final class MobileImageCache {
    struct Prepared { var user: String; var final: String; var references: [MobileImageReference] }
    struct Stored: Equatable {
        let id: String
        let digest: String
        let bytes: Int
        let expires: Double
        var fileName: String { id+"."+digest+"."+String(Int64(expires))+".json" }
    }
    private struct Cached { let stamp: String; let content: String?; let image: MobileImageReference }
    private let queue: DispatchQueue
    private let directory: URL
    private var loaded = false
    private var cached: [String:Cached] = [:]
    private var stored: [String:Stored] = [:]
    private var verified: [String:FileStamp] = [:]
    // Only this session's prepared references are upload candidates. Other
    // unexpired disk entries can still answer an authenticated LAN read.
    var uploadRecords: [Stored] { stored.values.filter { owners[$0.id] != nil } }
    func contains(_ id: String) -> Bool { stored[id] != nil }
    private var owners: [String:(request: String,expires: Double)] = [:]
    private var pinned: String?
    private var access: [String:Double] = [:]
    private var remote: [String:Data] = [:]
    private var localSources: [String:URL] = [:]
    private var remoteSources: [String:URL] = [:]
    private var sourceRequests: [String:String] = [:]
    private var pending = Set<String>()
    private var retry: [String:Date] = [:]
    private var generation = UUID()
    private lazy var downloader = MobileImageDownloader()
    var onReady: ((String) -> Void)?
    init(queue: DispatchQueue,directory: URL) { self.queue = queue; self.directory = directory.standardizedFileURL }
    func stop() { generation = UUID(); downloader.cancel(); pending.removeAll(); cached.removeAll(); stored.removeAll(); verified.removeAll(); loaded = false; owners.removeAll(); access.removeAll(); remote.removeAll(); localSources.removeAll(); remoteSources.removeAll(); sourceRequests.removeAll(); retry.removeAll() }
    private func loadFiles() {
        guard !loaded else { return }
        let manager = FileManager.default
        do {
            try manager.createDirectory(at:directory,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
            guard directory.resolvingSymlinksInPath() == directory else { return }
            let files = try manager.contentsOfDirectory(at:directory,includingPropertiesForKeys:[.isRegularFileKey,.isSymbolicLinkKey,.fileSizeKey,.contentModificationDateKey])
            loaded = true
            for file in files {
                let parts = file.lastPathComponent.split(separator:".")
                guard parts.count == 4, parts[3] == "json", [parts[0],parts[1]].allSatisfy({$0.count == 64 && $0.allSatisfy({"0123456789abcdef".contains($0)})}),
                      let deadline = Int64(parts[2]), deadline > 0, Double(deadline) < Double(Int64.max),
                      let info = try? file.resourceValues(forKeys:[.isRegularFileKey,.isSymbolicLinkKey,.fileSizeKey,.contentModificationDateKey]),
                      info.isRegularFile == true, info.isSymbolicLink != true else { continue }
                let bytes = info.fileSize ?? 0, id = String(parts[0])
                guard Double(deadline) > Date().timeIntervalSince1970, bytes > 0, bytes <= MobileProtocol.imageEnvelopeLimit, stored[id] == nil else { try? manager.removeItem(at:file); continue }
                stored[id] = Stored(id:id,digest:String(parts[1]),bytes:bytes,expires:Double(deadline))
                access[id] = info.contentModificationDate?.timeIntervalSince1970 ?? 0
            }
        } catch { }
    }
    private func file(_ value: Stored) -> URL { directory.appendingPathComponent(value.fileName) }
    private func available(_ value: Stored) -> Bool {
        guard value.expires > Date().timeIntervalSince1970, directory.resolvingSymlinksInPath() == directory,
              let info = try? file(value).resourceValues(forKeys:[.isRegularFileKey,.isSymbolicLinkKey,.fileSizeKey]) else { return false }
        return info.isRegularFile == true && info.isSymbolicLink != true && info.fileSize == value.bytes
    }
    private func discard(_ id: String) {
        if let value = stored.removeValue(forKey:id), directory.resolvingSymlinksInPath() == directory { try? FileManager.default.removeItem(at:file(value)) }
        verified.removeValue(forKey:id)
        access.removeValue(forKey:id)
        cached = cached.filter {$0.value.image.id != id}
    }
    private func save(_ data: Data,id: String,expires: Double) -> Stored? {
        guard loaded, expires.isFinite, expires > 0, expires < Double(Int64.max), !data.isEmpty, data.count <= MobileProtocol.imageEnvelopeLimit, directory.resolvingSymlinksInPath() == directory else { return nil }
        if let old = stored[id], available(old) { return old }
        let value = Stored(id:id,digest:MobileProtocol.hash(data),bytes:data.count,expires:expires)
        do {
            let target = file(value)
            guard target.resolvingSymlinksInPath() == target else { return nil }
            try data.write(to:target,options:.atomic)
            try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:target.path)
            stored[id] = value; access[id] = Date().timeIntervalSince1970
            verified[id] = FileStamp(target)
            return value
        } catch { return nil }
    }
    private func validPayload(_ value: Stored) -> Bool {
        guard available(value) else { return false }
        let target = file(value), stamp = FileStamp(file(value))
        if verified[value.id] == stamp { return true }
        // Reopened or changed files are checked once, using bounded buffers.
        // Remember file identity so each LAN chunk doesn't hash the full image.
        do {
            let reader = try FileHandle(forReadingFrom:target); defer {try? reader.close()}
            var hasher = SHA256(), count = 0
            while let chunk = try autoreleasepool(invoking:{try reader.read(upToCount:MobileProtocol.imageChunkBytes)}), !chunk.isEmpty {
                count += chunk.count; guard count <= value.bytes else { return false }; hasher.update(data:chunk)
            }
            guard count == value.bytes, hasher.finalize().map({String(format:"%02x",$0)}).joined() == value.digest, FileStamp(target) == stamp else { return false }
            verified[value.id] = stamp; return true
        } catch { return false }
    }
    // The upload task captures only Stored metadata; load one body immediately
    // before sending it, so acknowledgements never retain the whole outbox.
    func envelope(_ value: Stored) -> MobileEnvelope? {
        guard stored[value.id] == value else { return nil }
        guard available(value) else { retryFile(value.id); return nil }
        return autoreleasepool {
            guard let data = try? Data(contentsOf:file(value)), data.count == value.bytes, MobileProtocol.hash(data) == value.digest else {
                retryFile(value.id); return nil
            }
            access[value.id] = Date().timeIntervalSince1970
            return MobileEnvelope(dataset:"image-"+value.id,revision:1,digest:value.digest,payload:String(decoding:data,as:UTF8.self))
        }
    }
    private func retryFile(_ id: String) {
        let affected = cached.filter {$0.value.image.id == id}
        discard(id)
        for (key,value) in affected {
            var image = value.image; image.availability = "unavailable"
            cached[key] = Cached(stamp:value.stamp,content:value.content,image:image)
            retry[key] = Date().addingTimeInterval(60)
        }
    }
    func needsRefresh(_ request: String) -> Bool {
        for (key,entry) in cached where sourceRequests[key] == request && entry.image.expires > Date().timeIntervalSince1970 {
            if let file=localSources[key] {
                let stamp=FileStamp(file)
                if !entry.stamp.hasPrefix("\(file.path)|\(stamp.size)|\(stamp.modified?.timeIntervalSince1970 ?? 0)|\(stamp.inode)|") { return true }
                if entry.image.availability != "available", let next = retry[key], Date() >= next { return true }
            } else if remoteSources[key] != nil, entry.image.availability != "available", !pending.contains(key), pending.count < 2, Date() >= (retry[key] ?? .distantPast) { return true }
        }
        return false
    }
    func refreshPending() {
        let requests = Set(sourceRequests.keys.compactMap { key -> String? in
            guard let entry=cached[key],entry.image.expires > Date().timeIntervalSince1970,entry.image.availability != "available",
                  !pending.contains(key),Date() >= (retry[key] ?? .distantPast), remoteSources[key] != nil || retry[key] != nil else { return nil }
            return sourceRequests[key]
        })
        for request in requests.prefix(max(0,2-pending.count)) { onReady?(request) }
    }
    func prune(now: Double = Date().timeIntervalSince1970) {
        loadFiles()
        for (id,value) in stored where value.expires <= now { discard(id) }
        while stored.count > 128 || stored.values.reduce(0,{$0+$1.bytes}) > 32*1_024*1_024 {
            guard let id = access.filter({$0.key != pinned}).min(by:{$0.value < $1.value})?.key else { break }
            discard(id)
        }
        owners = owners.filter {$0.value.expires > now}
        if owners.count > 2048 { owners = Dictionary(uniqueKeysWithValues:owners.sorted {$0.value.expires > $1.value.expires}.prefix(2048).map {($0.key,$0.value)}) }
        cached = cached.filter {$0.value.image.expires > now}
        if cached.count > 512 { cached = Dictionary(uniqueKeysWithValues:cached.sorted {$0.value.image.expires > $1.value.image.expires}.prefix(512).map {($0.key,$0.value)}) }
        localSources = localSources.filter {cached[$0.key] != nil}; remoteSources = remoteSources.filter {cached[$0.key] != nil}; sourceRequests = sourceRequests.filter {cached[$0.key] != nil}
        retry = retry.filter {cached[$0.key] != nil || pending.contains($0.key)}
        if remote.values.reduce(0,{$0+$1.count}) > 16*1_024*1_024 { remote.removeAll() }
    }
    func prepare(_ raw: Object,requestID: String,started: Double,completed: Double?) -> Prepared {
        prune()
        var result = Prepared(user:raw.string("user"),final:raw.string("final"),references:[])
        var used = Set<String>()
        var canonical: [String:Cached] = [:]
        func retain(_ candidate: Cached,key: String) -> Cached {
            let content = candidate.content ?? candidate.image.id
            let chosen = canonical[content] ?? candidate
            canonical[content] = chosen
            let entry = Cached(stamp:candidate.stamp,content:candidate.content,image:chosen.image)
            cached[key] = entry
            if stored[entry.image.id] != nil {
                let id=entry.image.id
                access[id]=Date().timeIntervalSince1970
                owners[id]=(requestID,entry.image.expires)
            }
            return entry
        }
        // Equal bytes within one message share the earliest original lifetime.
        // Including that lifetime in the ID also prevents a later source from
        // overwriting an immutable cloud image after a process restart.
        let attachments = raw.objects("attachments").prefix(32).sorted {
            let lhs=$0.number("created_at") ?? started,rhs=$1.number("created_at") ?? started
            return lhs == rhs ? $0.string("id") < $1.string("id") : lhs < rhs
        }
        for item in attachments where item.string("mime").hasPrefix("image/") {
            let placement = item.string("placement","user") == "final" ? "final" : "user"
            let path = item.string("path"), reference = item.string("reference")
            let source = path.isEmpty && ["https","http"].contains(URL(string:reference)?.scheme?.lowercased() ?? "") ? reference : path
            let name = MobileProtocol.prefix(item.string("name","图片"),bytes:240)
            let created = floor(item.number("created_at") ?? started)
            let expires = created+MobileProtocol.imageRetention
            let key = requestID+"|"+placement+"|"+(source.isEmpty ? item.string("id",name) : source)
            guard used.insert(key).inserted else { continue }
            sourceRequests[key] = requestID
            var image = MobileImageReference(id:MobileProtocol.hash(Data(key.utf8)),name:name,mime:item.string("mime","image/png"),placement:placement,width:0,height:0,expires:expires,availability:expires <= Date().timeIntervalSince1970 ? "expired" : "unavailable")
            if expires > Date().timeIntervalSince1970, !source.isEmpty {
                let url = source.hasPrefix("/") ? URL(fileURLWithPath:source) : URL(string:source)
                if let url {
                    let stamp: String
                    var bytes: Data?
                    if url.isFileURL {
                        let file = url.resolvingSymlinksInPath(), value = FileStamp(file)
                        localSources[key] = file
                        stamp = "\(file.path)|\(value.size)|\(value.modified?.timeIntervalSince1970 ?? 0)|\(value.inode)|\(expires)"
                        if let old = cached[key], old.stamp == stamp,
                           (old.image.availability == "available" && stored[old.image.id].map(available) == true) || (old.image.availability != "available" && Date() < (retry[key] ?? .distantFuture)) {
                            image = retain(old,key:key).image
                            append(image,item:item,to:&result); continue
                        }
                        if let attrs=try? FileManager.default.attributesOfItem(atPath:file.path),attrs[.type] as? FileAttributeType == .typeRegular,value.size > 0,value.size <= 12*1_024*1_024 { bytes=try? Data(contentsOf:file,options:.mappedIfSafe) }
                    } else {
                        stamp = source+"|\(expires)"
                        if MobileImageDownloader.allowed(url) { remoteSources[key] = url }
                        if let old=cached[key],old.stamp==stamp,old.image.availability=="available",stored[old.image.id].map(available) == true {
                            image=retain(old,key:key).image
                            append(image,item:item,to:&result);continue
                        }
                        bytes=remote[source]
                        if bytes==nil,!pending.contains(key),Date() >= (retry[key] ?? .distantPast),pending.count < 2,MobileImageDownloader.allowed(url) {
                            pending.insert(key);let token=generation
                            downloader.fetch(url) { [weak self] data in
                                self?.queue.async {
                                    guard let self,self.generation==token else{return}
                                    self.pending.remove(key);self.retry[key]=Date().addingTimeInterval(data == nil ? 60 : 3600)
                                    if let data {self.remote[source]=data;self.cached.removeValue(forKey:key)}
                                    self.onReady?(requestID)
                                }
                            }
                        }
                    }
                    var content: String?
                    if let bytes,let encoded=autoreleasepool(invoking:{Self.raster(bytes)}) {
                        let identity=requestID+"|"+placement+"|"+MobileProtocol.hash(encoded.data)
                        content=identity
                        image.id=MobileProtocol.hash(Data((identity+"|"+String(created)).utf8));image.mime=encoded.mime;image.width=encoded.width;image.height=encoded.height;image.availability="available"
                        if let previous = canonical[identity], previous.image.availability == "available", stored[previous.image.id].map(available) == true {
                            image = previous.image
                        } else if stored[image.id].map(available) != true {
                            let payload=MobileImagePayload(id:image.id,request:requestID,mime:image.mime,width:image.width,height:image.height,created:created,expires:expires,data:encoded.data.base64EncodedString())
                            if let data=try? MobileProtocol.encode(payload),save(data,id:image.id,expires:expires) != nil { retry.removeValue(forKey:key) }
                            else {image.availability="unavailable";retry[key]=Date().addingTimeInterval(60)}
                        }
                    }
                    image=retain(Cached(stamp:stamp,content:content,image:image),key:key).image
                }
            }
            append(image,item:item,to:&result)
        }
        prune();return result
    }
    private func append(_ image: MobileImageReference,item: Object,to result: inout Prepared) {
        if !result.references.contains(where:{$0.id==image.id}) { result.references.append(image) }
        let target="codexio-image://"+image.id
        var text=image.placement=="user" ? result.user : result.final
        let sources=Set([item.string("reference"),item.string("path")]+(item["references"] as? [String] ?? [])).filter{!$0.isEmpty}
        text=RequestMedia.rewritingImages(in:text,sources:Set(sources),target:target,name:image.name)
        if !text.contains(target) { text += "\n\n![" + image.name.replacingOccurrences(of:"]",with:"") + "](" + target + ")" }
        if image.placement=="user" {result.user=text} else {result.final=text}
    }
    func message(_ id: String,part: Int,prepare: ((String) -> Void)? = nil) -> MobileMessage {
        pinned = id; defer { pinned = nil }
        prune()
        if let value = stored[id], !validPayload(value) { discard(id) }
        if stored[id] == nil, let owner = owners[id], owner.expires > Date().timeIntervalSince1970 { prepare?(owner.request) }
        guard let value=stored[id],validPayload(value) else {return MobileMessage(action:"image",error:"IMAGE_UNAVAILABLE",imageID:id)}
        let count=(value.bytes+MobileProtocol.imageChunkBytes-1)/MobileProtocol.imageChunkBytes
        guard part >= 0,part < count else{return MobileMessage(action:"image",error:"INVALID",imageID:id)}
        access[id]=Date().timeIntervalSince1970
        let start=part*MobileProtocol.imageChunkBytes,length=min(MobileProtocol.imageChunkBytes,value.bytes-start)
        return autoreleasepool {
            do {
                let reader=try FileHandle(forReadingFrom:file(value));defer {try? reader.close()}
                try reader.seek(toOffset:UInt64(start))
                guard let bytes=try reader.read(upToCount:length),bytes.count==length else {throw AppFailure("Incomplete image cache file")}
                return MobileMessage(action:"image",imageID:id,imagePart:part,imageManifest:MobileDetailManifest(id:id,revision:1,digest:value.digest,bytes:value.bytes,parts:count),imageChunk:bytes.base64EncodedString())
            } catch {discard(id);return MobileMessage(action:"image",error:"IMAGE_UNAVAILABLE",imageID:id)}
        }
    }
    private static func raster(_ bytes: Data) -> (data:Data,mime:String,width:Int,height:Int)? {
        guard let source=CGImageSourceCreateWithData(bytes as CFData,[kCGImageSourceShouldCache:false] as CFDictionary) else { return vector(bytes) }
        guard let props=CGImageSourceCopyPropertiesAtIndex(source,0,nil) as? [CFString:Any],let w=props[kCGImagePropertyPixelWidth] as? NSNumber,let h=props[kCGImagePropertyPixelHeight] as? NSNumber,w.intValue>0,h.intValue>0,w.intValue<=16_384,h.intValue<=16_384,w.intValue*h.intValue<=64_000_000 else{return nil}
        for side in [2048,1536,1024] {
            guard let image=CGImageSourceCreateThumbnailAtIndex(source,0,[kCGImageSourceCreateThumbnailFromImageAlways:true,kCGImageSourceCreateThumbnailWithTransform:true,kCGImageSourceThumbnailMaxPixelSize:side,kCGImageSourceShouldCacheImmediately:true] as CFDictionary) else{return nil}
            let alpha = ![CGImageAlphaInfo.none,.noneSkipFirst,.noneSkipLast].contains(image.alphaInfo)
            let type=alpha ? UTType.png : UTType.jpeg
            for quality in alpha ? [0.82] : [0.82,0.65] {
                let data=NSMutableData()
                guard let destination=CGImageDestinationCreateWithData(data,type.identifier as CFString,1,nil) else{return nil}
                CGImageDestinationAddImage(destination,image,[kCGImageDestinationLossyCompressionQuality:quality] as CFDictionary)
                if CGImageDestinationFinalize(destination),data.length <= MobileProtocol.imageDataLimit {return(data as Data,alpha ? "image/png" : "image/jpeg",image.width,image.height)}
            }
        }
        return nil
    }
    private static func vector(_ bytes: Data) -> (data:Data,mime:String,width:Int,height:Int)? {
        guard bytes.count <= 4*1_024*1_024, let text=String(data:bytes,encoding:.utf8), text.localizedCaseInsensitiveContains("<svg"),
              text.range(of:"(?is)<!DOCTYPE|<!ENTITY|<script|(?:href|src)\\s*=\\s*[\"'](?:https?:|file:|data:)",options:.regularExpression)==nil,
              let image=NSImage(data:bytes),image.size.width>0,image.size.height>0,image.size.width.isFinite,image.size.height.isFinite else{return nil}
        let scale=min(1,2048/max(image.size.width,image.size.height)),width=max(1,Int(ceil(image.size.width*scale))),height=max(1,Int(ceil(image.size.height*scale)))
        guard let bitmap=NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:width,pixelsHigh:height,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0),let context=NSGraphicsContext(bitmapImageRep:bitmap) else{return nil}
        NSGraphicsContext.saveGraphicsState();NSGraphicsContext.current=context;context.imageInterpolation = .high
        image.draw(in:NSRect(x:0,y:0,width:width,height:height),from:.zero,operation:.copy,fraction:1)
        NSGraphicsContext.restoreGraphicsState()
        guard let data=bitmap.representation(using:.png,properties:[:]),data.count<=MobileProtocol.imageDataLimit else{return nil}
        return(data,"image/png",width,height)
    }
}

// Public image URLs use an ephemeral unauthenticated session with bounded bytes.
private final class MobileImageDownloader: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private final class Transfer { var data=Data(); let done:(Data?)->Void; init(done:@escaping(Data?)->Void) { self.done=done } }
    private let lock=NSLock()
    private var transfers:[Int:Transfer]=[:]
    private lazy var session:URLSession = {let c=URLSessionConfiguration.ephemeral;c.timeoutIntervalForRequest=12;c.timeoutIntervalForResource=20;return URLSession(configuration:c,delegate:self,delegateQueue:nil)}()
    static func allowed(_ url:URL)->Bool {
        guard ["https","http"].contains(url.scheme?.lowercased() ?? ""),url.user==nil,url.password==nil,let host=url.host?.lowercased(),!host.isEmpty else{return false}
        return host != "localhost" && !host.hasSuffix(".local") && !host.hasPrefix("127.") && !host.hasPrefix("10.") && !host.hasPrefix("192.168.") && !host.hasPrefix("169.254.") && host != "::1" && !host.hasPrefix("[::1]") && !(host.hasPrefix("172.") && host.split(separator:".").dropFirst().first.flatMap({Int($0)}).map{(16...31).contains($0)} == true)
    }
    func fetch(_ url:URL,done:@escaping(Data?)->Void) {
        guard Self.allowed(url) else{done(nil);return};let task=session.dataTask(with:url)
        lock.lock();transfers[task.taskIdentifier]=Transfer(done:done);lock.unlock();task.resume()
    }
    func cancel() {session.getAllTasks{$0.forEach{$0.cancel()}}}
    func urlSession(_ session:URLSession,task:URLSessionTask,willPerformHTTPRedirection response:HTTPURLResponse,newRequest request:URLRequest,completionHandler:@escaping(URLRequest?)->Void) {completionHandler(request.url.flatMap{Self.allowed($0) ? request : nil})}
    func urlSession(_ session:URLSession,dataTask:URLSessionDataTask,didReceive response:URLResponse,completionHandler:@escaping(URLSession.ResponseDisposition)->Void) {
        guard let http=response as? HTTPURLResponse,(200..<300).contains(http.statusCode),response.expectedContentLength<=12*1_024*1_024,(response.mimeType ?? "").hasPrefix("image/") else{completionHandler(.cancel);return};completionHandler(.allow)
    }
    func urlSession(_ session:URLSession,dataTask:URLSessionDataTask,didReceive data:Data) {
        lock.lock();defer{lock.unlock()};guard let transfer=transfers[dataTask.taskIdentifier] else{return}
        guard transfer.data.count+data.count<=12*1_024*1_024 else{dataTask.cancel();return};transfer.data.append(data)
    }
    func urlSession(_ session:URLSession,task:URLSessionTask,didCompleteWithError error:Error?) {
        lock.lock();let transfer=transfers.removeValue(forKey:task.taskIdentifier);lock.unlock()
        transfer?.done(error==nil ? transfer?.data : nil)
    }
}
