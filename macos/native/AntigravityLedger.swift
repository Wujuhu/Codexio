import Foundation

struct AntigravityModelLabel {
    let source: String
    let title: String
    let effort: String
    private static let suffix = try! NSRegularExpression(
        pattern: #"(?i)(?:[\s_-]+|\s*\()(minimal|low|medium|high|extra(?:[-_ ]high)?|xhigh|max|ultra|thinking)\)?\s*$"#)
    private static let versionedGemini = try! NSRegularExpression(
        pattern: #"(?i)^gemini[-_ ]+([0-9]+(?:\.[0-9]+)?)[-_ ]+(pro|flash(?:[-_ ]lite)?|lite|neon|carbon|argon|hydrogen)$"#)

    init(_ source: String, explicitEffort: String = "") {
        let raw = source.trimmingCharacters(in: .whitespacesAndNewlines)
        self.source = raw
        let entire = NSRange(raw.startIndex..<raw.endIndex, in: raw)
        let match = Self.suffix.firstMatch(in: raw, range: entire)
        let catalog = ModelPricing.shared
        var base = raw, suffixEffort = ""
        if let match, let suffixRange = Range(match.range, in: raw), let effortRange = Range(match.range(at: 1), in: raw) {
            let candidate = String(raw[..<suffixRange.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
            let candidateEffort = Self.normalizedEffort(String(raw[effortRange]))
            if catalog.model(candidate) != nil || (Self.geminiTitle(candidate) != nil && ["low", "medium", "high", "xhigh"].contains(candidateEffort)) {
                base = candidate
                suffixEffort = candidateEffort
            }
        }
        title = catalog.model(base).flatMap(catalog.displayName) ?? Self.geminiTitle(base) ?? modelName(raw)
        let suppliedEffort = Self.normalizedEffort(explicitEffort)
        effort = suppliedEffort.isEmpty ? Self.normalizedEffort(suffixEffort) : suppliedEffort
    }

    private static func geminiTitle(_ value: String) -> String? {
        guard let match = versionedGemini.firstMatch(in: value, range: NSRange(value.startIndex..<value.endIndex, in: value)),
              let version = Range(match.range(at: 1), in: value), let variant = Range(match.range(at: 2), in: value) else { return nil }
        let name = value[variant].split(whereSeparator: { $0 == "-" || $0 == "_" || $0 == " " }).map { $0.capitalized }.joined(separator: "-")
        return "Gemini " + value[version] + " " + name
    }

    private static func normalizedEffort(_ value: String) -> String {
        let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        switch normalized {
        case "extra", "extra high", "extra_high", "extra-high", "xhigh": return "xhigh"
        case "none", "minimal", "low", "medium", "high", "max", "ultra", "mixed": return normalized
        case "thinking": return "Thinking"
        default: return ""
        }
    }
}

/// Indexed copy of Antigravity App's local JSONL. The source is opened read-only;
/// cursor updates and archived text commit with the corresponding ledger rows.
final class AntigravityLedger {
    let root: URL
    private let database: Database
    private var files: [(session: String, url: URL)] = []
    private var discoveryStamp: FileStamp?
    private var discoveredAt = Date.distantPast
    private var scanned: [URL: FileStamp] = [:]
    private let cancellationLock = NSLock()
    private var cancelled = false
    private(set) var revision = 0
    private(set) var warning: String?
    private static let mainGeminiModels: Set<String> = ["gemini-4-neon", "gemini-4-carbon", "gemini-4-hydrogen", "gemini-3.8-flash"]

    init(paths: AppPaths) throws {
        root = paths.mock ? paths.data.appendingPathComponent("antigravity-mock")
            : FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".gemini/antigravity")
        database = try Database(paths.data.appendingPathComponent("antigravity.sqlite"))
        try database.script("""
            CREATE TABLE IF NOT EXISTS antigravity_records(
                id TEXT PRIMARY KEY, session TEXT NOT NULL, request TEXT NOT NULL, kind TEXT NOT NULL,
                timestamp REAL NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL, search TEXT NOT NULL, data TEXT NOT NULL,
                input INTEGER, cached INTEGER, output INTEGER, total INTEGER, cost REAL
            );
            CREATE INDEX IF NOT EXISTS antigravity_time ON antigravity_records(kind,timestamp DESC,id);
            CREATE INDEX IF NOT EXISTS antigravity_model_time ON antigravity_records(kind,model,timestamp DESC,id);
            CREATE INDEX IF NOT EXISTS antigravity_request ON antigravity_records(request,timestamp DESC,id);
            CREATE INDEX IF NOT EXISTS antigravity_session ON antigravity_records(session,timestamp DESC,id);
            """)
    }

    func cancel() { cancellationLock.lock(); cancelled = true; cancellationLock.unlock() }
    func resume() { cancellationLock.lock(); cancelled = false; cancellationLock.unlock() }
    func makeWeeklyEstimator() throws -> WeeklyEstimator {
        try WeeklyEstimator(database, limitID: "antigravity") { [weak self] start, end in
            self?.weeklyConsumption(start: start, end: end) ?? (0, 0, true)
        }
    }

    private func weeklyConsumption(start: Double, end: Double) -> (Int, Double, Bool) {
        // Exclude calls that have explicit API-key or paid-credit funding. The
        // rest are local App reference costs inside a verified sample interval.
        guard let rows = try? database.query("""
            SELECT model,count(*) AS calls,count(total) AS metered,count(cost) AS priced,sum(total) AS tokens,sum(cost) AS cost
            FROM antigravity_records WHERE kind='model_call' AND timestamp>? AND timestamp<=?
                AND replace(lower(trim(COALESCE(json_extract(data,'$.auth_mode'),''))),'-','_') NOT IN ('api','api_key','apikey','openai_api_key','gemini_api_key')
                AND replace(lower(trim(COALESCE(json_extract(data,'$.billing_source'),''))),'-','_') NOT IN ('credits','credit','paid_credits','purchased_credits','usage_credits','prepaid','pay_as_you_go','payg','api','api_key','apikey')
            GROUP BY model
            """, [start, end]) else { return (0, 0, true) }
        let catalog = ModelPricing.shared
        var tokens = 0, cost = 0.0
        for row in rows {
            let raw = row.string("model"), model = catalog.model(raw) ?? ""
            let lower = raw.lowercased()
            if model == "gemini-4-argon" || catalog.group(model) == "claude" || catalog.group(model) == "gpt"
                || lower.hasPrefix("claude-") || lower.hasPrefix("claude ") || lower.hasPrefix("gpt-oss") { continue }
            guard Self.mainGeminiModels.contains(model), row.integer("calls") == row.integer("metered"),
                  row.integer("calls") == row.integer("priced"), let count = row.integer("tokens"), let amount = row.number("cost") else { return (0, 0, true) }
            let sum = tokens.addingReportingOverflow(count)
            guard !sum.overflow else { return (0, 0, true) }
            tokens = sum.partialValue
            cost += amount
        }
        return cost.isFinite ? (tokens, cost, false) : (0, 0, true)
    }

    private var isCancelled: Bool {
        cancellationLock.lock(); defer { cancellationLock.unlock() }
        return cancelled
    }

    func scan(now: Date = Date()) throws {
        guard !isCancelled else { return }
        try refreshPrices()
        let brain = root.appendingPathComponent("brain", isDirectory: true)
        let stamp = FileStamp(brain)
        guard stamp.exists else {
            warning = L("未找到 Antigravity App 本机记录", "No local Antigravity App records found")
            return
        }
        warning = nil
        if discoveryStamp != stamp || now.timeIntervalSince(discoveredAt) >= 300 || now < discoveredAt {
            let directories = try FileManager.default.contentsOfDirectory(at: brain, includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey], options: [.skipsHiddenFiles])
            files = directories.compactMap { directory in
                let session = directory.lastPathComponent
                guard UUID(uuidString: session) != nil,
                      let properties = try? directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey]),
                      properties.isDirectory == true, properties.isSymbolicLink != true else { return nil }
                return (session.lowercased(), directory.appendingPathComponent(".system_generated/logs/transcript.jsonl"))
            }.sorted { $0.session < $1.session }
            let active = Set(files.flatMap { [$0.url, fullTranscript($0.url)] })
            scanned = scanned.filter { active.contains($0.key) }
            discoveryStamp = stamp
            discoveredAt = now
        }
        for file in files {
            if isCancelled { return }
            do {
                try scan(file.url, session: file.session)
                try scan(fullTranscript(file.url), session: file.session, full: true)
            }
            catch { warning = L("部分 Antigravity 记录暂不可读，已保存历史保留", "Some Antigravity records could not be read; saved history is retained") }
        }
    }

    private func fullTranscript(_ compact: URL) -> URL {
        compact.deletingLastPathComponent().appendingPathComponent("transcript_full.jsonl")
    }

    private func scan(_ file: URL, session: String, full: Bool = false) throws {
        let stamp = FileStamp(file)
        guard stamp.exists, scanned[file] != stamp,
              file.resolvingSymlinksInPath().path == file.path,
              (try? file.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true else { return }
        let key = "antigravity-app-v1:" + file.path
        let cursor = database.object("usage_cursors", key: key)
        let handle = try FileHandle(forReadingFrom: file)
        defer { try? handle.close() }
        var offset = cursor.integer("offset") ?? 0
        guard stamp.size <= UInt64(Int.max) else { return }
        let oldSize = cursor.integer("size") ?? 0
        let rewritten = oldSize == Int(stamp.size) && cursor.number("modified") != stamp.modified?.timeIntervalSince1970
        var valid = cursor.string("device") == String(stamp.device) && cursor.string("inode") == String(stamp.inode)
            && UInt64(offset) <= stamp.size && !rewritten
        if valid, offset > 0 {
            valid = try tailHash(handle, at: offset) == cursor.string("tail")
        }
        if !valid { offset = 0 }
        var request = valid ? cursor.string("request") : ""
        try handle.seek(toOffset: UInt64(offset))
        var pending = Data()
        var badLine = false
        while !isCancelled {
            let remaining = stamp.size - min(stamp.size, try handle.offset())
            if remaining == 0 { break }
            let chunk = try handle.read(upToCount: Int(min(remaining, 256 * 1024))) ?? Data()
            if chunk.isEmpty { break }
            pending.append(chunk)
            let changed = try database.transaction { () -> Bool in
                var changed = false
                var start = pending.startIndex
                while !isCancelled, let end = pending[start...].firstIndex(of: 10) {
                    let data = pending.subdata(in: start..<end)
                    if !data.isEmpty {
                        let raw = jsonObject(data)
                        if raw.isEmpty { badLine = true }
                        else if try ingest(raw, session: session, request: &request, full: full) { changed = true }
                    }
                    offset += end - start + 1
                    start = end + 1
                }
                pending.removeSubrange(pending.startIndex..<start)
                try database.put("usage_cursors", key: key, value: [
                    "offset": offset, "size": Int(stamp.size), "modified": stamp.modified?.timeIntervalSince1970 as Any? ?? NSNull(),
                    "device": String(stamp.device), "inode": String(stamp.inode), "tail": try tailHash(handle, at: offset), "request": request
                ])
                return changed
            }
            if changed { revision += 1 }
        }
        if badLine { warning = L("部分 Antigravity 记录不完整，已保存历史保留", "Some Antigravity records are incomplete; saved history is retained") }
        if !isCancelled { scanned[file] = stamp }
    }

    private func tailHash(_ handle: FileHandle, at offset: Int) throws -> String {
        let position = try handle.offset()
        try handle.seek(toOffset: UInt64(max(0, offset - 4096)))
        let bytes = try handle.read(upToCount: min(4096, offset)) ?? Data()
        try handle.seek(toOffset: position)
        return digest(bytes)
    }

    private func ingest(_ raw: Object, session: String, request: inout String, full: Bool) throws -> Bool {
        let type = raw.string("type")
        let isRequest = type == "USER_INPUT"
        guard isRequest || type == "PLANNER_RESPONSE", let timestamp = parsedDate(raw["created_at"]),
              let sourceStep = raw.integer("step_index") else { return false }
        let kind = isRequest ? "user_request" : "model_call"
        let id = "antigravity:" + identity([session, type, raw["created_at"] ?? NSNull(), sourceStep])
        let sourceContent = raw.string("content")
        var content = sourceContent
        if isRequest, let open = sourceContent.range(of: "<USER_REQUEST>") {
            let body = sourceContent[open.upperBound...]
            content = String(body[..<(body.range(of: "</USER_REQUEST>")?.lowerBound ?? body.endIndex)])
        } else if isRequest, sourceContent.contains("<USER_SETTINGS_CHANGE>") {
            return false
        }
        content = content.trimmingCharacters(in: .whitespacesAndNewlines)
        let truncated = (raw["truncated_fields"] as? [Any]).map { !$0.isEmpty } ?? ((raw["truncated_fields"] as? String)?.isEmpty == false)
        let contentDigest = digest(Data(content.utf8))
        let field = isRequest ? "user" : "final"
        // A full transcript can lag the compact ledger. Enrich only existing
        // records with the same exact source identity, never count them twice.
        if full {
            guard !content.isEmpty, !truncated,
                  let stored = try database.query("SELECT data FROM antigravity_records WHERE id=?", [id]).first else { return false }
            var payload = jsonObject(Data(stored.string("data").utf8))
            guard payload.flag("antigravity_truncated") else { return false }
            payload["antigravity_truncated"] = false
            payload["antigravity_full_body"] = true
            payload["prompt_preview"] = String(content.prefix(320))
            payload["body_digest"] = contentDigest
            try storeBody(id, field: field, content: content, complete: true)
            try database.run("UPDATE antigravity_records SET search=?,data=? WHERE id=?", [payload.string("prompt_preview"), jsonString(payload), id])
            return true
        }
        if isRequest { request = id }
        var status: String
        // DONE on USER_INPUT describes receipt of the user step, not turn completion.
        switch isRequest ? "" : raw.string("status") {
        case "DONE": status = "completed"
        case "ERROR", "FAILED": status = "failed"
        case "CANCELLED", "CANCELED", "ABORTED": status = "aborted"
        default: status = "unknown"
        }
        var counters: Object = [:]
        for key in ["input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "thinking_tokens", "total_tokens"] {
            counters[key] = raw.integer(key)
        }
        let final = !isRequest && status == "completed" && !content.isEmpty && (raw["tool_calls"] as? [Any] ?? []).isEmpty
        let previous = isRequest || truncated || request.isEmpty ? try database.query("SELECT data,status,request FROM antigravity_records WHERE id=?", [id]).first : nil
        if isRequest, previous?.string("status") == "completed" { status = "completed" }
        let groupID = request.isEmpty ? previous?.string("request") ?? "" : request
        var payload: Object = [
            "client_kind": "antigravity", "source_id": "local:antigravity", "session_id": session,
            "id": id, "timestamp": iso(timestamp), "model": raw.string("model"), "status": status,
            "record_kind": isRequest ? kind : groupID.isEmpty ? "unassigned" : kind,
            "prompt_preview": String(content.prefix(320)), "antigravity_counters": counters,
            "antigravity_truncated": truncated, "antigravity_final": final,
            "body_digest": contentDigest
        ]
        let effort = raw.string("reasoning_effort", raw.string("thinking_level"))
        if !effort.isEmpty { payload["reasoning_effort"] = effort }
        for key in ["auth_mode", "billing_source"] where !raw.string(key).isEmpty { payload[key] = raw[key] }
        if truncated, let stored = previous {
            let previous = jsonObject(Data(stored.string("data").utf8))
            if !previous.flag("antigravity_truncated"), !previous.string("prompt_preview").isEmpty {
                for key in ["antigravity_truncated", "antigravity_full_body", "prompt_preview", "body_digest"] { payload[key] = previous[key] }
            }
        }
        if !isRequest, let input = counters.integer("input_tokens"), let cached = counters.integer("cache_read_tokens"),
           let output = counters.integer("output_tokens"), (counters.integer("cache_write_tokens") ?? 0) == 0 {
            // This App records uncached input and cache reads separately.
            let inclusive = input.addingReportingOverflow(cached)
            let total = inclusive.partialValue.addingReportingOverflow(output)
            if !inclusive.overflow, !total.overflow {
                payload["raw_input_tokens"] = input
                payload["input_tokens"] = inclusive.partialValue
                payload["cached_input_tokens"] = cached
                payload["output_tokens"] = output
                payload["total_tokens"] = total.partialValue
            }
        }
        if !isRequest { payload = priced(payload) }
        try database.run("""
            INSERT INTO antigravity_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET request=CASE WHEN excluded.request='' THEN antigravity_records.request ELSE excluded.request END,
                model=excluded.model,status=excluded.status,search=excluded.search,data=excluded.data,
                input=excluded.input,cached=excluded.cached,output=excluded.output,total=excluded.total,cost=excluded.cost
            WHERE antigravity_records.data<>excluded.data OR (excluded.request<>'' AND antigravity_records.request<>excluded.request)
            """, [id, session, request, kind, timestamp.timeIntervalSince1970, raw.string("model"), status,
                  payload.string("prompt_preview"), jsonString(payload), payload["input_tokens"] ?? NSNull(), payload["cached_input_tokens"] ?? NSNull(),
                  payload["output_tokens"] ?? NSNull(), payload["total_tokens"] ?? NSNull(), payload["cost_usd"] ?? NSNull()])
        let changed = try database.query("SELECT changes() AS n").first?.integer("n") == 1
        if changed, !content.isEmpty, payload.string("body_digest") == contentDigest, !payload.flag("antigravity_full_body") {
            try storeBody(id, field: field, content: content, complete: !truncated)
        }
        if final, !request.isEmpty {
            try database.run("UPDATE antigravity_records SET status='completed',data=json_set(data,'$.status','completed') WHERE id=? AND status<>'completed'", [request])
            let finished = try database.query("SELECT changes() AS n").first?.integer("n") == 1
            return changed || finished
        }
        return changed
    }

    private func storeBody(_ id: String, field: String, content: String, complete: Bool) throws {
        let body: Object = ["id": id, field: content, field + "_complete": complete]
        let hash = identity(body)
        let prior = try database.query("SELECT digest,revision FROM usage_request_archives WHERE id=?", [id]).first
        guard prior?.string("digest") != hash else { return }
        try database.archiveRequestMessage(id, encoded: jsonString(body), digest: hash, revision: (prior?.integer("revision") ?? 0) + 1)
    }

    private func priced(_ row: Object) -> Object {
        var raw = row
        var reference = row
        reference["service_tier"] = "default"
        reference["auth_mode"] = "api_key"
        let estimated = ModelPricing.shared.price(reference)
        raw["cost_usd"] = estimated["cost_usd"] ?? NSNull()
        raw["price_rates"] = estimated["price_rates"]
        raw["pricing_status"] = estimated.number("cost_usd") == nil ? "unpriced" : "estimated"
        raw["pricing_basis"] = "official_api_reference"
        return raw
    }

    private func refreshPrices() throws {
        let key = "antigravity-reference-prices", version = ModelPricing.shared.version
        guard database.object("usage_meta", key: key).string("version") != version else { return }
        var after = ""
        while !isCancelled {
            let rows = try database.query("SELECT id,data FROM antigravity_records WHERE kind='model_call' AND id>? ORDER BY id LIMIT 256", [after])
            if rows.isEmpty {
                try database.put("usage_meta", key: key, value: ["version": version])
                return
            }
            let changed = try database.transaction { () -> Bool in
                var changed = false
                for row in rows {
                    let value = priced(jsonObject(Data(row.string("data").utf8)))
                    let encoded = jsonString(value)
                    if encoded != row.string("data") {
                        try database.run("UPDATE antigravity_records SET data=?,cost=? WHERE id=?", [encoded, value["cost_usd"] ?? NSNull(), row.string("id")])
                        changed = true
                    }
                }
                return changed
            }
            if changed { revision += 1 }
            after = rows.last!.string("id")
        }
    }

    func models() throws -> [String] {
        try database.query("SELECT DISTINCT model FROM antigravity_records WHERE kind='model_call' AND model<>'' ORDER BY model")
            .map { $0.string("model") }.filter(UsageModel.isSingleModel)
    }

    func nextFuture(after now: Date) throws -> Date? {
        try database.query("SELECT min(timestamp) AS stamp FROM antigravity_records WHERE timestamp>?", [now.timeIntervalSince1970]).first?.number("stamp")
            .map { Date(timeIntervalSince1970: $0) }
    }

    private func filter(range: UsageRange, model: String = "all") -> (String, [Any]) {
        var sql = "timestamp>=? AND timestamp<=?"
        var args: [Any] = [range.start.timeIntervalSince1970, range.end.timeIntervalSince1970]
        if model != "all" {
            sql += " AND (model=? OR (kind='user_request' AND EXISTS(SELECT 1 FROM antigravity_records c WHERE c.request=antigravity_records.id AND c.kind='model_call' AND c.model=?)))"
            args += [model, model]
        }
        return (sql, args)
    }

    private func decode(_ rows: [Object]) throws -> [UsageRow] {
        let requests = rows.filter { $0.string("kind") == "user_request" }.map { $0.string("id") }
        let owners = Array(Set(rows.filter { $0.string("kind") == "model_call" }.map { $0.string("request") }.filter { !$0.isEmpty }))
        var models: [String: [AntigravityModelLabel]] = [:]
        var metrics: [String: Object] = [:]
        var summaries: [String: String] = [:]
        if !owners.isEmpty {
            let slots = Array(repeating: "?", count: owners.count).joined(separator: ",")
            for item in try database.query("SELECT id,search FROM antigravity_records WHERE kind='user_request' AND id IN (\(slots))", owners) {
                summaries[item.string("id")] = item.string("search")
            }
        }
        if !requests.isEmpty {
            let slots = Array(repeating: "?", count: requests.count).joined(separator: ",")
            for item in try database.query("SELECT DISTINCT request,model,json_extract(data,'$.reasoning_effort') AS effort FROM antigravity_records WHERE request IN (\(slots)) AND kind='model_call' ORDER BY model,effort", requests) {
                let label = AntigravityModelLabel(item.string("model"), explicitEffort: item.string("effort"))
                models[item.string("request"), default: []].append(label)
            }
            let columns = ["input", "cached", "output", "total", "cost"].map { "CASE WHEN count(\($0))=count(*) THEN sum(\($0)) END AS \($0)" }.joined(separator: ",")
            for item in try database.query("SELECT request,\(columns) FROM antigravity_records WHERE request IN (\(slots)) AND kind='model_call' GROUP BY request", requests) {
                metrics[item.string("request")] = item
            }
        }
        return rows.map { row in
            var raw = jsonObject(Data(row.string("data").utf8))
            let label = AntigravityModelLabel(raw.string("model"), explicitEffort: raw.string("reasoning_effort"))
            let members = models[row.string("id")] ?? []
            if !members.isEmpty { raw["upstream_models"] = Set(members.map(\.source).filter { !$0.isEmpty }).sorted() }
            if !label.title.isEmpty { raw["model_display_name"] = label.title }
            else if !members.isEmpty {
                raw["model_display_name"] = Set(members.map(\.title).filter { !$0.isEmpty }).sorted().joined(separator: " + ")
            }
            let observedEfforts = Set(members.map(\.effort))
            if !label.effort.isEmpty { raw["reasoning_effort"] = label.effort }
            else if !observedEfforts.contains(""), let effort = observedEfforts.first {
                raw["reasoning_effort"] = observedEfforts.count == 1 ? effort : "mixed"
            }
            if row.string("kind") == "model_call" {
                raw["prompt_preview"] = summaries[row.string("request")] ?? ""
            }
            if let counts = metrics[row.string("id")] {
                for (column, key) in [("input", "input_tokens"), ("cached", "cached_input_tokens"), ("output", "output_tokens"), ("total", "total_tokens"), ("cost", "cost_usd")] {
                    raw[key] = counts[column]
                }
                if counts.number("cost") != nil { raw["pricing_status"] = "estimated" }
            }
            return UsageRow(raw: raw)
        }
    }

    func logPage(range: UsageRange, mode: String, model: String, status: String, query: String, page: Int) throws -> LogProjection {
        var (sql, args) = filter(range: range, model: model)
        sql += mode == "calls" ? " AND kind='model_call'" : " AND (kind='user_request' OR request='')"
        if status != "all" { sql += " AND status=?"; args.append(status) }
        if !query.isEmpty {
            sql += " AND (instr(lower(search),lower(?))>0 OR instr(lower(session),lower(?))>0 OR (kind='model_call' AND EXISTS(SELECT 1 FROM antigravity_records owner WHERE owner.id=antigravity_records.request AND owner.kind='user_request' AND instr(lower(owner.search),lower(?))>0)))"
            args += [query, query, query]
        }
        let summary = try database.query("SELECT count(*) AS n,min(timestamp) AS first,sum(request='') AS orphan FROM antigravity_records WHERE \(sql)", args).first ?? [:]
        let count = summary.integer("n") ?? 0
        let selected = max(0, min(page, max(0, (count - 1) / 60)))
        let rows = try decode(database.query("SELECT id,kind,request,data FROM antigravity_records WHERE \(sql) ORDER BY timestamp DESC,id LIMIT 60 OFFSET ?", args + [selected * 60]))
        let calendar = Calendar.current, year = calendar.component(.year, from: Date())
        let first = summary.number("first").map { Date(timeIntervalSince1970: $0) }
        let showYear = range.start == .distantPast ? first.map { calendar.component(.year, from: $0) != year } ?? false
            : calendar.component(.year, from: range.start) != year || calendar.component(.year, from: range.end) != year
        return LogProjection(rows: rows, count: count, unassigned: summary.integer("orphan") ?? 0, showYear: showYear, page: selected)
    }

    func trend(range: UsageRange, model: String = "all", granularity: String) throws -> TrendProjection {
        let (sql, args) = filter(range: range, model: model)
        var summary = UsageSummary()
        let aggregate = "count(*) AS n,sum(input) AS input,sum(cached) AS cached,sum(output) AS output,sum(total) AS tokens,sum(cost) AS cost,count(*)-count(cost) AS unknown_costs,sum(total IS NULL OR cost IS NULL) AS incomplete"
        let totals = try database.query("SELECT \(aggregate) FROM antigravity_records WHERE kind='model_call' AND \(sql)", args).first ?? [:]
        summary.requests = try database.query("SELECT count(*) AS n FROM antigravity_records WHERE kind='user_request' AND \(sql)", args).first?.integer("n") ?? 0
        summary.applyAntigravity(totals)
        let models = try database.query("SELECT model,\(aggregate) FROM antigravity_records WHERE kind='model_call' AND model<>'' AND \(sql) GROUP BY model", args)
            .map { row in UsageModel(id: row.string("model"), name: modelName(row.string("model")), metric: ModelMetrics(tokens: row.integer("tokens"), cost: row.number("cost"), costComplete: row.integer("unknown_costs") == 0)) }
        let byCost = models.sorted { ($0.metric.cost ?? 0) == ($1.metric.cost ?? 0) ? $0.id < $1.id : ($0.metric.cost ?? 0) > ($1.metric.cost ?? 0) }
        let byTokens = models.sorted { ($0.metric.tokens ?? 0) == ($1.metric.tokens ?? 0) ? $0.id < $1.id : ($0.metric.tokens ?? 0) > ($1.metric.tokens ?? 0) }
        let recent = try decode(database.query("SELECT id,kind,data FROM antigravity_records WHERE kind='user_request' AND \(sql) ORDER BY timestamp DESC,id LIMIT 3", args))
        return TrendProjection(summary: summary, days: try days(sql: sql, args: args, range: range, granularity: granularity), recent: recent, modelRows: [byCost, byTokens, []])
    }

    private func days(sql: String, args: [Any], range: UsageRange, granularity: String) throws -> [DayUsage] {
        let grouping = granularity == "hour" ? "%Y-%m-%d %H" : granularity == "week" ? "%Y-%W" : "%Y-%m-%d"
        let rows = try database.query("SELECT min(timestamp) AS t,sum(kind='user_request') AS requests,sum(kind='model_call') AS calls,sum(total) AS tokens,sum(cost) AS cost FROM antigravity_records WHERE \(sql) GROUP BY strftime('\(grouping)',timestamp,'unixepoch','localtime') ORDER BY t", args)
        var calendar = Calendar.current
        calendar.firstWeekday = 2
        let component: Calendar.Component = granularity == "hour" ? .hour : granularity == "week" ? .weekOfYear : .day
        var buckets: [Date: DayUsage] = [:]
        for row in rows {
            if let date = row.number("t").map({ Date(timeIntervalSince1970: $0) }), let start = calendar.dateInterval(of: component, for: date)?.start {
                var day = buckets[start] ?? DayUsage(date: start, tokens: nil, cost: nil, calls: 0)
                day.calls += row.integer("calls") ?? 0
                day.requests += row.integer("requests") ?? 0
                if let tokens = row.integer("tokens") { day.tokens = (day.tokens ?? 0) + tokens }
                if let cost = row.number("cost") { day.cost = (day.cost ?? 0) + cost }
                buckets[start] = day
            }
        }
        let start = range.start == .distantPast ? buckets.keys.min() ?? calendar.startOfDay(for: range.end) : range.start
        var cursor = calendar.dateInterval(of: component, for: start)?.start ?? start
        var days: [DayUsage] = []
        while cursor <= range.end {
            days.append(buckets[cursor] ?? DayUsage(date: cursor, tokens: nil, cost: nil, calls: 0))
            guard let next = calendar.date(byAdding: component, value: 1, to: cursor), next > cursor else { break }
            cursor = next
        }
        return days
    }

    func detail(_ row: UsageRow, page: Int) throws -> AntigravityDetail {
        let isRequest = row.raw.string("record_kind") == "user_request"
        let body = try database.requestMessageRow(row.id).map { jsonObject(Data($0.string("data").utf8)) } ?? [:]
        guard isRequest else { return AntigravityDetail(answer: body.string("final"), counters: row.raw.object("antigravity_counters")) }
        let count = try database.query("SELECT count(*) AS n FROM antigravity_records WHERE request=? AND kind='model_call'", [row.id]).first?.integer("n") ?? 0
        let members = try decode(database.query("SELECT id,kind,request,data FROM antigravity_records WHERE request=? AND kind='model_call' ORDER BY timestamp,id LIMIT 50 OFFSET ?", [row.id, max(0, page) * 50]))
        // The final completed model step stays tied to its explicit request ID.
        let last = try database.query("SELECT id,data FROM antigravity_records WHERE request=? AND kind='model_call' AND json_extract(data,'$.antigravity_final')=1 ORDER BY timestamp DESC,id DESC LIMIT 1", [row.id]).first
        let answer = try last.flatMap { try database.requestMessageRow($0.string("id")) }.map { jsonObject(Data($0.string("data").utf8)).string("final") } ?? ""
        let answerTruncated = last.map { jsonObject(Data($0.string("data").utf8)).flag("antigravity_truncated") } ?? false
        return AntigravityDetail(prompt: body.string("user"), answer: answer, members: members, pages: max(1, (count + 49)/50), answerTruncated: answerTruncated)
    }

    func reportAggregate(range: DateInterval) throws -> UsageReportStore.Aggregate {
        let args = [range.start.timeIntervalSince1970, range.end.timeIntervalSince1970]
        var result = UsageReportStore.Aggregate()
        for group in try database.query("SELECT model,count(*) AS n,count(total) AS metered,count(cost) AS priced,sum(input) AS input,sum(cached) AS cached,sum(output) AS output,sum(total) AS tokens,sum(cost) AS cost FROM antigravity_records WHERE kind='model_call' AND timestamp>=? AND timestamp<? GROUP BY model", args) {
            let count = group.integer("n") ?? 0
            let name = requestValueText(modelName(group.string("model")))
            let metered = group.integer("metered") ?? 0, priced = group.integer("priced") ?? 0
            let tokens = group.integer("tokens") ?? 0, cost = group.number("cost")
            result.modelCalls += count
            result.unknownTokens += count - metered
            result.unknownCosts += count - priced
            result.input += group.integer("input") ?? 0
            result.cached += group.integer("cached") ?? 0
            result.output += group.integer("output") ?? 0
            result.total += tokens
            result.cost += cost ?? 0
            result.priced += priced
            result.models[name] = UsageReportModel(name: name, calls: count, tokens: tokens, costUSD: cost, costComplete: count == priced, tokensKnown: metered > 0)
        }
        let clock = "CAST(strftime('%H',timestamp,'unixepoch','localtime') AS INTEGER)*3600+CAST(strftime('%M',timestamp,'unixepoch','localtime') AS INTEGER)*60+CAST(strftime('%S',timestamp,'unixepoch','localtime') AS INTEGER)"
        for group in try database.query("SELECT (\(clock))/21600 AS bucket,count(*) AS n FROM antigravity_records WHERE kind='user_request' AND timestamp>=? AND timestamp<? GROUP BY bucket", args) {
            let bucket = group.integer("bucket") ?? 0, count = group.integer("n") ?? 0
            if result.times.indices.contains(bucket) { result.times[bucket] = count; result.requests += count }
        }
        result.first = try database.query("SELECT timestamp FROM antigravity_records WHERE kind='user_request' AND timestamp>=? AND timestamp<? ORDER BY (\(clock)),timestamp LIMIT 1", args).first?.number("timestamp").map { Date(timeIntervalSince1970: $0) }
        result.last = try database.query("SELECT timestamp FROM antigravity_records WHERE kind='user_request' AND timestamp>=? AND timestamp<? ORDER BY (\(clock)) DESC,timestamp LIMIT 1", args).first?.number("timestamp").map { Date(timeIntervalSince1970: $0) }
        return result
    }

    func ranking(page: Int) throws -> (rows: [Object], total: Int) {
        let count = try database.query("SELECT count(DISTINCT session) AS n FROM antigravity_records WHERE kind='user_request' AND timestamp<=?", [Date().timeIntervalSince1970]).first?.integer("n") ?? 0
        let sql = """
            SELECT session,count(*) AS requests,
                (SELECT r.search FROM antigravity_records r WHERE r.session=a.session AND r.kind='user_request' ORDER BY r.timestamp,id LIMIT 1) AS title
            FROM antigravity_records a WHERE kind='user_request' AND timestamp<=? GROUP BY session
            ORDER BY requests DESC,session LIMIT 50 OFFSET ?
            """
        let rows = try database.query(sql, [Date().timeIntervalSince1970, max(0, page)*50])
        return (rows, count)
    }

    func activity() throws -> ActivityStats {
        let now = Date(), calendar = Calendar.current
        let from = calendar.date(byAdding: .day, value: -364, to: now) ?? now
        let range = UsageRange(period: "custom", from: from, through: now, now: now)
        let (sql, args) = filter(range: range)
        let values = try days(sql: sql, args: args, range: range, granularity: "day")
        var result = ActivityStats()
        result.days = values
        result.peak = values.compactMap(\.tokens).max()
        result.total = try database.query("SELECT sum(total) AS n FROM antigravity_records WHERE kind='model_call' AND timestamp<=?", [now.timeIntervalSince1970]).first?.integer("n")
        var run = 0
        for day in values {
            run = day.requests > 0 ? run + 1 : 0
            result.longestStreak = max(result.longestStreak, run)
            if calendar.isDateInYesterday(day.date) || (calendar.isDateInToday(day.date) && run > 0) { result.currentStreak = run }
        }
        return result
    }
}

private extension UsageSummary {
    mutating func applyAntigravity(_ row: Object) {
        calls = row.integer("n") ?? 0
        input = row.integer("input") ?? 0
        cached = row.integer("cached") ?? 0
        output = row.integer("output") ?? 0
        tokens = row.integer("tokens")
        cost = row.number("cost")
        unknownCosts = row.integer("unknown_costs") ?? 0
        incompleteRecords = row.integer("incomplete") ?? 0
    }
}

struct AntigravityDetail {
    var prompt = ""
    var answer = ""
    var counters: Object = [:]
    var members: [UsageRow] = []
    var pages = 1
    var answerTruncated = false
}
