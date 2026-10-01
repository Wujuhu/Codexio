package backend

import (
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"net"
	"time"
	"unicode/utf8"
)

func mobileFrame(c net.Conn, opcode byte, payload []byte) error {
	if len(payload) > mobileLimit {
		return errors.New("sync response exceeds capacity")
	}
	head := []byte{0x80 | opcode}
	switch {
	case len(payload) < 126:
		head = append(head, byte(len(payload)))
	case len(payload) < 65536:
		head = append(head, 126, byte(len(payload)>>8), byte(len(payload)))
	default:
		head = append(head, 127)
		var b [8]byte
		binary.BigEndian.PutUint64(b[:], uint64(len(payload)))
		head = append(head, b[:]...)
	}
	for _, b := range [][]byte{head, payload} {
		for len(b) > 0 {
			n, e := c.Write(b)
			if e != nil {
				return e
			}
			if n == 0 {
				return io.ErrUnexpectedEOF
			}
			b = b[n:]
		}
	}
	return nil
}
func mobileReceive(c net.Conn) (Row, error) {
	for {
		var head [2]byte
		if _, e := io.ReadFull(c, head[:]); e != nil {
			return nil, e
		}
		opcode := head[0] & 15
		masked := head[1]&128 != 0
		n := uint64(head[1] & 127)
		// The phone is the RFC6455 client: FIN, zero RSV, client masking and
		// canonical length encodings are required; fragmented messages are rejected.
		if head[0]&128 == 0 || head[0]&112 != 0 || !masked {
			return nil, errors.New("invalid sync frame")
		}
		switch n {
		case 126:
			var b [2]byte
			if _, e := io.ReadFull(c, b[:]); e != nil {
				return nil, e
			}
			n = uint64(binary.BigEndian.Uint16(b[:]))
			if n < 126 {
				return nil, errors.New("noncanonical frame length")
			}
		case 127:
			var b [8]byte
			if _, e := io.ReadFull(c, b[:]); e != nil {
				return nil, e
			}
			n = binary.BigEndian.Uint64(b[:])
			if n < 65536 || n>>63 != 0 {
				return nil, errors.New("noncanonical frame length")
			}
		}
		if n > mobileLimit || opcode >= 8 && n > 125 {
			return nil, errors.New("sync frame exceeds capacity")
		}
		var mask [4]byte
		if _, e := io.ReadFull(c, mask[:]); e != nil {
			return nil, e
		}
		b := make([]byte, int(n))
		if _, e := io.ReadFull(c, b); e != nil {
			return nil, e
		}
		for i := range b {
			b[i] ^= mask[i%4]
		}
		switch opcode {
		case 8:
			if len(b) == 1 || len(b) > 2 && !utf8.Valid(b[2:]) {
				return nil, errors.New("invalid close frame")
			}
			_ = mobileFrame(c, 8, b)
			return nil, io.EOF
		case 9:
			if e := mobileFrame(c, 10, b); e != nil {
				return nil, e
			}
			continue
		case 10:
			continue
		case 1, 2:
			if !utf8.Valid(b) || !json.Valid(b) || len(b) == 0 || b[0] != '{' {
				return nil, errors.New("invalid sync UTF-8")
			}
			r, e := DecodeRow(b)
			if e != nil {
				return nil, errors.New("invalid sync message")
			}
			return r, nil
		default:
			return nil, errors.New("unsupported sync opcode")
		}
	}
}
func (m *MobileHost) serve(ctx context.Context, listener net.Listener) {
	for {
		c, e := listener.Accept()
		if e != nil {
			return
		}
		select {
		case m.clients <- struct{}{}:
		case <-ctx.Done():
			c.Close()
			return
		default:
			c.Close()
			continue
		}
		m.mu.Lock()
		if !m.enabled || m.listener != listener {
			m.mu.Unlock()
			c.Close()
			<-m.clients
			return
		}
		m.connections[c] = true
		m.mu.Unlock()
		go m.client(ctx, c)
	}
}
func (m *MobileHost) client(ctx context.Context, c net.Conn) {
	defer func() { c.Close(); m.mu.Lock(); delete(m.connections, c); m.mu.Unlock(); <-m.clients }()
	authenticated := false
	readerID := ""
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		if authenticated {
			_ = c.SetReadDeadline(time.Time{})
		} else {
			_ = c.SetReadDeadline(time.Now().Add(10 * time.Second))
		}
		message, e := mobileReceive(c)
		if e != nil {
			return
		}
		if ValueString(message["action"]) == "ack" && authenticated && readerID != "" {
			m.mu.Lock()
			known := false
			for _, reader := range ValueRows(m.host["readers"]) {
				known = known || ValueString(reader["id"]) == readerID
			}
			changed := known && m.recordSyncLocked("lan", readerID)
			m.mu.Unlock()
			if changed {
				m.notify()
			}
			continue
		}
		r := m.message(message)
		if r == nil {
			continue
		}
		if action := ValueString(r["action"]); action == "sync" || action == "detail" || action == "image" {
			authenticated = true
			readerID = ValueString(ValueRow(message["reader"])["id"])
		}
		_ = c.SetWriteDeadline(time.Now().Add(10 * time.Second))
		raw, e := mobileJSON(r)
		if e != nil || mobileFrame(c, 1, raw) != nil {
			return
		}
		_ = c.SetWriteDeadline(time.Time{})
	}
}
func (m *MobileHost) message(message Row) Row {
	action := ValueString(message["action"])
	if action == "ack" {
		return nil
	}
	reader := ValueRow(message["reader"])
	_, readerIDString := reader["id"].(string)
	_, readerNameString := reader["name"].(string)
	_, localSecretString := reader["localSecret"].(string)
	_, cloudSecretString := reader["cloudSecret"].(string)
	m.mu.Lock()
	m.expireTicketLocked()
	var knownReader Row
	for _, r := range ValueRows(m.host["readers"]) {
		if readerIDString && localSecretString && ValueString(r["id"]) == ValueString(reader["id"]) && mobileSecretPattern.MatchString(ValueString(reader["localSecret"])) && mobileEqual(ValueString(r["localSecret"]), ValueString(reader["localSecret"])) {
			knownReader = r
			break
		}
	}
	if knownReader != nil {
		m.retainContentLocked()
		name := ValueString(reader["name"])
		if readerNameString && name != "" && len([]rune(name)) <= 40 && name != ValueString(knownReader["name"]) {
			knownReader["name"] = name
			if e := m.saveLocked(); e != nil {
				m.lastError = e.Error()
			}
			go m.notify()
		}
		if action == "detail" {
			m.mu.Unlock()
			return m.detailMessage(message)
		}
		if action == "image" {
			ctx := m.workerCtx
			m.mu.Unlock()
			return m.imageMessage(ctx, message)
		}
		// iOS keeps sending pair until the first sync response confirms desktop
		// approval. Mac authenticates this retry and returns the initial datasets.
		known := ValueRow(message["known"])
		envelopes := []Row{}
		revisions := Row{}
		for _, name := range []string{"live", "recent", "trends"} {
			if e := m.datasets[name]; e != nil {
				revisions[name] = e["revision"]
				if ValueInt(e["revision"]) > ValueInt(known[name]) {
					envelopes = append(envelopes, CloneRow(e))
				}
			}
		}
		versions := Row{}
		for id, d := range m.details {
			if d.Expires > float64(time.Now().Unix()) {
				versions[id] = d.Envelope["revision"]
			}
		}
		var cloud any
		if ValueBool(m.host["cloud_enabled"]) && m.cloudReaders[ValueString(reader["id"])] {
			cloud = mobileOrigin
		}
		m.mu.Unlock()
		return Row{"action": "sync", "known": revisions, "datasets": envelopes, "seen": float64(time.Now().UnixMilli()) / 1000, "supportsAck": true, "cloud": cloud, "capabilities": []string{"request-kinds-v1", "request-details-v1", "request-images-v1"}, "detailVersions": versions}
	}
	if action == "pair" && readerIDString && readerNameString && localSecretString && cloudSecretString && m.ticket != nil && mobileEqual(ValueString(message["ticket"]), ValueString(m.ticket["ticket"])) && mobileUUID.MatchString(ValueString(reader["id"])) && len([]rune(ValueString(reader["name"]))) <= 40 && mobileSecretPattern.MatchString(ValueString(reader["localSecret"])) && mobileSecretPattern.MatchString(ValueString(reader["cloudSecret"])) && (m.pending == nil || ValueString(m.pending["id"]) == ValueString(reader["id"])) {
		// Copy only protocol credentials, so a phone cannot add arbitrary fields to
		// the persistent host identity. Approval is an explicit desktop action.
		m.pending = Row{"id": reader["id"], "name": reader["name"], "localSecret": reader["localSecret"], "cloudSecret": reader["cloudSecret"]}
		m.mu.Unlock()
		m.notify()
		return Row{"action": "pending"}
	}
	m.mu.Unlock()
	if action == "sync" || action == "detail" || action == "image" {
		return Row{"action": "revoked", "error": "REVOKED"}
	}
	return Row{"action": "error", "error": "配对已过期或被拒绝"}
}
func (m *MobileHost) detailMessage(message Row) Row {
	id := ValueString(message["detailID"])
	result := Row{"action": "detail", "detailID": id}
	if !mobileIDPattern.MatchString(id) {
		result["error"] = "INVALID"
		return result
	}
	detail, e := m.getDetailWithForce(id, true, ValueBool(message["force"]) && ValueInt(message["detailPart"]) == 0)
	if e != nil || detail == nil {
		result["error"] = "DETAIL_UNAVAILABLE"
		return result
	}
	raw := []byte(ValueString(detail["payload"]))
	source, e := DecodeRow(raw)
	if e != nil {
		result["error"] = "DETAIL_UNAVAILABLE"
		return result
	}
	if ValueBool(message["full"]) {
		result["full"] = true
		if ValueString(source["availability"]) == "capacity" {
			result["error"] = "DETAIL_CAPACITY"
			return result
		}
		parts := (len(raw) + mobileDetailChunk - 1) / mobileDetailChunk
		index := ValueInt(message["detailPart"])
		if v, ok := message["detailPart"]; ok {
			if n, good := ValueFloat(v); !good || float64(index) != n {
				result["error"] = "INVALID"
				return result
			}
		}
		if index < 0 || index >= int64(parts) {
			result["error"] = "INVALID"
			return result
		}
		start := int(index) * mobileDetailChunk
		end := min(len(raw), start+mobileDetailChunk)
		result["detailPart"] = index
		result["detailManifest"] = Row{"id": id, "revision": detail["revision"], "digest": detail["digest"], "bytes": len(raw), "parts": parts}
		result["detailChunk"] = base64.StdEncoding.EncodeToString(raw[start:end])
		return result
	}
	source["user"] = mobilePrefix(ValueString(source["user"]), 1200)
	source["final"] = mobilePrefix(ValueString(source["final"]), 5000)
	for _, a := range ValueRows(source["attachments"]) {
		a["thumbnail"] = nil
	}
	source["full"] = false
	preview, _ := mobileJSON(source)
	result["full"] = false
	result["detail"] = Row{"dataset": detail["dataset"], "revision": detail["revision"], "digest": HashString(string(preview)), "payload": string(preview)}
	return result
}
