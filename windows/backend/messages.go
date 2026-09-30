package backend

import (
	"bufio"
	"database/sql"
	"io"
	"os"
	"strings"
)

// Evicted full messages can be recovered only from a verified original source.
// The lookup is on demand, bounded, and never mistakes a preview for full text.
func (s *Store) sourceMessage(request Row) Row {
	session, turn := dataString(request, "session_id"), dataString(request, "turn_id")
	if session == "" || turn == "" || strings.HasPrefix(turn, "legacy-user:") {
		return nil
	}
	rows, e := s.db.Query(`SELECT DISTINCT json_extract(c.data,'$.path') FROM usage_cursors c JOIN usage_origins o ON c.key=o.file_key WHERE o.kind='turn' AND o.item_id IN(SELECT id FROM usage_turns WHERE json_extract(data,'$.session_id')=? AND json_extract(data,'$.turn_id')=?) LIMIT 4`, session, turn)
	if e != nil {
		return nil
	}
	paths := []string{}
	for rows.Next() {
		var path sql.NullString
		if rows.Scan(&path) == nil && path.Valid {
			paths = append(paths, path.String)
		}
	}
	rows.Close()
	for _, path := range paths {
		if result := readSourceMessage(path, session, turn); len(result) > 0 {
			return result
		}
	}
	return nil
}
func readSourceMessage(path, session, target string) Row {
	f, e := os.Open(path)
	if e != nil {
		return nil
	}
	defer f.Close()
	scan := bufio.NewScanner(io.LimitReader(f, 128<<20))
	scan.Buffer(make([]byte, 65536), 8<<20)
	current := ""
	owner := ""
	var pending any
	found := false
	result := Row{"attachments": []Row{}, "availability": "source", "user_complete": false, "final_complete": false}
	for scan.Scan() {
		line := scan.Bytes()
		if !strings.Contains(string(line), "message") && !strings.Contains(string(line), "turn_context") && !strings.Contains(string(line), "task_started") && !strings.Contains(string(line), "session_meta") && !strings.Contains(string(line), "turn_started") {
			continue
		}
		entry, e := DecodeRow(line)
		if e != nil {
			continue
		}
		p := ValueRow(entry["payload"])
		kind, sub := dataString(entry, "type"), dataString(p, "type")
		if kind == "session_meta" {
			owner = firstString(p["id"], p["thread_id"])
			if owner != session {
				return nil
			}
		}
		explicit := ""
		if kind == "turn_context" {
			explicit = firstString(p["turn_id"], p["id"])
		} else if sub == "task_started" || sub == "turn_started" {
			explicit = dataString(p, "turn_id")
		}
		if explicit != "" {
			if found && explicit != target {
				break
			}
			current = explicit
			if current == target {
				found = true
				if pending != nil {
					text, attachments, complete := visibleMessage(pending, true)
					result["user"] = text
					result["attachments"] = attachments
					result["user_complete"] = complete
				}
			}
			pending = nil
		}
		var input any
		if kind == "response_item" && dataString(p, "role") == "user" {
			input = p["content"]
		} else if sub == "user_message" {
			input = p["message"]
			if input == nil {
				input = p["content"]
			}
		}
		if input != nil {
			if current != target {
				pending = input
			} else {
				text, attachments, complete := visibleMessage(input, true)
				if text != "" || len(attachments) > 0 {
					if dataString(result, "user") == "" {
						result["user"] = text
						result["user_complete"] = complete
						result["attachments"] = attachments
					}
				}
			}
		}
		if current != target || owner != session {
			continue
		}
		var final any
		if sub == "task_complete" || sub == "turn_complete" {
			final = p["last_agent_message"]
		} else if kind == "response_item" && sub == "message" && dataString(p, "role") == "assistant" && (dataString(p, "channel") == "final" || dataString(p, "phase") == "final" || dataString(p, "phase") == "final_answer") {
			final = p["content"]
		}
		if final != nil {
			text, _, complete := visibleMessage(final, false)
			if text != "" {
				result["final"] = text
				result["final_complete"] = complete
			}
		}
	}
	if scan.Err() != nil {
		return nil
	}
	if dataString(result, "user") == "" && dataString(result, "final") == "" {
		return nil
	}
	return result
}
