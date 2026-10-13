import Foundation

// Source event timing is independent of which usage fragment supplies the tokens.
enum ClaudeTiming {
    static func observation(_ row: Object) -> Object {
        guard let date = parsedDate(row["timestamp"]) else {return [:]}
        let stamp = iso(date), stop = row.object("message").string("stop_reason")
        var result: Object = ["first":stamp,"last":stamp]
        if !stop.isEmpty {result["terminal"] = stamp; result["stop"] = stop}
        return result
    }
    static func merge(_ old: Object,_ next: Object) -> Object {
        var result = old
        for key in ["first","last"] {
            if let date = parsedDate(next[key]), parsedDate(old[key]).map({key == "first" ? date < $0 : date > $0}) ?? true {result[key] = next[key]}
        }
        if let date = parsedDate(next["terminal"]) {
            if let prior = parsedDate(old["terminal"]), date == prior, old.string("stop") != next.string("stop") {result["conflict"] = true}
            if parsedDate(old["terminal"]).map({date > $0}) ?? true {result["terminal"] = next["terminal"]; result["stop"] = next["stop"]}
        }
        if next.flag("conflict") {result["conflict"] = true}
        return result
    }
    static func requestFields(_ request: Object,main: [Object]) -> Object {
        var fields: Object = [:]
        let previousDerived = request.string("duration_source") == "main_call"
        let explicit = request.number("explicit_duration_ms") ?? (previousDerived ? nil : request.number("duration_ms"))
        let explicitEnd = request["explicit_ended_at"] ?? (previousDerived ? nil : request["ended_at"])
        if let explicit {fields["explicit_duration_ms"] = explicit; fields["explicit_ended_at"] = explicitEnd ?? NSNull()}
        let latest = main.first ?? [:], timing = latest.object("call_timing")
        let observed = parsedDate(timing["last"]) ?? parsedDate(latest["timestamp"])
        if let explicit, explicit >= 0, let end = parsedDate(explicitEnd), observed.map({end >= $0}) ?? true {
            fields["duration_ms"] = explicit; fields["ended_at"] = explicitEnd; fields["duration_source"] = "turn_duration"
            if !["failed","aborted"].contains(request.string("status")) {fields["status"] = "completed"}
            return fields
        }
        fields["duration_ms"] = NSNull(); fields["ended_at"] = NSNull(); fields["duration_source"] = NSNull()
        if request.string("status") == "completed" {fields["status"] = ""}
        guard !["failed","aborted"].contains(request.string("status")),
              latest.flag("call_timing_checked"), !latest.flag("call_timing_incomplete"), !timing.flag("conflict"),
              ["end_turn","stop_sequence"].contains(timing.string("stop")),
              let start = parsedDate(request["timestamp"]), let end = parsedDate(timing["terminal"]),
              let observed, end >= observed, end >= start else {return fields}
        // Equal timestamps from separate calls do not establish which call was last.
        if main.count > 1, let first = parsedDate(timing["first"]) ?? parsedDate(latest["timestamp"]), let other = parsedDate(main[1].object("call_timing")["first"]) ?? parsedDate(main[1]["timestamp"]), other >= first {return fields}
        let ms = (end.timeIntervalSince(start)*1000).rounded()
        guard ms.isFinite else {return fields}
        fields["duration_ms"] = ms; fields["ended_at"] = iso(end); fields["duration_source"] = "main_call"; fields["status"] = "completed"
        return fields
    }
}
