package backend

// Message extraction and ownership follow Mac v0.3.5 RequestMedia.swift and
// Database.RequestMessageText. Raw image bytes never enter ledger/cursor rows.
import (
	"fmt"
	"html"
	"net/url"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"
)

const requestMediaSchema = 1
const requestMediaLimit = 32

var requestMediaExtensions = map[string]string{"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "image/heic": "heic", "image/heif": "heif", "image/avif": "avif", "image/tiff": "tiff", "image/bmp": "bmp", "image/svg+xml": "svg"}
var requestMediaHTML = regexp.MustCompile(`(?is)<img\b[^>]*>`)
var requestMediaXML = regexp.MustCompile(`(?is)<image\b[^>]*(?:/>|>.*?</image\s*>|>)`)
var requestMediaAttribute = regexp.MustCompile(`(?is)\b(src|path|file_path|url|alt)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))`)
var requestMediaWrapper = regexp.MustCompile(`(?m)^## ([^\r\n]+?):[ \t]+([^\r\n]+)$`)
var requestMediaDataURI = regexp.MustCompile(`(?i)data:image/[a-z0-9.+-]+;base64,[a-z0-9+/=\r\n]+`)
var requestMediaUnescape = regexp.MustCompile(`\\([\\()\[\] <>])`)
var requestUserContextTags = []string{"recommended_plugins", "environment_context", "permissions instructions", "permissions", "INSTRUCTIONS", "user_instructions", "developer_instructions", "skills_instructions", "skill_instructions", "system", "developer", "system-reminder", "app-context", "collaboration_mode", "multi_agent_role", "multi_agent_mode"}

type requestImageMatch struct {
	start, end int
	source     string
	name       string
}

// Mask Markdown code before looking for image syntax. Escaped image markers
// and fenced/inline examples stay literal text, including unclosed fences.
func requestCodeMask(text string) []bool {
	mask := make([]bool, len(text))
	fence, length := byte(0), 0
	for start := 0; start < len(text); {
		end := strings.IndexByte(text[start:], '\n')
		if end < 0 {
			end = len(text)
		} else {
			end += start + 1
		}
		line := text[start:end]
		trim := strings.TrimLeft(line, " ")
		indent := len(line) - len(trim)
		run := 0
		if indent <= 3 && len(trim) > 0 && (trim[0] == '`' || trim[0] == '~') {
			for run < len(trim) && trim[run] == trim[0] {
				run++
			}
		}
		marked := fence != 0 || run >= 3
		if fence == 0 && run >= 3 {
			fence, length = trim[0], run
		} else if fence != 0 && run >= length && trim[0] == fence && strings.TrimSpace(trim[run:]) == "" {
			fence, length = 0, 0
		}
		if marked || indent >= 4 || strings.HasPrefix(line, "\t") {
			for i := start; i < end; i++ {
				mask[i] = true
			}
		}
		start = end
	}
	for i := 0; i < len(text); i++ {
		if mask[i] || text[i] != '`' || requestEscaped(text, i) {
			continue
		}
		end := i
		for end < len(text) && text[end] == '`' {
			end++
		}
		delimiter := text[i:end]
		for cursor := end; cursor < len(text); {
			rel := strings.Index(text[cursor:], delimiter)
			if rel < 0 {
				break
			}
			at := cursor + rel
			stop := at + len(delimiter)
			if !mask[at] && (at == 0 || text[at-1] != '`') && (stop == len(text) || text[stop] != '`') {
				for p := i; p < stop; p++ {
					mask[p] = true
				}
				i = stop - 1
				break
			}
			cursor = stop
		}
	}
	return mask
}

func requestEscaped(text string, at int) bool {
	n := 0
	for at > 0 && text[at-1] == '\\' {
		n++
		at--
	}
	return n%2 == 1
}

func requestImageMatches(text string) []requestImageMatch {
	mask := requestCodeMask(text)
	matches := []requestImageMatch{}
	for start := 0; start+2 < len(text) && len(matches) < 128; start++ {
		if text[start] != '!' || text[start+1] != '[' || mask[start] || requestEscaped(text, start) {
			continue
		}
		labelEnd := start + 2
		for labelEnd < len(text) && (text[labelEnd] != ']' || requestEscaped(text, labelEnd)) && text[labelEnd] != '\n' {
			labelEnd++
		}
		if labelEnd+1 >= len(text) || text[labelEnd+1] != '(' {
			continue
		}
		at := labelEnd + 2
		for at < len(text) && (text[at] == ' ' || text[at] == '\t') {
			at++
		}
		sourceStart, sourceEnd, end := at, at, at
		if at < len(text) && text[at] == '<' {
			sourceStart++
			at++
			for at < len(text) && text[at] != '>' && text[at] != '\n' {
				at++
			}
			if at >= len(text) || text[at] != '>' {
				continue
			}
			sourceEnd, end = at, at+1
		} else {
			depth := 0
			for at < len(text) {
				ch := text[at]
				if ch == '\n' || ch == '\r' || depth == 0 && (ch == ')' || ch == ' ' || ch == '\t') {
					break
				}
				if ch == '\\' && at+1 < len(text) {
					at += 2
					continue
				}
				if ch == '(' {
					depth++
				} else if ch == ')' {
					depth--
				}
				if depth > 8 {
					break
				}
				at++
			}
			if depth != 0 {
				continue
			}
			sourceEnd, end = at, at
		}
		for end < len(text) && (text[end] == ' ' || text[end] == '\t') {
			end++
		}
		if end < len(text) && (text[end] == '"' || text[end] == '\'' || text[end] == '(') {
			quote := text[end]
			if quote == '(' {
				quote = ')'
			}
			end++
			for end < len(text) && text[end] != '\n' && (text[end] != quote || requestEscaped(text, end)) {
				end++
			}
			if end < len(text) && text[end] == quote {
				end++
			}
			for end < len(text) && (text[end] == ' ' || text[end] == '\t') {
				end++
			}
		}
		if sourceEnd > sourceStart && end < len(text) && text[end] == ')' {
			matches = append(matches, requestImageMatch{start, end + 1, text[sourceStart:sourceEnd], text[start+2 : labelEnd]})
			start = end
		}
	}
	for _, indices := range requestMediaHTML.FindAllStringIndex(text, 128) {
		if mask[indices[0]] || requestEscaped(text, indices[0]) {
			continue
		}
		attrs := requestImageAttributes(text[indices[0]:indices[1]])
		if attrs["src"] != "" {
			matches = append(matches, requestImageMatch{indices[0], indices[1], attrs["src"], attrs["alt"]})
		}
	}
	sort.SliceStable(matches, func(i, j int) bool {
		if matches[i].start == matches[j].start {
			return matches[i].end > matches[j].end
		}
		return matches[i].start < matches[j].start
	})
	unique := []requestImageMatch{}
	for _, match := range matches {
		if len(unique) == 0 || match.start >= unique[len(unique)-1].end {
			unique = append(unique, match)
		}
	}
	return unique
}

func requestImageAttributes(tag string) map[string]string {
	attrs := map[string]string{}
	for _, match := range requestMediaAttribute.FindAllStringSubmatch(tag, 16) {
		attrs[strings.ToLower(match[1])] = firstString(match[2], match[3], match[4])
	}
	return attrs
}

func requestDecodedSource(source string) string {
	value := strings.Trim(strings.TrimSpace(source), "<>`")
	if len(value) >= 2 && (value[0] == '"' && value[len(value)-1] == '"' || value[0] == '\'' && value[len(value)-1] == '\'') {
		value = value[1 : len(value)-1]
	}
	return html.UnescapeString(requestMediaUnescape.ReplaceAllString(value, "$1"))
}

func requestLocalPath(source, cwd string) string {
	if source == "" || len(source) > 4096 || strings.ContainsRune(source, 0) {
		return ""
	}
	if strings.HasPrefix(source, `\\`) || strings.HasPrefix(source, "//") {
		return "" // Never turn a source reference into an authenticated SMB read.
	}
	if u, e := url.Parse(source); e == nil && u.Scheme != "" && !(len(u.Scheme) == 1 && len(source) > 2 && (source[2] == '\\' || source[2] == '/')) {
		if strings.ToLower(u.Scheme) != "file" || u.Host != "" && u.Host != "localhost" {
			return ""
		}
		source = u.Path
		if len(source) > 2 && source[0] == '/' && source[2] == ':' {
			source = source[1:]
		}
	} else if decoded, err := url.PathUnescape(source); err == nil {
		source = decoded
	}
	if filepath.IsAbs(source) {
		return filepath.Clean(source)
	}
	if strings.HasPrefix(source, "/") || strings.HasPrefix(source, `\`) || !filepath.IsAbs(cwd) {
		return ""
	}
	return filepath.Clean(filepath.Join(cwd, source))
}

func requestComparableSource(source string) string {
	decoded := requestDecodedSource(source)
	if path := requestLocalPath(decoded, ""); path != "" {
		return strings.ToLower(path)
	}
	return decoded
}

func requestImageMIME(source string) string {
	if u, e := url.Parse(source); e == nil && u.Scheme != "" && len(u.Scheme) != 1 {
		source = u.Path
	}
	ext := strings.TrimPrefix(strings.ToLower(filepath.Ext(source)), ".")
	if ext == "jpeg" {
		ext = "jpg"
	}
	if ext == "tif" {
		ext = "tiff"
	}
	for mime, suffix := range requestMediaExtensions {
		if ext == suffix {
			return mime
		}
	}
	return "application/octet-stream"
}

func requestImagePart(part Row) bool {
	switch strings.ToLower(strings.ReplaceAll(dataString(part, "type"), "_", "")) {
	case "image", "inputimage", "outputimage", "imageurl", "localimage":
		return true
	}
	return false
}

func requestMediaContent(raw any, metadata Row) []Row {
	parts := []Row{}
	appendPart := func(value any) {
		if str, ok := value.(string); ok {
			parts = append(parts, Row{"type": "text", "text": str})
		} else if row := ValueRow(value); len(row) > 0 {
			parts = append(parts, row)
		}
	}
	switch values := raw.(type) {
	case []any:
		for _, value := range values[:min(256, len(values))] {
			appendPart(value)
		}
	case []Row:
		parts = append(parts, values[:min(256, len(values))]...)
	default:
		appendPart(raw)
	}
	for _, key := range []string{"images", "local_images"} {
		var values []any
		switch source := metadata[key].(type) {
		case []any:
			values = source
		case []string:
			for _, value := range source {
				values = append(values, value)
			}
		case []Row:
			for _, value := range source {
				values = append(values, value)
			}
		}
		for _, value := range values[:min(requestMediaLimit, len(values))] {
			if str, ok := value.(string); ok {
				parts = append(parts, Row{"type": "image", "image_url": str})
			} else if row := ValueRow(value); len(row) > 0 {
				row = CloneRow(row)
				if dataString(row, "type") == "" {
					row["type"] = "image"
				}
				parts = append(parts, row)
			}
		}
	}
	return parts[:min(256, len(parts))]
}

func requestUserFiles(text string) []Row {
	files := []Row{}
	mask := requestCodeMask(text)
	if start := strings.Index(text, "# Files mentioned by the user:"); start >= 0 && !mask[start] {
		section := text[start:]
		if end := strings.Index(section, "## My request"); end >= 0 {
			section = section[:end]
		}
		for _, match := range requestMediaWrapper.FindAllStringSubmatch(section, requestMediaLimit) {
			files = append(files, Row{"type": "file", "name": match[1], "path": match[2]})
		}
	}
	for _, span := range requestMediaXML.FindAllStringIndex(text, requestMediaLimit) {
		if mask[span[0]] {
			continue
		}
		tag := text[span[0]:span[1]]
		attrs := requestImageAttributes(tag)
		source := firstString(attrs["src"], attrs["path"], attrs["file_path"], attrs["url"])
		if source == "" {
			open, end := strings.IndexByte(tag, '>'), strings.Index(strings.ToLower(tag), "</image")
			if open >= 0 && end > open {
				source = strings.TrimSpace(tag[open+1 : end])
				for _, name := range []string{"path", "url"} {
					if strings.HasPrefix(source, "<"+name+">") && strings.HasSuffix(source, "</"+name+">") {
						source = strings.TrimSuffix(strings.TrimPrefix(source, "<"+name+">"), "</"+name+">")
					}
				}
			}
		}
		if source != "" {
			files = append(files, Row{"type": "image", "path": source})
		}
	}
	return files[:min(requestMediaLimit, len(files))]
}

func requestUserTextTransform(text string, transform func(string) string) string {
	// Literal examples stay text while control/image wrappers outside code are
	// interpreted. This helper only transforms strings; it never reads images.
	mask := requestCodeMask(text)
	type saved struct{ token, text string }
	blocks := []saved{}
	var out strings.Builder
	for at := 0; at < len(text); {
		if !mask[at] {
			out.WriteByte(text[at])
			at++
			continue
		}
		end := at + 1
		for end < len(text) && mask[end] {
			end++
		}
		token := fmt.Sprintf("\x00codexio-code-%d\x00", len(blocks))
		blocks = append(blocks, saved{token, text[at:end]})
		out.WriteString(token)
		at = end
	}
	result := transform(out.String())
	for _, block := range blocks {
		result = strings.ReplaceAll(result, block.token, block.text)
	}
	return result
}

func requestUserBody(text string) string {
	return requestUserTextTransform(text, userText)
}

func requestUserMediaText(text string) string {
	return requestUserTextTransform(text, func(value string) string {
		if strings.Contains(value, "<send_user_message_question_reply>") {
			return ""
		}
		value = externalAppInputPattern.ReplaceAllString(value, " ")
		if strings.HasPrefix(strings.ToLower(strings.TrimSpace(value)), "<external_codex_apps_") {
			return ""
		}
		// Strip context before looking for file or image wrappers. Otherwise an
		// image example inside environment/instruction metadata becomes input.
		for _, tag := range requestUserContextTags {
			for {
				lower := strings.ToLower(value)
				start := strings.Index(lower, "<"+strings.ToLower(tag))
				if start < 0 {
					break
				}
				end := strings.Index(lower[start:], "</"+strings.ToLower(tag)+">")
				if end < 0 {
					value = value[:start]
					break
				}
				value = value[:start] + value[start+end+len(tag)+3:]
			}
		}
		if strings.HasPrefix(strings.TrimSpace(value), "# AGENTS.md instructions") && !strings.Contains(value, "## My request") && !strings.Contains(value, "<user_request>") {
			return ""
		}
		return value
	})
}

func requestMediaText(parts []Row) (string, bool) {
	texts := []string{}
	for _, part := range parts {
		switch strings.ToLower(strings.ReplaceAll(dataString(part, "type"), "_", "")) {
		case "text", "inputtext", "outputtext":
			texts = append(texts, dataString(part, "text"))
		}
	}
	return requestBoundedText(strings.Join(texts, "\n"), 16_000_000)
}

// Ingestion, entry classification and interrupted-turn tracking share this
// no-I/O gate. Detect XML/Markdown/HTML and typed image/file content before
// userText removes wrappers; return only tiny presence markers for previews.
func requestUserInput(content any) (string, []Row, bool) {
	parts := requestMediaContent(content, nil)
	text, complete := requestMediaText(parts)
	if strings.Contains(text, "<send_user_message_question_reply>") {
		return "", nil, true
	}
	text = requestUserMediaText(text)
	extra := requestUserFiles(text)
	if strings.Contains(text, "# Files mentioned by the user:") && !strings.Contains(text, "## My request") && !strings.Contains(text, "<user_request>") {
		complete = false
	}
	text = requestUserBody(text)
	attachments := []Row{}
	appendMarker := func(name, mime string) {
		if len(attachments) < requestMediaLimit {
			attachments = append(attachments, Row{"name": clip(firstString(name, "附件"), 255), "mime": mime})
		} else {
			complete = false
		}
	}
	for _, part := range append(parts, extra...) {
		if requestImagePart(part) {
			appendMarker(firstString(part["name"], part["filename"]), "image/unknown")
		} else if kind := strings.ToLower(strings.ReplaceAll(dataString(part, "type"), "_", "")); kind == "file" || kind == "inputfile" {
			appendMarker(firstString(part["name"], part["filename"]), "application/octet-stream")
		}
	}
	for _, match := range requestImageMatches(text) {
		appendMarker(match.name, "image/unknown")
	}
	text, fits := requestBoundedText(strings.TrimSpace(text), 1<<20)
	return text, attachments, complete && fits
}

func requestBoundedText(text string, limit int) (string, bool) {
	if len(text) <= limit {
		return text, true
	}
	for limit > 0 && !utf8.RuneStart(text[limit]) {
		limit--
	}
	return text[:limit], false
}

func (s *Store) extractRequestMessage(content any, user bool, cwd string, created float64) (string, []Row, bool) {
	parts := requestMediaContent(content, nil)
	text, complete := requestMediaText(parts)
	extra := []Row{}
	placement := "final"
	if user {
		if strings.Contains(text, "<send_user_message_question_reply>") {
			return "", nil, true
		}
		placement = "user"
		text = requestUserMediaText(text)
		extra = requestUserFiles(text)
		if strings.Contains(text, "# Files mentioned by the user:") && !strings.Contains(text, "## My request") && !strings.Contains(text, "<user_request>") {
			complete = false
		}
		text = requestUserBody(text)
	}
	attachments := []Row{}
	add := func(source, name, mime string, image bool, reference string) Row {
		if len(attachments) >= 128 {
			complete = false
			return nil
		}
		source = requestDecodedSource(source)
		if strings.HasPrefix(strings.ToLower(source), "data:image/") {
			if path, err := s.media.materialize(source); err == nil {
				uriPath := filepath.ToSlash(path)
				if len(uriPath) > 1 && uriPath[1] == ':' {
					uriPath = "/" + uriPath
				}
				source = (&url.URL{Scheme: "file", Path: uriPath}).String()
			} else {
				source = "codexio-image-unavailable://" + HashString(source)
				complete = false
			}
			if reference != "" {
				reference = source
			}
		}
		path := requestLocalPath(source, cwd)
		normalized := source
		if path != "" {
			normalized = strings.ToLower(path)
		}
		if normalized == "" {
			normalized = fmt.Sprintf("unavailable|%s|%d", placement, len(attachments))
		}
		sourceKey := HashString(normalized)
		if name == "" && !strings.Contains(source, "-unavailable://") {
			if path != "" {
				name = filepath.Base(path)
			} else if u, e := url.Parse(source); e == nil {
				name = filepath.Base(u.Path)
			}
		}
		if name == "" || name == "." || name == "/" {
			if image {
				name = "图片"
			} else {
				name = "附件"
			}
		}
		if !strings.HasPrefix(mime, "image/") {
			mime = requestImageMIME(firstString(path, source, name))
			if mime == "application/octet-stream" && image {
				mime = "image/unknown"
			}
		}
		value := Row{"id": HashString(placement + "|" + sourceKey), "name": clip(name, 255), "path": path, "mime": mime, "placement": placement, "source_key": sourceKey}
		if reference == "" && strings.HasPrefix(mime, "image/") {
			reference = source
		}
		if reference != "" && len(reference) <= 4096 {
			value["reference"], value["references"] = reference, []string{reference}
		}
		if created > 0 {
			value["created_at"] = created
		}
		attachments = append(attachments, value)
		return value
	}
	matches := requestImageMatches(text)
	for i := len(matches) - 1; i >= 0; i-- {
		match := matches[i]
		item := add(match.source, match.name, "", true, match.source)
		if ref := dataString(item, "reference"); ref != "" && ref != match.source {
			text = text[:match.start] + requestImageMarkdown(firstString(match.name, item["name"]), ref) + text[match.end:]
		}
	}
	// Keep inline references first when the same source also occurs in a wrapper.
	for i, j := 0, len(attachments)-1; i < j; i, j = i+1, j-1 {
		attachments[i], attachments[j] = attachments[j], attachments[i]
	}
	for _, part := range append(parts, extra...) {
		isImage := requestImagePart(part)
		kind := dataString(part, "type")
		if !isImage && kind != "file" && kind != "input_file" {
			continue
		}
		origin := ValueRow(part["source"])
		if len(origin) == 0 {
			origin = ValueRow(part["image"])
		}
		imageURL, _ := part["image_url"].(string)
		if imageURL == "" {
			imageURL = dataString(ValueRow(part["image_url"]), "url")
		}
		imageString, _ := part["image"].(string)
		source := firstString(part["path"], part["file_path"], part["url"], part["uri"], imageURL, imageString, origin["url"])
		mime := strings.ToLower(firstString(part["mime"], part["mime_type"], part["mimeType"], origin["media_type"], origin["mimeType"]))
		bytes := firstString(part["data"], part["b64_json"], part["blob"], origin["data"])
		if source == "" && bytes != "" && isImage && strings.HasPrefix(mime, "image/") {
			source = "data:" + mime + ";base64," + bytes
		}
		ref := ""
		if isImage {
			ref = source
		}
		add(source, firstString(part["filename"], part["name"]), mime, isImage, ref)
	}
	attachments = deduplicatedRequestMedia(attachments)
	if len(attachments) > requestMediaLimit {
		attachments, complete = attachments[:requestMediaLimit], false
	}
	// Never persist an unmaterialized base64 body, including a malformed image.
	if redacted := requestMediaPreview(text); redacted != text {
		text, complete = redacted, false
	}
	text, fits := requestBoundedText(strings.TrimSpace(text), 1<<20)
	return text, attachments, complete && fits
}

func requestMediaPreview(text string) string {
	spans := requestMediaDataURI.FindAllStringIndex(text, 128)
	if len(spans) == 0 {
		return text
	}
	mask := requestCodeMask(text)
	for i := len(spans) - 1; i >= 0; i-- {
		span := spans[i]
		if !mask[span[0]] {
			text = text[:span[0]] + "[Image]" + text[span[1]:]
		}
	}
	return text
}

func requestImagePartIdentity(part Row) string {
	origin := ValueRow(part["source"])
	if len(origin) == 0 {
		origin = ValueRow(part["image"])
	}
	imageURL, _ := part["image_url"].(string)
	image, _ := part["image"].(string)
	source := firstString(part["path"], part["file_path"], part["url"], part["uri"], imageURL, ValueRow(part["image_url"])["url"], image, origin["url"])
	if source != "" {
		return HashString(requestComparableSource(source))
	}
	return pythonHash([]any{firstString(part["data"], part["b64_json"], part["blob"], origin["data"]), firstString(part["mime"], part["mime_type"], part["mimeType"], origin["media_type"], origin["mimeType"])})
}

func requestMediaKey(value Row) string {
	source := firstString(value["source_key"], value["path"], value["reference"], value["id"])
	return firstString(value["placement"], "user") + "|" + source
}

func deduplicatedRequestMedia(values []Row) []Row {
	result, indices := []Row{}, map[string]int{}
	for _, source := range values[:min(4096, len(values))] {
		value := CloneRow(source)
		value["placement"] = firstString(value["placement"], "user")
		key := requestMediaKey(value)
		if index, found := indices[key]; found {
			old := result[index]
			refs := append(ValueStrings(old["references"]), ValueStrings(value["references"])...)
			refs = append(refs, dataString(old, "reference"), dataString(value, "reference"))
			kept, seen := []string{}, map[string]bool{}
			for _, ref := range refs {
				if ref != "" && len(ref) <= 4096 && !seen[ref] && len(kept) < 16 {
					kept, seen[ref] = append(kept, ref), true
				}
			}
			if len(kept) > 0 {
				old["reference"], old["references"] = kept[0], kept
			}
			if dataString(old, "mime") == "application/octet-stream" && strings.HasPrefix(dataString(value, "mime"), "image/") {
				old["mime"] = value["mime"]
			}
			if created, ok := ValueFloat(value["created_at"]); ok && created > 0 {
				previous, valid := ValueFloat(old["created_at"])
				if !valid || previous <= 0 || created < previous {
					old["created_at"] = created
				}
			}
		} else {
			indices[key] = len(result)
			result = append(result, value)
		}
	}
	return result
}

func mergeRequestMediaPatch(value, input Row) Row {
	patch := CloneRow(input)
	sameUser := dataString(patch, "user_message_key") != "" && dataString(patch, "user_message_key") == dataString(value, "user_message_key")
	sameFinal := dataString(patch, "final_message_key") != "" && dataString(patch, "final_message_key") == dataString(value, "final_message_key")
	fallback := ValueBool(patch["final_fallback"])
	delete(patch, "final_fallback")
	if fallback && (dataString(value, "final_source") == "message" || dataString(patch, "final") == "" && len(ValueRows(patch["final_attachments"])) == 0 && (dataString(value, "final") != "" || len(ValueRows(value["final_attachments"])) > 0)) {
		if dataString(patch, "final") == dataString(value, "final") {
			sameFinal = true
			for _, key := range []string{"final_source", "final_message_key", "final_complete"} {
				patch[key] = value[key]
			}
		} else {
			for _, key := range []string{"final", "final_complete", "final_attachments", "final_source", "final_message_key"} {
				delete(patch, key)
			}
		}
	}
	for _, key := range []string{"attachments", "final_attachments", "generated_attachments"} {
		if _, present := patch[key]; !present {
			continue
		}
		items := ValueRows(patch[key])
		if key == "attachments" && sameUser || key == "final_attachments" && sameFinal || key == "generated_attachments" {
			items = append(append([]Row{}, ValueRows(value[key])...), items...)
		}
		if key == "generated_attachments" && len(ValueRows(value[key])) > 0 && !ValueBool(value["generated_complete"]) {
			patch["generated_complete"] = false
		}
		previous := ValueRows(value[key])
		if key != "attachments" {
			previous = append(append([]Row{}, ValueRows(value["final_attachments"])...), ValueRows(value["generated_attachments"])...)
		}
		dates := map[string]float64{}
		for _, item := range previous {
			if created, valid := ValueFloat(item["created_at"]); valid && created > 0 {
				id := requestMediaKey(item)
				if dates[id] == 0 || dates[id] > created {
					dates[id] = created
				}
			}
		}
		items = deduplicatedRequestMedia(items)
		for _, item := range items {
			if old := dates[requestMediaKey(item)]; old > 0 {
				created, valid := ValueFloat(item["created_at"])
				if !valid || created <= 0 || old < created {
					item["created_at"] = old
				}
			}
		}
		patch[key] = items[:min(requestMediaLimit, len(items))]
		if len(items) > requestMediaLimit {
			field := map[string]string{"attachments": "user_complete", "final_attachments": "final_complete", "generated_attachments": "generated_complete"}[key]
			patch[field] = false
		}
	}
	if sameUser && dataString(patch, "user") == "" && dataString(value, "user") != "" {
		patch["user"], patch["user_complete"] = value["user"], value["user_complete"]
	}
	return patch
}

func requestImageTool(name string) bool {
	name = strings.ToLower(name)
	return strings.Contains(name, "imagegen") || strings.Contains(name, "image_gen") || strings.Contains(name, "image_generation") || name == "generate_image"
}

func requestImageToolParts(output any) []Row {
	result, nodes := []Row{}, 0
	var visit func(any, int)
	visit = func(raw any, depth int) {
		if depth > 6 || nodes >= 256 || len(result) >= 128 {
			return
		}
		nodes++
		if text, ok := raw.(string); ok {
			if depth == 0 && len(text) <= 16_000_000 {
				if value, err := decodeMediaJSON(text); err == nil {
					visit(value, depth+1)
					return
				}
			}
			if strings.HasPrefix(text, "data:image/") || strings.HasPrefix(requestImageMIME(text), "image/") && (filepath.IsAbs(text) || strings.HasPrefix(text, "file://") || len(strings.Fields(text)) == 1) {
				result = append(result, Row{"type": "image", "path": text})
			} else {
				result = append(result, Row{"type": "text", "text": text})
			}
			return
		}
		if list, ok := raw.([]any); ok {
			for _, item := range list[:min(128, len(list))] {
				visit(item, depth+1)
			}
			return
		}
		part := ValueRow(raw)
		if len(part) == 0 {
			return
		}
		kind := dataString(part, "type")
		if requestImagePart(part) || kind == "text" || kind == "input_text" || kind == "output_text" {
			result = append(result, part)
			return
		}
		if kind == "image_generation_call" {
			if encoded, ok := part["result"].(string); ok && encoded != "" {
				format := strings.ToLower(firstString(part["output_format"], "png"))
				if format == "jpg" {
					format = "jpeg"
				}
				if strings.HasPrefix(encoded, "data:image/") {
					result = append(result, Row{"type": "image", "image_url": encoded})
				} else {
					result = append(result, Row{"type": "image", "data": encoded, "mime": "image/" + format})
				}
				return
			}
		}
		if part["image_url"] != nil || part["b64_json"] != nil || strings.HasPrefix(firstString(part["mimeType"], part["mime_type"]), "image/") || strings.HasPrefix(requestImageMIME(firstString(part["path"], part["file_path"])), "image/") {
			part = CloneRow(part)
			part["type"] = "image"
			if part["b64_json"] != nil && part["mime"] == nil {
				part["mime"] = "image/png"
			}
			result = append(result, part)
		}
		for _, key := range []string{"content", "output", "images", "artifacts", "result", "resource"} {
			if part[key] != nil {
				visit(part[key], depth+1)
			}
		}
	}
	visit(output, 0)
	return result
}

func requestImageMarkdown(name, target string) string {
	name = clip(strings.Join(strings.Fields(name), " "), 255)
	name = strings.NewReplacer("\\", "\\\\", "[", "\\[", "]", "\\]").Replace(name)
	return "![" + name + "](" + target + ")"
}

func rewriteRequestImages(text string, item Row, target string) string {
	sources := append(ValueStrings(item["references"]), dataString(item, "reference"), dataString(item, "path"))
	normalized := map[string]bool{}
	for _, source := range sources {
		if source != "" {
			normalized[requestComparableSource(source)] = true
		}
	}
	matches := requestImageMatches(text)
	for i := len(matches) - 1; i >= 0; i-- {
		match := matches[i]
		if normalized[requestComparableSource(match.source)] {
			text = text[:match.start] + requestImageMarkdown(firstString(match.name, item["name"]), target) + text[match.end:]
		}
	}
	if !strings.Contains(text, target) {
		text += "\n\n" + requestImageMarkdown(dataString(item, "name"), target)
	}
	return text
}
