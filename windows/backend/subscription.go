package backend

import "sort"

// The official service reports allowance percentages, not dollar costs.
func (s *Service) projectAccountReports(snapshot Row) {
	reports := ValueRow(snapshot["reports"])
	plan := ValueRow(reports["plan"])
	cycles := []Row{}
	for _, p := range ValueRows(plan["periods"]) {
		if ValueInt(p["window_minutes"]) != 10080 {
			continue
		}
		used, uok := ValueFloat(p["used_basis_points"])
		var percent any
		if uok {
			percent = used / 100
		}
		models := []Row{}
		for _, breakdown := range ValueRows(p["breakdowns"]) {
			if ValueString(breakdown["dimension"]) != "model" {
				continue
			}
			for _, m := range ValueRows(breakdown["rows"]) {
				var portion any
				if n, ok := ValueFloat(m["basis_points"]); ok {
					portion = n / 100
				}
				models = append(models, Row{"model": m["key"], "used_percent": portion})
			}
		}
		cycles = append(cycles, Row{"id": p["id"], "start": p["starts_at"], "end": p["ends_at"], "used_percent": percent, "models": models, "data_as_of": plan["data_as_of"], "approximate": plan["approximate"] != false, "accounting_complete": p["accounting_complete"], "coverage_complete": plan["coverage_complete"]})
	}
	sort.SliceStable(cycles, func(i, j int) bool { return ValueString(cycles[i]["start"]) > ValueString(cycles[j]["start"]) })
	snapshot["cycles"] = cycles
	chats := ValueRow(reports["chats"])
	official := []Row{}
	for index, thread := range ValueRows(chats["threads"]) {
		if index == 25 {
			break
		}
		id := ValueString(thread["thread_id"])
		var title string
		_ = s.store.db.QueryRow("SELECT title FROM usage_session_titles WHERE session_id=?", id).Scan(&title)
		if title == "" {
			title = id
		}
		row := CloneRow(thread)
		row["title"] = title
		row["used_percent"] = thread["weekly_limit_percent"]
		row["data_as_of"] = chats["data_as_of"]
		official = append(official, row)
	}
	sort.SliceStable(official, func(i, j int) bool {
		a, aok := ValueFloat(official[i]["used_percent"])
		b, bok := ValueFloat(official[j]["used_percent"])
		if aok != bok {
			return aok
		}
		return a > b
	})
	snapshot["official_reports"] = official
}
