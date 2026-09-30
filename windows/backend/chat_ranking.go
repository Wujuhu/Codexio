package backend

import "sort"

// Mac UsageViews sorts the service's allowance values and expands only visible
// rows. The bridge keeps the same 25-row boundary rather than shipping history.
func (s *Service) GetChatRanking(metric string, page int) Row {
	if metric != "balance_usage_credits" {
		metric = "weekly_limit_percent"
	}
	s.requestAccountReports(false)
	reports := ValueRow(s.quota.Snapshot()["reports"])
	chats := ValueRow(reports["chats"])
	source := ValueRows(chats["threads"])
	sort.SliceStable(source, func(i, j int) bool {
		a, aok := ValueFloat(source[i][metric])
		b, bok := ValueFloat(source[j][metric])
		if aok != bok {
			return aok
		}
		return a > b
	})
	pages := max(1, (len(source)+24)/25)
	page = max(1, min(pages, page))
	begin := min(len(source), (page-1)*25)
	end := min(len(source), begin+25)
	result := make([]Row, 0, end-begin)
	for _, raw := range source[begin:end] {
		row := CloneRow(raw)
		id := ValueString(row["thread_id"])
		var title string
		_ = s.store.db.QueryRow("SELECT title FROM usage_session_titles WHERE session_id=?", id).Scan(&title)
		if title == "" {
			title = id
		}
		row["title"] = title
		result = append(result, row)
	}
	s.reportMu.Lock()
	busy := s.reportBusy
	s.reportMu.Unlock()
	return Row{"threads": result, "page": page, "pages": pages, "total": len(source), "data_as_of": chats["data_as_of"], "error": reports["error"], "loading": busy}
}
