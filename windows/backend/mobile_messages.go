package backend

// Desktop and mobile share Mac's bounded explicit-member assembly, indexed
// source recovery and collector message ownership; no second rollout parser.
func (m *MobileHost) sourceDetail(request Row, recover bool) (Row, error) {
	if normalizedClient(dataString(request, "client_kind")) == "claude_code" {
		detail, err := m.store.claudeDetail(request, false, 1)
		if err != nil {
			return nil, err
		}
		attachments := append([]Row{}, ValueRows(detail["attachments"])...)
		for _, image := range ValueRows(detail["images"]) {
			item := CloneRow(image)
			if raw, e := m.store.claudeImage(m.workerCtx, dataString(request, "id"), dataString(image, "id")); e == nil {
				item["references"] = []string{"codexio-image://" + dataString(image, "id")}
				if path, err := m.store.media.materialize(dataString(raw, "dataURL")); err == nil {
					item["path"] = path
				}
			}
			attachments = append(attachments, item)
		}
		detail["attachments"] = attachments
		return detail, nil
	}
	return m.store.requestMessageDetail(request, recover)
}
