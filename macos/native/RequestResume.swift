import Foundation

// Preserve user-message ownership across an explicit model-switch restart.
// The continuation edge is consumed by the existing v0.2.10-style grouping.
enum RequestResume {
    static func observe(_ entry: Object,context: inout Object,activeTurn: String = "",preview: (Any)->String) -> Object? {
        let kind = entry.string("type"), payload = entry.object("payload"), event = payload.string("type")
        if kind == "session_meta" {
            context["history_start"] = payload["subagent_history_start_ordinal"]
            context["forked"] = !payload.string("forked_from_id").isEmpty
            context["fork_timestamp"] = entry["timestamp"]
            context["subagent"] = !payload.object("source").object("subagent").object("thread_spawn").isEmpty
            return nil
        }
        guard !context.flag("subagent") else { return nil }
        if let boundary = context.integer("history_start"), let ordinal = entry.integer("ordinal"), ordinal < boundary { return nil }
        if context.flag("forked"), entry.string("timestamp") < context.string("fork_timestamp") { return nil }

        let begins = kind == "turn_context" || (kind == "event_msg" && ["task_started","turn_started"].contains(event))
        var current = context.object("current")
        let id = begins ? payload.string("turn_id",payload.string("id")) : activeTurn
        if !id.isEmpty, current.string("id") != id {
            context["previous"] = current
            current = ["id":id]
        }
        let metadata = payload.object("internal_chat_message_metadata_passthrough")
        let owner = payload.string("turn_id",metadata.string("turn_id"))
        guard owner.isEmpty || owner == current.string("id") else { return nil }
        let item = payload.object("item")
        let content: Any? = kind == "response_item" && payload.string("role") == "user" ? payload["content"]
            : kind == "event_msg" && event == "user_message" ? payload["message"] ?? payload["content"]
            : kind == "event_msg" && ["user_message","userMessage","UserMessage"].contains(item.string("type")) ? item["content"] ?? item["message"] : nil
        if let content {
            let text = preview(content)
            if !text.isEmpty, !current.string("id").isEmpty {
                let inherited = current.flag("resumed")
                current["source"] = current["id"]; current["has_user"] = true; current["resumed"] = false
                context["input"] = ["turn":current.string("id"),"preview":text]
                context["current"] = current
                return inherited ? ["turn":current.string("id"),"clear":true] : nil
            }
        }
        if kind == "event_msg", ["task_complete","turn_complete","turn_aborted"].contains(event) {
            current["status"] = event == "turn_aborted" ? "aborted" : "completed"
            current["reason"] = payload["reason"]
        }
        context["current"] = current
        let markers = metadata["content_item_kinds"] as? [String] ?? []
        guard kind == "response_item", payload.string("role") == "developer",
              markers.contains("model_switch.instructions"), !current.flag("has_user"),
              !owner.isEmpty, owner == current.string("id") else { return nil }
        let previous = context.object("previous"), input = context.object("input")
        let source = input.string("turn"), text = input.string("preview")
        // A marker alone is not a predecessor ID. Require uninterrupted message
        // ownership from the preceding execution segment to the original input.
        guard !source.isEmpty, !text.isEmpty, previous.string("source") == source,
              previous.string("id") != current.string("id"), source != current.string("id") else { return nil }
        current["source"] = source; current["resumed"] = true; context["current"] = current
        var patch: Object = ["turn":current.string("id"),"source":source,"preview":text]
        if previous.string("status") == "aborted", previous.string("reason") == "interrupted" {
            patch["continuation"] = previous.string("id")
        }
        return patch
    }
}
