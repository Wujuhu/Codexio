package backend

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	_ "image/gif"
	"image/jpeg"
	"image/png"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	_ "golang.org/x/image/bmp"
	"golang.org/x/image/draw"
	_ "golang.org/x/image/tiff"
	_ "golang.org/x/image/webp"
)

const requestImageDataLimit = 1 << 20
const requestImageRetention = 3 * 86400

var requestOwnedMediaName = regexp.MustCompile(`^[a-f0-9]{64}\.(png|jpg|gif|webp|heic|heif|avif|tiff|bmp|svg)$`)
var requestImageID = regexp.MustCompile(`^[a-f0-9]{64}$`)

type requestMediaFile struct {
	size int64
	used time.Time
}
type requestImagePreview struct {
	path, mime, digest string
	width, height      int
	used, retry        time.Time
}
type requestImageFlight struct {
	done  chan struct{}
	value requestImagePreview
	err   error
}
type requestMediaCache struct {
	mu        sync.Mutex
	directory string
	mock      bool
	files     map[string]requestMediaFile
	loaded    bool
	eviction  uint64
	previews  map[string]requestImagePreview
	flights   map[string]*requestImageFlight
	work      chan struct{}
	client    *http.Client
	ctx       context.Context
	cancel    context.CancelFunc
}

func newRequestMediaCache(directory string, mock bool) *requestMediaCache {
	name := "request-media"
	if mock {
		name += "-mock"
	}
	ctx, cancel := context.WithCancel(context.Background())
	var trustedProxies sync.Map
	transport := &http.Transport{MaxIdleConns: 2, MaxConnsPerHost: 2, MaxResponseHeaderBytes: 65536, IdleConnTimeout: 30 * time.Second, ResponseHeaderTimeout: 12 * time.Second, TLSHandshakeTimeout: 8 * time.Second}
	transport.Proxy = func(request *http.Request) (*url.URL, error) {
		if !requestPublicImageURL(request.URL) {
			return nil, errors.New("unsupported image URL")
		}
		proxy, err := systemAccountProxy(request)
		if err != nil || proxy == nil {
			return proxy, err
		}
		// A user-configured loopback proxy is allowed. Validate the destination
		// separately before the proxy handles it, and retain no app credentials.
		addresses, err := net.DefaultResolver.LookupIPAddr(request.Context(), request.URL.Hostname())
		if err != nil || len(addresses) == 0 {
			return nil, errors.New("image host unavailable")
		}
		for _, address := range addresses {
			if !requestPublicIP(address.IP) {
				return nil, errors.New("image host is not public")
			}
		}
		port := proxy.Port()
		if port == "" {
			port = "80"
			if proxy.Scheme == "https" {
				port = "443"
			}
		}
		trustedProxies.Store(net.JoinHostPort(proxy.Hostname(), port), true)
		return proxy, nil
	}
	transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		if _, trusted := trustedProxies.Load(address); trusted {
			return (&net.Dialer{Timeout: 8 * time.Second}).DialContext(ctx, network, address)
		}
		host, port, err := net.SplitHostPort(address)
		if err != nil {
			return nil, err
		}
		addresses, err := net.DefaultResolver.LookupIPAddr(ctx, host)
		if err != nil || len(addresses) == 0 {
			return nil, errors.New("image host unavailable")
		}
		// Check resolved addresses as well as the URL, including each redirect;
		// public image previews must not reach local services through DNS rebinding.
		for _, addr := range addresses {
			if !requestPublicIP(addr.IP) {
				return nil, errors.New("image host is not public")
			}
		}
		dialer := net.Dialer{Timeout: 8 * time.Second}
		for _, addr := range addresses {
			connection, e := dialer.DialContext(ctx, network, net.JoinHostPort(addr.IP.String(), port))
			if e == nil {
				return connection, nil
			}
			err = e
		}
		return nil, err
	}
	client := &http.Client{Transport: transport, Timeout: 20 * time.Second, CheckRedirect: func(request *http.Request, via []*http.Request) error {
		if len(via) >= 5 || !requestPublicImageURL(request.URL) {
			return errors.New("unsupported image redirect")
		}
		return nil
	}}
	return &requestMediaCache{directory: filepath.Join(directory, name), mock: mock, files: map[string]requestMediaFile{}, previews: map[string]requestImagePreview{}, flights: map[string]*requestImageFlight{}, work: make(chan struct{}, 2), client: client, ctx: ctx, cancel: cancel}
}

func (c *requestMediaCache) close() {
	if c != nil {
		c.cancel()
		c.client.CloseIdleConnections()
	}
}

func requestPublicIP(ip net.IP) bool {
	return ip != nil && ip.IsGlobalUnicast() && !ip.IsPrivate() && !ip.IsLoopback() && !ip.IsLinkLocalUnicast() && !ip.IsLinkLocalMulticast() && !ip.IsUnspecified()
}
func requestPublicImageURL(u *url.URL) bool {
	if u == nil || u.User != nil || (u.Scheme != "http" && u.Scheme != "https") {
		return false
	}
	host := strings.ToLower(strings.TrimSuffix(u.Hostname(), "."))
	if host == "" || host == "localhost" || strings.HasSuffix(host, ".localhost") || strings.HasSuffix(host, ".local") {
		return false
	}
	if ip := net.ParseIP(host); ip != nil {
		return requestPublicIP(ip)
	}
	return true
}

func requestBytesDigest(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}
func decodeMediaJSON(text string) (any, error) {
	var result any
	decoder := json.NewDecoder(strings.NewReader(text))
	decoder.UseNumber()
	err := decoder.Decode(&result)
	return result, err
}

// The only files removed by eviction have our content-addressed basename and
// live in this private directory. Original source files are always read-only.
func (c *requestMediaCache) directoryReady() bool {
	if c == nil || os.MkdirAll(c.directory, 0700) != nil {
		return false
	}
	actual, err := filepath.EvalSymlinks(c.directory)
	return err == nil && strings.EqualFold(filepath.Clean(actual), filepath.Clean(c.directory))
}
func (c *requestMediaCache) loadFilesLocked() error {
	if !c.directoryReady() {
		return errors.New("image cache unavailable")
	}
	if c.loaded {
		return nil
	}
	entries, err := os.ReadDir(c.directory)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if !requestOwnedMediaName.MatchString(entry.Name()) {
			continue
		}
		info, e := entry.Info()
		if e == nil && info.Mode().IsRegular() && entry.Type()&os.ModeSymlink == 0 {
			c.files[entry.Name()] = requestMediaFile{info.Size(), info.ModTime()}
		}
	}
	c.loaded = true
	c.pruneLocked("")
	return nil
}
func (c *requestMediaCache) pruneLocked(pinned string) {
	var size int64
	for _, file := range c.files {
		size += file.size
	}
	for len(c.files) > 256 || size > 128<<20 {
		oldest := ""
		var date time.Time
		for name, file := range c.files {
			if name != pinned && (oldest == "" || file.used.Before(date)) {
				oldest, date = name, file.used
			}
		}
		if oldest == "" {
			break
		}
		if c.directoryReady() {
			_ = os.Remove(filepath.Join(c.directory, oldest))
		}
		size -= c.files[oldest].size
		delete(c.files, oldest)
		c.eviction++
	}
	for len(c.previews) > 512 {
		oldest := ""
		var date time.Time
		for key, item := range c.previews {
			if oldest == "" || item.used.Before(date) {
				oldest, date = key, item.used
			}
		}
		delete(c.previews, oldest)
	}
}
func (c *requestMediaCache) saveBytes(data []byte, suffix string) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if err := c.loadFilesLocked(); err != nil {
		return "", err
	}
	name := requestBytesDigest(data) + "." + suffix
	if !requestOwnedMediaName.MatchString(name) || len(data) == 0 || len(data) > 12<<20 {
		return "", errors.New("invalid image bytes")
	}
	path := filepath.Join(c.directory, name)
	info, err := os.Lstat(path)
	if err == nil && (!info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0) {
		return "", errors.New("invalid image cache entry")
	}
	if err != nil || info.Size() != int64(len(data)) {
		if err = writePrivateFile(path, data); err != nil {
			return "", err
		}
	}
	c.files[name] = requestMediaFile{int64(len(data)), time.Now()}
	c.pruneLocked(name)
	return path, nil
}
func (c *requestMediaCache) materialize(source string) (string, error) {
	if c == nil || len(source) > 12_000_000 {
		return "", errors.New("image data exceeds limit")
	}
	comma := strings.IndexByte(source, ',')
	if comma < 0 {
		return "", errors.New("invalid image data URI")
	}
	header := strings.ToLower(source[:comma])
	mime := strings.TrimPrefix(strings.Split(header, ";")[0], "data:")
	suffix := requestMediaExtensions[mime]
	if !strings.HasPrefix(header, "data:image/") || !strings.HasSuffix(header, ";base64") || suffix == "" {
		return "", errors.New("unsupported image data URI")
	}
	data, err := base64.StdEncoding.DecodeString(strings.TrimSpace(source[comma+1:]))
	if err != nil || len(data) == 0 || len(data) > 8<<20 {
		return "", errors.New("invalid image data")
	}
	return c.saveBytes(data, suffix)
}

func (c *requestMediaCache) missingSources(detail Row) bool {
	if c == nil {
		return false
	}
	for _, key := range []string{"attachments", "final_attachments", "generated_attachments"} {
		for _, item := range ValueRows(detail[key]) {
			path := dataString(item, "path")
			if strings.EqualFold(filepath.Dir(path), c.directory) && requestOwnedMediaName.MatchString(filepath.Base(path)) {
				if info, err := os.Lstat(path); err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
					return true
				}
			}
		}
	}
	return false
}

func (c *requestMediaCache) recoveryGeneration() uint64 {
	if c == nil {
		return 0
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.eviction
}

func (c *requestMediaCache) preview(ctx context.Context, item Row) (requestImagePreview, error) {
	if c == nil {
		return requestImagePreview{}, errors.New("image cache unavailable")
	}
	if err := ctx.Err(); err != nil {
		return requestImagePreview{}, err
	}
	if err := c.ctx.Err(); err != nil {
		return requestImagePreview{}, err
	}
	source := firstString(item["path"], item["reference"])
	stamp := source
	local := dataString(item, "path") != ""
	if local {
		if !filepath.IsAbs(source) || strings.HasPrefix(source, `\\`) {
			return requestImagePreview{}, errors.New("image unavailable")
		}
		file, err := os.Open(source)
		if err != nil {
			return requestImagePreview{}, err
		}
		info, err := file.Stat()
		if err == nil && info.Mode().IsRegular() && info.Size() > 0 && info.Size() <= 12<<20 {
			stamp = fmt.Sprintf("%s|%d|%d|%s", source, info.Size(), info.ModTime().UnixNano(), ledgerFileIdentity(file))
		} else {
			file.Close()
			return requestImagePreview{}, errors.New("unsupported image source")
		}
		file.Close()
	} else if u, err := url.Parse(source); err != nil || !requestPublicImageURL(u) {
		return requestImagePreview{}, errors.New("image unavailable")
	}
	key := HashString(stamp)
	for {
		if err := ctx.Err(); err != nil {
			return requestImagePreview{}, err
		}
		if err := c.ctx.Err(); err != nil {
			return requestImagePreview{}, err
		}
		c.mu.Lock()
		if cached, exists := c.previews[key]; exists {
			cached.used = time.Now()
			c.previews[key] = cached
			if cached.path != "" {
				if info, err := os.Lstat(cached.path); err == nil && info.Mode().IsRegular() && info.Size() > 0 && info.Size() <= requestImageDataLimit && (local || time.Now().Before(cached.retry)) {
					c.mu.Unlock()
					return cached, nil
				}
			} else if time.Now().Before(cached.retry) {
				c.mu.Unlock()
				return requestImagePreview{}, errors.New("image unavailable")
			}
		}
		if pending := c.flights[key]; pending != nil {
			c.mu.Unlock()
			select {
			case <-ctx.Done():
				return requestImagePreview{}, ctx.Err()
			case <-c.ctx.Done():
				return requestImagePreview{}, c.ctx.Err()
			case <-pending.done:
				if requestImageContextError(pending.err) {
					// The flight belonged to another caller's context. A still
					// mounted consumer retries under its own live context.
					continue
				}
				return pending.value, pending.err
			}
		}
		flight := &requestImageFlight{done: make(chan struct{})}
		c.flights[key] = flight
		c.mu.Unlock()
		value, err := c.makePreview(ctx, source, stamp, local)
		if contextError := ctx.Err(); contextError != nil {
			err = contextError
		} else if contextError := c.ctx.Err(); contextError != nil {
			err = contextError
		}
		c.mu.Lock()
		if !requestImageContextError(err) {
			value.used = time.Now()
			if err != nil {
				value.retry = time.Now().Add(time.Minute)
			} else if !local {
				value.retry = time.Now().Add(time.Hour)
			}
			c.previews[key] = value
			c.pruneLocked(filepath.Base(value.path))
		}
		flight.value, flight.err = value, err
		delete(c.flights, key)
		close(flight.done)
		c.mu.Unlock()
		return value, err
	}
}

func requestImageContextError(err error) bool {
	return errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded)
}

func (c *requestMediaCache) makePreview(ctx context.Context, source, stamp string, local bool) (requestImagePreview, error) {
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	stop := context.AfterFunc(c.ctx, cancel)
	defer stop()
	select {
	case c.work <- struct{}{}:
		defer func() { <-c.work }()
	case <-ctx.Done():
		return requestImagePreview{}, ctx.Err()
	}
	var reader io.ReadCloser
	if local {
		file, err := os.Open(source)
		if err != nil {
			return requestImagePreview{}, err
		}
		info, err := file.Stat()
		if err != nil || !info.Mode().IsRegular() || stamp != fmt.Sprintf("%s|%d|%d|%s", source, info.Size(), info.ModTime().UnixNano(), ledgerFileIdentity(file)) {
			file.Close()
			return requestImagePreview{}, errors.New("image source changed")
		}
		reader = file
	} else {
		if c.mock {
			return requestImagePreview{}, errors.New("mock image preview does not use the network")
		}
		request, err := http.NewRequestWithContext(ctx, "GET", source, nil)
		if err != nil {
			return requestImagePreview{}, err
		}
		response, err := c.client.Do(request)
		if err != nil {
			if contextError := ctx.Err(); contextError != nil {
				return requestImagePreview{}, contextError
			}
			return requestImagePreview{}, err
		}
		mime := strings.ToLower(strings.SplitN(response.Header.Get("Content-Type"), ";", 2)[0])
		if response.StatusCode < 200 || response.StatusCode >= 300 || response.ContentLength > 12<<20 || mime != "" && mime != "application/octet-stream" && !strings.HasPrefix(mime, "image/") {
			response.Body.Close()
			return requestImagePreview{}, errors.New("unsupported image response")
		}
		reader = response.Body
	}
	defer reader.Close()
	data, err := io.ReadAll(io.LimitReader(reader, (12<<20)+1))
	if err := ctx.Err(); err != nil {
		return requestImagePreview{}, err
	}
	if err != nil {
		return requestImagePreview{}, err
	}
	if len(data) == 0 || len(data) > 12<<20 {
		return requestImagePreview{}, errors.New("invalid image source size")
	}
	encoded, mime, width, height, err := requestRasterImage(data)
	if err := ctx.Err(); err != nil {
		return requestImagePreview{}, err
	}
	if err != nil {
		return requestImagePreview{}, err
	}
	path, err := c.saveBytes(encoded, requestMediaExtensions[mime])
	return requestImagePreview{path: path, mime: mime, digest: requestBytesDigest(encoded), width: width, height: height}, err
}

func requestRasterImage(data []byte) ([]byte, string, int, int, error) {
	config, _, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil || config.Width <= 0 || config.Height <= 0 || config.Width > 16384 || config.Height > 16384 || int64(config.Width)*int64(config.Height) > 64_000_000 {
		return nil, "", 0, 0, errors.New("unsupported image dimensions or format")
	}
	source, _, err := image.Decode(bytes.NewReader(data))
	if err != nil {
		return nil, "", 0, 0, err
	}
	for _, side := range []int{2048, 1536, 1024, 768, 512} {
		w, h := config.Width, config.Height
		if max(w, h) > side {
			w, h = max(1, w*side/max(config.Width, config.Height)), max(1, h*side/max(config.Width, config.Height))
		}
		destination := image.NewNRGBA(image.Rect(0, 0, w, h))
		draw.CatmullRom.Scale(destination, destination.Bounds(), source, source.Bounds(), draw.Src, nil)
		if !destination.Opaque() {
			var encoded bytes.Buffer
			if err := png.Encode(&encoded, destination); err == nil && encoded.Len() <= requestImageDataLimit {
				return encoded.Bytes(), "image/png", w, h, nil
			}
		} else {
			for _, quality := range []int{82, 65} {
				var encoded bytes.Buffer
				if err := jpeg.Encode(&encoded, destination, &jpeg.Options{Quality: quality}); err == nil && encoded.Len() <= requestImageDataLimit {
					return encoded.Bytes(), "image/jpeg", w, h, nil
				}
			}
		}
	}
	return nil, "", 0, 0, errors.New("image preview exceeds limit")
}

func (c *requestMediaCache) readPreview(preview requestImagePreview) ([]byte, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.directoryReady() || !strings.EqualFold(filepath.Dir(preview.path), c.directory) {
		return nil, errors.New("image cache unavailable")
	}
	info, err := os.Lstat(preview.path)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Size() <= 0 || info.Size() > requestImageDataLimit {
		return nil, errors.New("image preview unavailable")
	}
	data, err := os.ReadFile(preview.path)
	if err != nil || requestBytesDigest(data) != preview.digest {
		return nil, errors.New("image preview changed")
	}
	c.files[filepath.Base(preview.path)] = requestMediaFile{int64(len(data)), time.Now()}
	return data, nil
}

func (s *Store) desktopRequestImages(raw Row, requestID string) Row {
	value := CloneRow(raw)
	refs, attachments := []Row{}, []Row{}
	for _, item := range deduplicatedRequestMedia(ValueRows(raw["attachments"])) {
		if !strings.HasPrefix(dataString(item, "mime"), "image/") {
			attachments = append(attachments, item)
			continue
		}
		id := dataString(item, "id")
		if !requestImageID.MatchString(id) {
			id = HashString(requestMediaKey(item))
		}
		placement := firstString(item["placement"], "user")
		if placement != "final" {
			placement = "user"
		}
		refs = append(refs, Row{"id": id, "name": item["name"], "mime": item["mime"], "placement": placement, "availability": "pending"})
		value[placement] = rewriteRequestImages(dataString(value, placement), item, "codexio-image://"+id)
	}
	value["attachments"], value["images"] = attachments, refs
	return value
}

func (s *Store) requestImage(ctx context.Context, requestID, imageID string) (Row, error) {
	if !requestImageID.MatchString(imageID) || requestID == "" || len(requestID) > 1024 {
		return nil, errors.New("invalid image identity")
	}
	// Resolve exclusively through the indexed request. No path or URL is ever
	// accepted from the UI, including when a caller fabricates an image ID.
	detail, err := s.Detail(requestID, 1)
	if err != nil {
		return nil, err
	}
	for _, item := range ValueRows(detail["attachments"]) {
		id := dataString(item, "id")
		if !requestImageID.MatchString(id) {
			id = HashString(requestMediaKey(item))
		}
		if id != imageID || !strings.HasPrefix(dataString(item, "mime"), "image/") {
			continue
		}
		preview, err := s.media.preview(ctx, item)
		if err != nil {
			return Row{"id": imageID, "availability": "unavailable"}, nil
		}
		data, err := s.media.readPreview(preview)
		if err != nil {
			return Row{"id": imageID, "availability": "unavailable"}, nil
		}
		return Row{"id": imageID, "availability": "available", "mime": preview.mime, "width": preview.width, "height": preview.height, "sha256": preview.digest, "dataURL": "data:" + preview.mime + ";base64," + base64.StdEncoding.EncodeToString(data)}, nil
	}
	return Row{"id": imageID, "availability": "unavailable"}, nil
}

// Public fields exactly match apple/shared/MobileProtocol.swift. Payloads are
// transient host output for the transport/outbox, never desktop Detail content.
func (s *Store) prepareRequestImages(ctx context.Context, raw Row, requestID string, started float64, enforceRetention bool) (Row, error) {
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	if !requestImageID.MatchString(requestID) {
		return nil, errors.New("invalid mobile request identity")
	}
	result := Row{"user": dataString(raw, "user"), "final": dataString(raw, "final"), "images": []Row{}, "image_payloads": Row{}}
	items := append([]Row{}, ValueRows(raw["attachments"])...)
	createdAt := func(item Row) float64 {
		created, valid := ValueFloat(item["created_at"])
		if !valid || created <= 0 {
			created = started
		}
		return math.Floor(created)
	}
	sort.SliceStable(items, func(i, j int) bool { return createdAt(items[i]) < createdAt(items[j]) })
	refs, payloads, content := []Row{}, Row{}, map[string]Row{}
	used := map[string]bool{}
	for _, item := range items[:min(requestMediaLimit, len(items))] {
		if !strings.HasPrefix(dataString(item, "mime"), "image/") {
			continue
		}
		placement := firstString(item["placement"], "user")
		if placement != "final" {
			placement = "user"
		}
		source := firstString(item["path"], item["reference"], item["id"], item["name"])
		key := requestID + "|" + placement + "|" + source
		if used[key] {
			continue
		}
		used[key] = true
		created := createdAt(item)
		expires := created + requestImageRetention
		id := HashString(key)
		availability := "unavailable"
		if enforceRetention && expires <= float64(time.Now().Unix()) {
			availability = "expired"
		}
		name, _ := requestBoundedText(firstString(item["name"], "图片"), 240)
		ref := Row{"id": id, "name": name, "mime": firstString(item["mime"], "image/png"), "placement": placement, "width": 0, "height": 0, "expires": expires, "availability": availability}
		if availability != "expired" && created > 0 && created <= float64(time.Now().Unix()+300) {
			if preview, err := s.media.preview(ctx, item); err == nil {
				identity := requestID + "|" + placement + "|" + preview.digest
				if previous := content[identity]; previous != nil {
					ref = previous
				} else if data, err := s.media.readPreview(preview); err == nil {
					// Swift String(floor(Double)) preserves its .0 suffix.
					id = HashString(identity + "|" + fmt.Sprintf("%.1f", created))
					ref["id"], ref["mime"], ref["width"], ref["height"], ref["availability"] = id, preview.mime, preview.width, preview.height, "available"
					payloads[id] = Row{"id": id, "request": requestID, "mime": preview.mime, "width": preview.width, "height": preview.height, "created": created, "expires": expires, "data": base64.StdEncoding.EncodeToString(data)}
					content[identity] = ref
				}
			}
		}
		id = dataString(ref, "id")
		found := false
		for _, previous := range refs {
			if dataString(previous, "id") == id {
				found = true
				break
			}
		}
		if !found {
			refs = append(refs, ref)
		}
		result[placement] = rewriteRequestImages(dataString(result, placement), item, "codexio-image://"+id)
	}
	result["images"], result["image_payloads"] = refs, payloads
	return result, nil
}
