package backend

// Desktop and mobile share Mac's bounded explicit-member assembly, indexed
// source recovery and collector message ownership; no second rollout parser.
func (m *MobileHost) sourceDetail(request Row, recover bool) (Row, error) {
	return m.store.requestMessageDetail(request, recover)
}
