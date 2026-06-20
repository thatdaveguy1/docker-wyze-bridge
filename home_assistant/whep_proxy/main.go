package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"time"

	"github.com/gorilla/mux"
	"github.com/pion/webrtc/v3"
)

type ICEServer struct {
	URL        string `json:"url"`
	Username   string `json:"username"`
	Credential string `json:"credential"`
}

type WebRTCConfig struct {
	SignalingURL string      `json:"signaling_url"`
	ICEServers   []ICEServer `json:"ice_servers"`
	AuthToken    string      `json:"auth_token"`
	PhoneID      string      `json:"phone_id"`
}

func envListMatches(name, value string) bool {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return false
	}
	if strings.EqualFold(raw, "all") || raw == "*" {
		return true
	}
	wanted := strings.FieldsFunc(raw, func(r rune) bool {
		return r == ',' || r == ' ' || r == '\n' || r == '\t'
	})
	for _, item := range wanted {
		if strings.EqualFold(strings.TrimSpace(item), value) {
			return true
		}
	}
	return false
}

func upstreamVideoOnly(streamID string) bool {
	return envListMatches("WHEP_UPSTREAM_VIDEO_ONLY_STREAMS", streamID)
}

func newWebRTCStream(streamID string, config WebRTCConfig) (*WebRTCStream, error) {
	videoTrack, err := webrtc.NewTrackLocalStaticRTP(
		webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeH264},
		"video",
		"pion",
	)
	if err != nil {
		return nil, err
	}

	audioTrack, err := webrtc.NewTrackLocalStaticRTP(
		webrtc.RTPCodecCapability{
			MimeType:  webrtc.MimeTypePCMU,
			ClockRate: 8000,
			Channels:  2,
		},
		"audio",
		"pion",
	)
	if err != nil {
		return nil, err
	}

	stream := &WebRTCStream{
		streamID:        streamID,
		media:           &MediaForwarder{videoTrack: videoTrack, audioTrack: audioTrack},
		streamCreatedAt: time.Now(),
	}
	stream.setConfig(config)
	return stream, nil
}

func startStreamUpstream(stream *WebRTCStream) {
	go func(stream *WebRTCStream) {
		if err := establishUpstream(stream); err != nil {
			fmt.Println("[WHEP_PROXY] Initial upstream establish failed for", stream.streamID, err)
			stream.scheduleReconnect("initial establish failed")
		}
	}(stream)
}

func isLoopbackRemoteAddr(remoteAddr string) bool {
	host, _, err := net.SplitHostPort(remoteAddr)
	if err != nil {
		host = remoteAddr
	}
	parsed := net.ParseIP(strings.TrimSpace(host))
	return parsed != nil && parsed.IsLoopback()
}

func whepListenAddress() string {
	port := strings.TrimSpace(os.Getenv("WHEP_PROXY_PORT"))
	if port == "" {
		port = "8080"
	}
	if strings.HasPrefix(port, ":") {
		return "127.0.0.1" + port
	}
	return "127.0.0.1:" + port
}

func main() {
	r := mux.NewRouter()
	r.HandleFunc("/whep/{streamID}", whepHandler).Methods("GET", "OPTIONS", "POST")
	r.HandleFunc("/websocket/{streamID}", websocketHandler).Methods("GET", "POST")
	r.HandleFunc("/status/{streamID}", statusHandler).Methods("GET")

	go func() {
		addr := whepListenAddress()
		fmt.Printf("[WHEP_PROXY] Listening on %s\n", addr)
		if err := http.ListenAndServe(addr, r); err != nil {
			panic(err)
		}
	}()
	stopReaper := make(chan struct{})
	go runStreamHealthReaper(stopReaper)

	sigchan := make(chan os.Signal, 1)
	signal.Notify(sigchan, os.Interrupt)
	<-sigchan
	close(stopReaper)

	fmt.Println("[WHEP_PROXY] Exiting.")

	streamsMu.Lock()
	defer streamsMu.Unlock()
	for streamID, stream := range streams {
		destroyStreamLocked(streamID, stream)
	}
}

func redactURL(raw string) string {
	parsed, err := url.Parse(raw)
	if err != nil {
		return raw
	}
	query := parsed.Query()
	for _, key := range []string{
		"X-Amz-Security-Token",
		"X-Amz-Signature",
		"X-Amz-Credential",
		"X-Amz-Date",
		"X-Amz-Expires",
	} {
		if query.Has(key) {
			query.Set(key, "REDACTED")
		}
	}
	parsed.RawQuery = query.Encode()
	return parsed.String()
}

func mustJSON(v interface{}) []byte {
	data, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return data
}

func whepDebugEnabled() bool {
	v := strings.TrimSpace(strings.ToLower(os.Getenv("WHEP_DEBUG")))
	return v == "1" || v == "true" || v == "yes" || v == "on"
}

func whepTraceStream() string {
	return strings.TrimSpace(strings.ToLower(os.Getenv("WHEP_TRACE_STREAM")))
}

func whepTraceEnabled(streamID string) bool {
	traceStream := whepTraceStream()
	return traceStream != "" && strings.EqualFold(traceStream, streamID)
}

func traceLogf(streamID, format string, args ...interface{}) {
	if !whepTraceEnabled(streamID) {
		return
	}
	log.Printf("[WHEP_TRACE] %s: %s", streamID, fmt.Sprintf(format, args...))
}

func sanitizeLogURL(raw string) string {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return "<redacted>"
	}
	return fmt.Sprintf("%s://%s%s", parsed.Scheme, parsed.Host, parsed.Path)
}

func sdpHasMediaLine(sdp, media string) bool {
	return strings.Contains(sdp, "\nm="+media+" ") || strings.HasPrefix(sdp, "m="+media+" ")
}
