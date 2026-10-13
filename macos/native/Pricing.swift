import Foundation

struct PriceRow: Identifiable {
    var raw: Object
    var id: String { raw.string("model") + ":" + raw.string("service_tier") + ":" + String(raw.integer("threshold") ?? 0) }
    var model: String { raw.string("model") }
}

final class PricingCatalog {
    let directory: URL
    private var cache: Object = [:]
    private var overrides: Object = [:]
    private(set) var bases: [Object] = []
    private(set) var rows: [PriceRow] = []
    private(set) var updated: Date?
    private(set) var warning: String?
    private(set) var version = ""
    private var ratesByModel: [String:[PriceRow]] = [:]
    private var basesByModel: [String:Object] = [:]
    private var billingPolicies: Object = [:]
    init(_ directory: URL) {
        self.directory = directory
        cache = readObject(directory.appendingPathComponent("pricing_cache.json"))
        overrides = readObject(directory.appendingPathComponent("pricing_overrides.json"))
        rebuild()
    }
    func rebuild() {
        let seed = Bundle.main.url(forResource:"pricing_seed",withExtension:"json").map(readObject) ?? [:]
        billingPolicies = seed.object("billing_policies")
        var merged: [String:Object] = [:]
        for raw in seed.objects("rows") + cache.objects("rows") where raw.integer("threshold") ?? 0 == 0 && ["default","standard"].contains(raw.string("service_tier","default")) {
            guard raw.number("input") != nil, raw.number("output") != nil else { continue }
            var row = raw
            for key in ["cache_read","cache_write"] where row.number(key) == nil { row[key] = merged[row.string("model")]?[key] }
            merged[row.string("model")] = row
        }
        if let officialSol = seed.objects("rows").first(where: {$0.string("model") == "gpt-6.1-sol"}) {
            merged["gpt-6.1-sol"] = officialSol
        }
        for (model, value) in overrides {
            guard let values = value as? [Object], var row = values.last(where: { ($0.integer("threshold") ?? 0) == 0 && ["default","standard"].contains($0.string("service_tier","default")) }) else { continue }
            row["model"] = model; row["locked"] = true; merged[model] = row
        }
        bases = merged.values.sorted { $0.string("model") < $1.string("model") }
        basesByModel = Dictionary(bases.map { ($0.string("model"),$0) },uniquingKeysWith:{$1})
        rows = bases.flatMap { base -> [PriceRow] in
            let model = base.string("model").replacingOccurrences(of:#"-\d{4}-\d{2}-\d{2}$"#,with:"",options:.regularExpression)
            if model == "gpt-6.1-sol", !billingPolicies.object(model).isEmpty {
                return ["default","priority","ultrafast"].compactMap { tier in
                    solRates(base:base,mode:"subscription",tier:tier,inputTokens:0).map { PriceRow(raw:$0) }
                }
            }
            let modern = ["gpt-6.1-sol","gpt-6-sol","gpt-6-luna","gpt-5.6","gpt-5.6-sol","gpt-5.6-terra","gpt-5.6-luna","gpt-5.5"].contains(model)
            let fast: Double? = model == "gpt-5.4" ? 2 : (modern || model == "gpt-6-astra" ? 2.5 : nil)
            let thresholds = modern || model == "gpt-5.4" ? [0,272000] : [0]
            return ([ ("default",1.0) ] + (fast.map { [("priority",$0)] } ?? [])).flatMap { tier, speed in
                thresholds.map { threshold in
                    var row = base; row["service_tier"] = tier; row["threshold"] = threshold
                    let inputMultiplier = speed * (threshold > 0 ? 2 : 1), outputMultiplier = speed * (threshold > 0 ? 1.5 : 1)
                    for key in ["input","cache_read","cache_write","output"] {
                        let baseKey = key == "cache_write" && (tier != "default" || threshold > 0) ? "input" : key
                        row[key] = base.number(baseKey).map { $0 * (key == "output" ? outputMultiplier : inputMultiplier) } as Any?
                    }
                    return PriceRow(raw:row)
                }
            }
        }
        ratesByModel = Dictionary(grouping:rows,by: { $0.model })
        func signature(_ raw: Object) -> Object { var value = raw; value.removeValue(forKey:"updated_at"); return value }
        let policyContent: Object = billingPolicies.mapValues { raw -> Any in
            guard var policy = raw as? Object else { return raw }
            policy.removeValue(forKey:"sources"); policy.removeValue(forKey:"verified_at")
            return policy
        }
        version = identity(["rule":4,"billing_policies":policyContent,"standard_rows":bases.map(signature),"rows":rows.map { signature($0.raw) }] as Object)
        updated = parsedDate(cache["updated_at"])
    }
    func price(_ raw: Object) -> Object {
        var value = raw; value["cost_usd"] = NSNull(); value["pricing_status"] = "unpriced"; value["price_version"] = version
        for key in ["price_rates","pricing_mode","pricing_identity_estimated","assumed_billing_source"] { value.removeValue(forKey:key) }
        let keys = ["input_tokens","cached_input_tokens","cache_write_input_tokens","output_tokens","reasoning_output_tokens"]
        let values = keys.map { raw.integer($0) ?? (raw[$0] == nil ? 0 : -1) }
        guard raw["input_tokens"] != nil, raw["output_tokens"] != nil, values.allSatisfy({$0 >= 0}), values[1]+values[2] <= values[0], values[4] <= values[3], !raw.string("quality").hasPrefix("invalid") else { value["pricing_status"] = "invalid"; return value }
        let provider = raw.string("provider","unknown"), official = ["openai","codexio-upstream"].contains(provider)
        guard provider != "unknown", !provider.isEmpty else { return value }
        let model = official ? raw.string("model").replacingOccurrences(of:"openai/",with:"") : raw.string("model")
        let tier = normalizedTier(raw.string("service_tier"))
        let rawTier = raw.string("service_tier").trimmingCharacters(in:.whitespacesAndNewlines).lowercased()
        let missingTier = rawTier.isEmpty || rawTier == "auto"
        let selectedTier = missingTier ? "default" : tier
        let normalizedModel = model.replacingOccurrences(of:#"-\d{4}-\d{2}-\d{2}$"#,with:"",options:.regularExpression)
        let identity = BillingIdentity.resolve(raw)
        var identityEstimated = false
        var assumedSource: String?
        let selected: Object
        if normalizedModel == "gpt-6.1-sol", !billingPolicies.object(normalizedModel).isEmpty {
            let policy = billingPolicies.object(normalizedModel)
            let mode: String
            if identity.mode == "unknown" {
                mode = official ? policy.string("unknown_official_mode","subscription") : "legacy"
                assumedSource = mode
            } else { mode = identity.mode }
            identityEstimated = identity.mode == "unknown" && official ? policy.flag("unknown_official_estimated",true) : identity.estimated
            guard let base = basesByModel[model], var derived = solRates(base:base,mode:mode,tier:selectedTier,inputTokens:values[0]) else { return value }
            if identityEstimated { derived["pricing_identity_estimated"] = true }
            else { derived.removeValue(forKey:"pricing_identity_estimated") }
            if let assumedSource { derived["assumed_billing_source"] = assumedSource; value["assumed_billing_source"] = assumedSource }
            selected = derived
            value["pricing_mode"] = mode
            if identityEstimated { value["pricing_identity_estimated"] = true }
        } else {
            let candidates = ratesByModel[model] ?? []
            let threshold = candidates.map { $0.raw.integer("threshold") ?? 0 }.filter { values[0] > $0 }.max() ?? 0
            guard let candidate = candidates.first(where: { $0.raw.string("service_tier") == selectedTier && $0.raw.integer("threshold") == threshold }) else { return value }
            selected = candidate.raw
        }
        let parts = [values[0]-values[1]-values[2],values[1],values[2],values[3]]
        let rates = ["input","cache_read","cache_write","output"].map { selected.number($0) }
        guard zip(parts,rates).allSatisfy({$0.0 == 0 || $0.1 != nil}) else { return value }
        value["cost_usd"] = zip(parts,rates).reduce(0.0) { $0 + Double($1.0) * ($1.1 ?? 0) / 1e6 }
        value["pricing_status"] = tier == "unknown" || raw.string("quality").hasPrefix("cumulative") || !official || identityEstimated ? "estimated" : "priced"
        value["price_rates"] = selected
        return value
    }
    private func solRates(base: Object, mode: String, tier: String, inputTokens: Int) -> Object? {
        let policy = billingPolicies.object("gpt-6.1-sol"), selected = policy.object(mode)
        guard !selected.isEmpty, let speed = selected.object("speeds").number(tier), speed >= 0 else { return nil }
        let threshold = selected.integer("long_context_threshold") ?? 0
        let long = threshold > 0 && inputTokens > threshold
        let inputMultiplier = speed * (long ? 2 : 1), outputMultiplier = speed * (long ? 1.5 : 1)
        let basis = selected.string("cache_write_basis","legacy")
        var row = base
        row["service_tier"] = tier; row["threshold"] = long ? threshold : 0
        row["long_context_threshold"] = threshold; row["pricing_mode"] = mode
        row["speed_multiplier"] = speed; row["input_multiplier"] = inputMultiplier; row["output_multiplier"] = outputMultiplier
        row["cache_write_basis"] = basis; row["rule_version"] = policy.string("rule_version")
        row["base_rates"] = Dictionary(uniqueKeysWithValues:["input","cache_read","cache_write","output"].map { ($0,base[$0] ?? NSNull()) })
        row["multipliers"] = ["input":inputMultiplier,"cache_read":inputMultiplier,"cache_write":inputMultiplier,"output":outputMultiplier]
        for key in ["input","cache_read","cache_write","output"] {
            let baseKey = key == "cache_write" && (basis == "input" || (basis == "legacy" && (tier != "default" || long))) ? "input" : key
            row[key] = base.number(baseKey).map { $0 * (key == "output" ? outputMultiplier : inputMultiplier) } as Any?
        }
        return row
    }
    func setOverride(model: String, rates: Object?) throws {
        if var rates {
            guard let input = rates.number("input"), let output = rates.number("output"), input >= 0, output >= 0 else { throw AppFailure(L("输入和输出基础价必须为非负数", "Input and output base prices must be nonnegative")) }
            for key in ["cache_read","cache_write"] where rates[key] != nil && !(rates[key] is NSNull) { guard let n = rates.number(key), n >= 0 else { throw AppFailure(L("单价无效", "Invalid price")) } }
            rates["model"] = model; rates["service_tier"] = "default"; rates["threshold"] = 0; rates["source"] = "manual"; rates["updated_at"] = iso()
            overrides[model] = [rates]
        } else { overrides.removeValue(forKey:model) }
        try atomicJSON(overrides,to:directory.appendingPathComponent("pricing_overrides.json")); rebuild(); try archive()
    }
    private func archive() throws {
        let destination = directory.appendingPathComponent("versions/\(version).json")
        guard !FileManager.default.fileExists(atPath:destination.path) else { return }
        try atomicJSON(["price_version":version,"created_at":iso(),"standard_rows":bases,"billing_policies":billingPolicies,"rows":rows.map(\.raw),"unit":"USD per million tokens"],to:destination)
    }
    func sync(force: Bool = false) throws {
        let retry: TimeInterval = cache.string("status") == "offline" ? 3600 : 86400
        if !force, let checked = parsedDate(cache["checked_at"]), Date().timeIntervalSince(checked) < retry { return }
        let primary = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
        let secondary = "https://models.dev/api.json"
        func fetch(_ source: String) throws -> [Object] {
            let payload = jsonObject(try HTTP.read(URL(string:source)!,maximum:25_000_000))
            let objects = source == primary ? payload : payload.object("openai").object("models")
            let fields = ["input":"input_cost_per_token","output":"output_cost_per_token","cache_read":"cache_read_input_token_cost","cache_write":"cache_creation_input_token_cost"]
            var result: [Object] = []
            for (name,value) in objects {
                guard let raw = value as? Object else { continue }
                if source == primary && (raw.string("litellm_provider") != "openai" || !["chat","responses","completion"].contains(raw.string("mode"))) { continue }
                let model = name.hasPrefix("openai/") ? String(name.dropFirst(7)) : name
                guard !model.isEmpty, !model.contains("/"), !model.hasPrefix("ft:") else { continue }
                var row: Object = ["model":model,"service_tier":"default","threshold":0,"source":source,"updated_at":iso(),"locked":false]
                for (key,field) in fields {
                    let amount = source == primary ? raw.number(field).map {$0*1e6} : raw.object("cost").number(key)
                    if let amount, amount >= 0 { row[key] = amount }
                }
                if row.number("input") != nil && row.number("output") != nil { result.append(row) }
            }
            guard !result.isEmpty else { throw AppFailure(L("价格目录无有效内容", "No valid prices in the catalog")) }
            return result
        }
        do {
            var fetched: [Object], source = primary, verification: [Object] = [], conflicts = Set<String>()
            do { fetched = try fetch(primary) }
            catch { source = secondary; fetched = try fetch(secondary) }
            warning = nil
            if source == primary {
                do {
                    verification = try fetch(secondary)
                    let references = Dictionary(verification.map {($0.string("model"),$0)},uniquingKeysWith:{$1})
                    for row in fetched {
                        guard let reference = references[row.string("model")] else { continue }
                        if ["input","output","cache_read","cache_write"].contains(where:{ key in
                            guard let left = row.number(key), let right = reference.number(key) else { return false }
                            return abs(left-right) > max(1e-9,max(abs(left),abs(right))*1e-6)
                        }) { conflicts.insert(row.string("model")) }
                    }
                    if !conflicts.isEmpty {
                        fetched.removeAll {conflicts.contains($0.string("model"))}
                        fetched += bases.filter {conflicts.contains($0.string("model")) && !$0.flag("locked")}
                        warning = L("两源单价冲突，保留已有价格", "Price sources disagree; saved prices are retained")+": "+conflicts.sorted().joined(separator:", ")
                    }
                } catch { warning = L("备源核对暂不可用", "Reference price verification is unavailable") }
            }
            cache = ["rows":fetched,"updated_at":iso(),"checked_at":iso(),"status":conflicts.isEmpty ? "synced" : "conflict","active_source":source,"verification_rows":verification,"conflicting_models":conflicts.sorted(),"warning":warning as Any? ?? NSNull()]
            try atomicJSON(cache,to:directory.appendingPathComponent("pricing_cache.json")); rebuild(); try archive()
        } catch {
            cache["checked_at"] = iso(); cache["status"] = "offline"; try? atomicJSON(cache,to:directory.appendingPathComponent("pricing_cache.json"))
            throw error
        }
    }
}
