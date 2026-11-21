import Foundation

/// Desktop App SQLite telemetry, queried only for log rows and request details.
/// The transcript supplies the ledger identity. SQLite only supplies timings.
final class AntigravityNativeTiming {
    private struct Signature: Equatable {
        let main: FileStamp
        let wal: FileStamp
        let journal: FileStamp
        init(_ url: URL) {
            main = FileStamp(url)
            wal = FileStamp(URL(fileURLWithPath: url.path + "-wal"))
            journal = FileStamp(URL(fileURLWithPath: url.path + "-journal"))
        }
    }

    private struct Counts: Equatable {
        var input: Int
        var cached: Int
        var output: Int
        init?(record: Object) {
            guard let input = record.integer("input_tokens"), let cached = record.integer("cache_read_tokens"),
                  let output = record.integer("output_tokens") else { return nil }
            self.init(input: input, cached: cached, output: output)
        }
        init(input: Int, cached: Int, output: Int) {
            self.input = input; self.cached = cached; self.output = output
        }
    }

    private struct Generation {
        var steps: [Int]
        var counts: Counts
        var ttft: Double?
        var streaming: Double?
    }
    private struct Candidate {
        let generation: Generation
        let ids: [String]
    }
    private struct Load {
        var calls: [String: [Candidate]] = [:]
        var coverage: [String: Counts]? // nil covers the entire session
        var weight = 0

        func covers(_ wanted: [String: Counts]) -> Bool {
            guard let coverage else { return true }
            return wanted.allSatisfy { coverage[$0.key] == $0.value }
        }
    }
    private struct Entry {
        let stamp: Signature
        let load: Load
        let retryAfter: TimeInterval?
    }

    private let sources = ReadOnlySource()
    private let folder: URL
    private var cache: [URL: Entry] = [:]
    private var order: [URL] = []
    private let timestamps: ISO8601DateFormatter = {
        let value = ISO8601DateFormatter()
        value.formatOptions = [.withInternetDateTime]
        value.timeZone = TimeZone(secondsFromGMT: 0)
        return value
    }()

    init(root: URL) { folder = root.appendingPathComponent("conversations", isDirectory: true) }

    func apply(to rows: inout [Object], ledger: Database, cancelled: () -> Bool) {
        let groups = Dictionary(grouping: rows.indices.filter { index in
            let row = rows[index]
            return row.string("record_kind") != "user_request" &&
                (row.number("duration_ms") == nil || recordedTTFTMilliseconds(row) == nil)
        }, by: { rows[$0].string("session_id") })
        // Prepare and apply each session before moving on so cache eviction
        // never loses results for the currently requested page.
        for (session, positions) in groups {
            guard let uuid = UUID(uuidString: session), !cancelled() else { continue }
            var wanted: [String: Counts] = [:]
            for index in positions { wanted[rows[index].string("id")] = Counts(record: rows[index].object("antigravity_counters")) }
            guard !wanted.isEmpty else { continue }
            let url = folder.appendingPathComponent(uuid.uuidString.lowercased() + ".db")
            guard let timing = sessionTiming(at: url, session: session, wanted: wanted, cancelled: cancelled) else { continue }
            for index in positions {
                let id = rows[index].string("id")
                guard let counts = wanted[id] else { continue }
                apply(to: &rows[index], candidates: timing.calls[id] ?? [], counts: counts, ledger: ledger)
            }
        }
    }

    private func sessionTiming(at url: URL, session: String, wanted: [String: Counts], cancelled: () -> Bool) -> Load? {
        let stamp = Signature(url), now = ProcessInfo.processInfo.systemUptime
        if let cached = cache[url], cached.stamp == stamp, (cached.retryAfter ?? .infinity) > now,
           cached.retryAfter != nil || cached.load.covers(wanted) {
            order.removeAll { $0 == url }; order.append(url)
            return cached.load
        }
        let loaded = stamp.main.exists && url.resolvingSymlinksInPath().path == url.path
            ? (try? load(url, session: session, stamp: stamp, wanted: wanted, cancelled: cancelled)) : Load()
        guard !cancelled() else { return nil }
        cache[url] = nil; order.removeAll { $0 == url }
        let result = loaded ?? Load()
        var total = cache.values.reduce(result.weight) { $0 + $1.load.weight }
        while !order.isEmpty && (order.count >= 64 || total > 16_384) {
            total -= cache.removeValue(forKey: order.removeFirst())?.load.weight ?? 0
        }
        // A temporarily busy session may be retried on a later view query,
        // but never once for every row in the same page.
        cache[url] = Entry(stamp: stamp, load: result, retryAfter: loaded == nil ? now + 15 : nil)
        order.append(url)
        return result
    }

    private func apply(to row: inout Object, candidates: [Candidate], counts: Counts, ledger: Database) {
        let matches = candidates.filter { $0.generation.counts == counts }
        guard matches.count == 1, let selected = matches.first else { return }
        if selected.ids.count > 1 {
            let parameters = Array(repeating: "?", count: selected.ids.count).joined(separator: ",")
            guard let other = try? ledger.query("SELECT data FROM antigravity_records WHERE kind='model_call' AND id<>? AND id IN (\(parameters))", [row.string("id")] + selected.ids),
                  !other.contains(where: { Counts(record: jsonObject(Data($0.string("data").utf8)).object("antigravity_counters")) == counts }) else { return }
        }
        let generation = selected.generation
        if let ttft = generation.ttft, recordedTTFTMilliseconds(row) == nil { row["ttft_ms"] = ttft * 1000 }
        if row.number("duration_ms") == nil, let ttft = generation.ttft, let stream = generation.streaming,
           (ttft + stream).isFinite, ttft + stream > 0 {
            row["duration_ms"] = (ttft + stream) * 1000
            row["duration_source"] = "antigravity_generation_metadata"
        }
    }

    private func load(_ url: URL, session: String, stamp: Signature, wanted: [String: Counts], cancelled: () -> Bool) throws -> Load {
        func read(_ database: Database) throws -> Load {
            try database.readTransaction {
                var result = Load(), after: Int?
                while !cancelled() {
                    let predicate = after == nil ? "" : " AND rowid>?"
                    let rows = try database.query("SELECT rowid AS row_id FROM gen_metadata WHERE data IS NOT NULL\(predicate) ORDER BY rowid LIMIT 128", after.map { [$0] } ?? [])
                    guard !rows.isEmpty else { break }
                    var generations: [Generation] = [], indices = Set<Int>()
                    for row in rows {
                        if cancelled() { return Load() }
                        guard let id = row["row_id"] as? Int else { throw AppFailure("Invalid Antigravity generation identity") }
                        after = id
                        if let generation = try database.readBlob(table: "gen_metadata", column: "data", rowID: id, { try Self.generation($0) }) {
                            generations.append(generation)
                            indices.formUnion(generation.steps)
                        }
                    }
                    let ledgerIDs = try stepIDs(Array(indices), in: database, session: session, cancelled: cancelled)
                    for generation in generations {
                        let ids = Array(Set(generation.steps.compactMap { ledgerIDs[$0] })).sorted()
                        let candidate = Candidate(generation: generation, ids: ids)
                        // The cache limit controls retained memory, not which
                        // part of a conversation can yield timing evidence.
                        if result.coverage == nil && result.weight + ids.count > 8192 {
                            result.coverage = wanted
                            let relevant = wanted.map { id, counts in
                                (id, Array((result.calls[id] ?? []).filter { $0.generation.counts == counts }.prefix(2)))
                            }
                            result.calls = Dictionary(uniqueKeysWithValues: relevant)
                            result.weight = result.calls.values.reduce(0) { $0 + $1.count }
                        }
                        for id in ids {
                            if result.coverage != nil && (wanted[id] != generation.counts || (result.calls[id]?.count ?? 0) >= 2) { continue }
                            result.calls[id, default: []].append(candidate)
                            result.weight += 1
                        }
                    }
                }
                return result
            }
        }
        do { return try read(Database(url, readOnly: true, busyTimeout: 50)) }
        catch {
            // Reuse the standalone snapshot fallback when macOS SQLite rejects
            // a checkpointed WAL-mode database whose sidecars are now absent.
            guard !stamp.wal.exists, !stamp.journal.exists, !cancelled() else { throw error }
            return try sources.read(url, read)
        }
    }

    private func stepIDs(_ indices: [Int], in database: Database, session: String, cancelled: () -> Bool) throws -> [Int: String] {
        var result: [Int: String] = [:]
        for start in stride(from: 0, to: indices.count, by: 400) {
            if cancelled() { return [:] }
            let batch = Array(indices[start..<min(indices.count, start + 400)])
            let parameters = Array(repeating: "?", count: batch.count).joined(separator: ",")
            let rows = try database.query("SELECT rowid AS row_id,idx FROM steps WHERE idx IN (\(parameters)) AND metadata IS NOT NULL", batch)
            for row in rows {
                guard let index = row.integer("idx"), let id = row["row_id"] as? Int,
                      let second = try database.readBlob(table: "steps", column: "metadata", rowID: id, { try Self.stepSecond($0) }) else { continue }
                // The App transcript emits this timestamp at whole-second UTC
                // precision. Match the exact persisted ID, never a nearby step.
                let createdAt = timestamps.string(from: Date(timeIntervalSince1970: Double(second)))
                result[index] = "antigravity:" + identity([session, "PLANNER_RESPONSE", createdAt, index] as [Any])
            }
        }
        return result
    }

    private static func generation(_ blob: Database.ReadBlob) throws -> Generation? {
        let wire = BlobWire(blob)
        // CortexStepGeneratorMetadata.chat(1), step_indices(2).
        let root = try wire.fields(in: 0..<blob.count, keeping: [1, 2])
        guard let chat = root.slice(1) else { return nil }
        let steps = try wire.indices(root[2] ?? [])
        guard !steps.isEmpty else { return nil }
        let fields = try wire.fields(in: chat, keeping: [4, 11, 12])
        guard let usage = fields.slice(4) else { return nil }
        let counters = try wire.fields(in: usage, keeping: [2, 3, 5])
        guard let input = Int(exactly: counters.number(2)), let output = Int(exactly: counters.number(3)),
              let cached = Int(exactly: counters.number(5)) else { return nil }
        return Generation(steps: steps, counts: Counts(input: input, cached: cached, output: output),
                          ttft: try wire.duration(fields.slice(11)), streaming: try wire.duration(fields.slice(12)))
    }

    private static func stepSecond(_ blob: Database.ReadBlob) throws -> UInt64? {
        let wire = BlobWire(blob)
        let metadata = try wire.fields(in: 0..<blob.count, keeping: [1])
        guard let stamp = metadata.slice(1) else { return nil }
        let timestamp = try wire.fields(in: stamp, keeping: [1, 2])
        let seconds = timestamp.number(1), nanos = timestamp.number(2)
        return (1...253_402_300_799).contains(seconds) && nanos < 1_000_000_000 ? seconds : nil
    }
}

/// Read protobuf headers a block at a time; move over unused messages/strings
/// by their encoded length without loading the prompt or response bodies.
private final class BlobWire {
    private struct Invalid: Error {}
    private let blob: Database.ReadBlob
    private var buffer = Data()
    private var bufferStart = 0
    init(_ blob: Database.ReadBlob) { self.blob = blob }

    private func varint(at position: inout Int, end: Int) throws -> UInt64 {
        var value: UInt64 = 0
        for bit in stride(from: 0, through: 63, by: 7) {
            guard position < end else { throw Invalid() }
            if !(bufferStart..<bufferStart + buffer.count).contains(position) {
                bufferStart = position
                buffer = try blob.bytes(at: position, count: min(4096, blob.count - position))
            }
            let byte = buffer[position - bufferStart]
            position += 1
            if bit == 63 && byte > 1 { throw Invalid() }
            value |= UInt64(byte & 127) << bit
            if byte & 128 == 0 { return value }
        }
        throw Invalid()
    }

    func fields(in range: Range<Int>, keeping: Set<UInt64>) throws -> [UInt64: [WireValue]] {
        var position = range.lowerBound
        var output: [UInt64: [WireValue]] = [:]
        while position < range.upperBound {
            let tag = try varint(at: &position, end: range.upperBound), field = tag >> 3
            guard field > 0 else { throw Invalid() }
            switch tag & 7 {
            case 0:
                let value = try varint(at: &position, end: range.upperBound)
                if keeping.contains(field) { output[field, default: []].append(.number(value)) }
            case 2:
                let length = try varint(at: &position, end: range.upperBound)
                guard length <= UInt64(range.upperBound - position) else { throw Invalid() }
                let end = position + Int(length)
                if keeping.contains(field) { output[field, default: []].append(.slice(position..<end)) }
                position = end
            case 1, 5:
                let size = (tag & 7) == 1 ? 8 : 4
                guard size <= range.upperBound - position else { throw Invalid() }
                position += size
            default: throw Invalid()
            }
            if let values = output[field], values.count > 128 { throw Invalid() }
        }
        return output
    }

    func indices(_ items: [WireValue]) throws -> [Int] {
        var result = Set<Int>()
        func add(_ value: UInt64) throws {
            guard value <= UInt64(UInt32.max), result.count < 128 else { throw Invalid() }
            result.insert(Int(value))
        }
        for item in items {
            switch item {
            case .number(let value): try add(value)
            case .slice(let range):
                var position = range.lowerBound
                while position < range.upperBound { try add(varint(at: &position, end: range.upperBound)) }
            }
        }
        return result.sorted()
    }

    func duration(_ range: Range<Int>?) throws -> Double? {
        guard let range else { return nil }
        let duration = try fields(in: range, keeping: [1, 2])
        let seconds = duration.number(1), nanos = duration.number(2)
        guard seconds <= 315_576_000_000, nanos < 1_000_000_000 else { return nil }
        return Double(seconds) + Double(nanos) / 1_000_000_000
    }
}

private enum WireValue { case number(UInt64), slice(Range<Int>) }
private extension Dictionary where Key == UInt64, Value == [WireValue] {
    func slice(_ key: UInt64) -> Range<Int>? {
        guard let values = self[key], values.count == 1, case .slice(let range) = values[0] else { return nil }
        return range
    }
    func number(_ key: UInt64) -> UInt64 {
        guard let values = self[key] else { return 0 }
        guard values.count == 1, case .number(let value) = values[0] else { return UInt64.max }
        return value
    }
}
