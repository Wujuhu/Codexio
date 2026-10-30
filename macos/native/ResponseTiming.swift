import Foundation

// One observer per response stream. Queue delays and wall-clock adjustments do
// not enter the duration: feed() supplies the transport's monotonic receipt time.
// A complete message/item is never evidence of a first token.
final class ResponseCallTiming {
    private var starts: [String:(TimeInterval,Date)] = [:]
    func observe(_ value: Object,response: Object,kind: String,at: TimeInterval,date: Date) -> Object {
        let id = response.string("id")
        guard !id.isEmpty else { return [:] }
        if kind == "response.created" {
            if starts[id] == nil {
                if starts.count >= 64, let oldest = starts.min(by:{$0.value.0 < $1.value.0})?.key { starts.removeValue(forKey:oldest) }
                starts[id] = (at,date)
            }
            return [:]
        }
        guard ["response.completed","response.failed","response.incomplete"].contains(kind) else { return [:] }
        let start = starts.removeValue(forKey:id)
        var fields: Object = ["status":kind == "response.completed" ? "completed" : kind == "response.failed" ? "failed" : "aborted","timing_version":1]
        let sources = [response,value,response.object("timing"),value.object("timing")]
        if let ms = sources.compactMap({$0.number("duration_ms")}).first(where:{$0.isFinite && $0 >= 0}) {
            fields["duration_ms"] = ms; fields["duration_source"] = "response"
        } else if let start, at >= start.0 {
            fields["duration_ms"] = (at-start.0)*1000; fields["duration_source"] = "response_stream"
            fields["call_started_at"] = iso(start.1); fields["call_ended_at"] = iso(date)
        } else {
            // Codex's Responses transport also supplies explicit engine timing
            // on some completions. It is per response, not an OTel histogram.
            for source in [response.object("timing_metrics"),value.object("timing_metrics")] {
                if let ms = source.number("engine_service_total_ms"), ms.isFinite, ms >= 0 {
                    fields["duration_ms"] = ms; fields["duration_source"] = "response_engine"; break
                }
            }
        }
        if let ttft = recordedTTFTMilliseconds(response) ?? recordedTTFTMilliseconds(value) {
            fields["ttft_ms"] = ttft; fields["ttft_source"] = "response"
        }
        return fields
    }
}

// Replayed completions may fill missing evidence, but must not replace a saved
// stream duration with the much shorter time taken to replay the same response.
func mergeResponseTiming(_ old: Object,_ incoming: Object) -> Object {
    var result = old
    for (key,value) in incoming where result[key] == nil || result[key] is NSNull { result[key] = value }
    return result
}
