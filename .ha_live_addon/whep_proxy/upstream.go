package main

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
	"github.com/pion/interceptor"
	"github.com/pion/webrtc/v3"
)

// UpstreamSession owns the signaling channel and peer connection to the KVS
// backend.  One session is created per establishUpstream() call; when the
// websocket or peer connection fails, the stream replaces it with a new
// session after reconnect backoff.
type UpstreamSession struct {
	peerConnection    *webrtc.PeerConnection
	wsConn            *websocket.Conn
	wsMu              sync.Mutex
	pendingCandidates []webrtc.ICECandidateInit
	remoteDescription *webrtc.SessionDescription
	answerReceived    chan struct{}
	answerOnce        sync.Once
	correlationID     string
	recipientClientID string
	startedAt         time.Time
	normalCloseLogged atomic.Bool
	videoTrackLogged  atomic.Bool
	audioTrackLogged  atomic.Bool
}

type wsCloseInfo struct {
	code   int
	reason string
	normal bool
}

func (stream *WebRTCStream) currentUpstream() *UpstreamSession {
	stream.upstreamMu.RLock()
	defer stream.upstreamMu.RUnlock()
	return stream.upstream
}

func (stream *WebRTCStream) setUpstream(session *UpstreamSession) {
	stream.upstreamMu.Lock()
	stream.upstream = session
	stream.upstreamMu.Unlock()
	stream.upstreamAlive.Store(session != nil)
}

func (stream *WebRTCStream) clearUpstreamIfCurrent(session *UpstreamSession) bool {
	stream.upstreamMu.Lock()
	defer stream.upstreamMu.Unlock()
	if stream.upstream != session {
		return false
	}
	stream.upstream = nil
	stream.upstreamAlive.Store(false)
	return true
}

func (stream *WebRTCStream) getConfig() WebRTCConfig {
	stream.configMu.RLock()
	defer stream.configMu.RUnlock()
	return stream.config
}

func (stream *WebRTCStream) setConfig(config WebRTCConfig) {
	stream.configMu.Lock()
	stream.config = config
	stream.configMu.Unlock()
}

func closeUpstreamSession(session *UpstreamSession) {
	if session == nil {
		return
	}
	if session.wsConn != nil {
		session.wsMu.Lock()
		_ = session.wsConn.Close()
		session.wsConn = nil
		session.wsMu.Unlock()
	}
	if session.peerConnection != nil {
		_ = session.peerConnection.Close()
		session.peerConnection = nil
	}
}

func shouldLogTrackEnd(err error) bool {
	return !errors.Is(err, io.EOF)
}

func shouldLogRTCPEnd(err error) bool {
	return !errors.Is(err, io.EOF)
}

func logNormalClose(
	session *UpstreamSession,
	streamID string,
	closeInfo wsCloseInfo,
	peerState webrtc.PeerConnectionState,
	iceState webrtc.ICEConnectionState,
	videoReady bool,
	audioReady bool,
	upstreamAlive bool,
	whepClients int32,
) {
	if session == nil || !session.normalCloseLogged.CompareAndSwap(false, true) {
		return
	}
	reason := closeInfo.reason
	if reason == "" {
		reason = "normal closure"
	}
	log.Printf(
		"[WHEP_PROXY] Upstream session rotated for %s: websocket close code=%d reason=%q peer=%s ice=%s video_ready=%t audio_ready=%t upstream_alive=%t whep_clients=%d session_age=%s",
		streamID,
		closeInfo.code,
		reason,
		peerState.String(),
		iceState.String(),
		videoReady,
		audioReady,
		upstreamAlive,
		whepClients,
		time.Since(session.startedAt).Round(time.Millisecond),
	)
}

func shouldReconnectOnNormalWSClosure(state webrtc.PeerConnectionState, videoReady bool, audioReady bool) bool {
	switch state {
	case webrtc.PeerConnectionStateConnected:
		return false
	case webrtc.PeerConnectionStateFailed, webrtc.PeerConnectionStateClosed:
		return true
	}

	return !videoReady
}

func sendSignalingMessage(
	session *UpstreamSession,
	action string,
	payload interface{},
	correlationID string,
) error {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return err
	}

	envelope := map[string]interface{}{
		"action":         action,
		"messagePayload": base64.StdEncoding.EncodeToString(encoded),
	}
	if session != nil && session.recipientClientID != "" {
		envelope["recipientClientId"] = session.recipientClientID
	}
	if correlationID != "" {
		envelope["correlationId"] = correlationID
	}

	session.wsMu.Lock()
	defer session.wsMu.Unlock()
	if session.wsConn == nil {
		return fmt.Errorf("websocket unavailable")
	}
	return session.wsConn.WriteJSON(envelope)
}

func decodeSignalingPayload(msg map[string]interface{}) ([]byte, error) {
	payload, _ := msg["messagePayload"].(string)
	if payload == "" {
		return nil, fmt.Errorf("empty messagePayload")
	}
	return base64.StdEncoding.DecodeString(payload)
}

func generateCorrelationID(phoneID string) string {
	correlationID := fmt.Sprintf("%s.%d", phoneID, time.Now().UnixMilli())
	if phoneID == "" {
		correlationID = fmt.Sprintf("%d", time.Now().UnixMilli())
	}
	if len(correlationID) <= 256 {
		return correlationID
	}
	return correlationID[len(correlationID)-256:]
}

func decodeSignalingURL(rawURL string) (string, error) {
	return rawURL, nil
}

func createPeerConnection(config WebRTCConfig) (*webrtc.PeerConnection, error) {
	iceServers := []webrtc.ICEServer{}
	for _, server := range config.ICEServers {
		iceServers = append(iceServers, webrtc.ICEServer{
			URLs:       []string{server.URL},
			Username:   server.Username,
			Credential: server.Credential,
		})
	}
	if len(iceServers) == 0 {
		iceServers = []webrtc.ICEServer{{URLs: []string{"stun:stun.l.google.com:19302"}}}
	}

	mediaEngine := &webrtc.MediaEngine{}
	if err := mediaEngine.RegisterDefaultCodecs(); err != nil {
		return nil, err
	}

	interceptorRegistry := &interceptor.Registry{}
	if err := webrtc.RegisterDefaultInterceptors(mediaEngine, interceptorRegistry); err != nil {
		return nil, err
	}

	return webrtc.NewAPI(
		webrtc.WithMediaEngine(mediaEngine),
		webrtc.WithInterceptorRegistry(interceptorRegistry),
	).NewPeerConnection(webrtc.Configuration{ICEServers: iceServers})
}

func establishUpstream(stream *WebRTCStream) error {
	config := stream.getConfig()
	decodedURL, err := decodeSignalingURL(config.SignalingURL)
	if err != nil {
		return fmt.Errorf("decode signaling URL: %w", err)
	}

	if whepDebugEnabled() {
		fmt.Println("[WHEP_PROXY] Connecting websocket:", redactURL(decodedURL))
	}

	dialer := *websocket.DefaultDialer
	dialer.HandshakeTimeout = 20 * time.Second
	dialer.EnableCompression = true
	headers := http.Header{
		"User-Agent": {"okhttp/4.12.0"},
	}
	conn, resp, err := dialer.Dial(decodedURL, headers)
	if err != nil {
		if resp != nil {
			if resp.Body != nil {
				defer resp.Body.Close()
			}
			fmt.Println("[WHEP_PROXY] Websocket handshake status:", resp.Status)
			fmt.Println("[WHEP_PROXY] Websocket handshake headers:", resp.Header)
		}
		if resp != nil && resp.Body != nil {
			bodyBytes := make([]byte, 2048)
			if n, readErr := resp.Body.Read(bodyBytes); readErr == nil || readErr == io.EOF {
				fmt.Println("[WHEP_PROXY] Websocket response:", string(bodyBytes[:n]))
			}
		}
		return fmt.Errorf("connect websocket: %w", err)
	}

	peerConnection, err := createPeerConnection(config)
	if err != nil {
		_ = conn.Close()
		return err
	}

	session := &UpstreamSession{
		peerConnection:    peerConnection,
		wsConn:            conn,
		answerReceived:    make(chan struct{}),
		correlationID:     generateCorrelationID(config.PhoneID),
		recipientClientID: config.PhoneID,
		startedAt:         time.Now(),
	}
	stream.setUpstream(session)
	traceLogf(stream.streamID, "upstream connect start signaling=%s", sanitizeLogURL(config.SignalingURL))

	if _, err = peerConnection.AddTransceiverFromKind(
		webrtc.RTPCodecTypeVideo,
		webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly},
	); err != nil {
		stream.handleUpstreamDisconnect(session, fmt.Sprintf("add video transceiver: %v", err))
		return err
	}

	if upstreamVideoOnly(stream.streamID) {
		traceLogf(stream.streamID, "upstream audio transceiver disabled by WHEP_UPSTREAM_VIDEO_ONLY_STREAMS")
	} else {
		if _, err = peerConnection.AddTransceiverFromKind(
			webrtc.RTPCodecTypeAudio,
			webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly},
		); err != nil {
			stream.handleUpstreamDisconnect(session, fmt.Sprintf("add audio transceiver: %v", err))
			return err
		}
	}

	peerConnection.OnICEConnectionStateChange(func(state webrtc.ICEConnectionState) {
		if whepDebugEnabled() || state == webrtc.ICEConnectionStateFailed || state == webrtc.ICEConnectionStateDisconnected {
			log.Printf("[WHEP_PROXY] ICE connection state for %s: %s", stream.streamID, state.String())
		}
		traceLogf(stream.streamID, "upstream ice state=%s after=%s", state.String(), time.Since(session.startedAt).Round(time.Millisecond))
	})
	peerConnection.OnConnectionStateChange(func(state webrtc.PeerConnectionState) {
		if whepDebugEnabled() || state == webrtc.PeerConnectionStateFailed || state == webrtc.PeerConnectionStateDisconnected {
			log.Printf("[WHEP_PROXY] Peer connection state for %s: %s", stream.streamID, state.String())
		}
		traceLogf(stream.streamID, "upstream peer state=%s after=%s", state.String(), time.Since(session.startedAt).Round(time.Millisecond))
		switch state {
		case webrtc.PeerConnectionStateFailed, webrtc.PeerConnectionStateClosed:
			stream.handleUpstreamDisconnect(session, fmt.Sprintf("peer connection state=%s", state.String()))
		}
	})

	peerConnection.OnICECandidate(func(c *webrtc.ICECandidate) {
		if c == nil {
			return
		}
		candidate := c.ToJSON()
		if err := sendSignalingMessage(session, "ICE_CANDIDATE", candidate, session.correlationID); err != nil {
			fmt.Println("[WHEP_PROXY] Error sending ICE candidate:", err)
		}
	})

	peerConnection.OnTrack(func(track *webrtc.TrackRemote, receiver *webrtc.RTPReceiver) {
		if stream.currentUpstream() != session {
			return
		}

		if whepDebugEnabled() {
			log.Printf("[WHEP_PROXY] Received track for %s: codec=%s", stream.streamID, track.Codec().MimeType)
			fmt.Printf(
				"[WHEP_PROXY] Received remote track for %s: kind=%s codec=%s payloadType=%d\n",
				stream.streamID,
				track.Kind().String(),
				track.Codec().MimeType,
				track.PayloadType(),
			)
		}

		var localTrack *webrtc.TrackLocalStaticRTP
		switch track.Kind() {
		case webrtc.RTPCodecTypeVideo:
			stream.setVideoSource(track)
			localTrack = stream.mediaState().videoTrack
			if session.videoTrackLogged.CompareAndSwap(false, true) {
				traceLogf(stream.streamID, "first upstream video track codec=%s payload=%d after=%s", track.Codec().MimeType, track.PayloadType(), time.Since(session.startedAt).Round(time.Millisecond))
			}
			if stream.whepClients.Load() > 0 {
				stream.mediaState().videoPLIRequested.Store(true)
				if err := stream.requestVideoKeyframe("upstream track available"); err != nil {
					log.Printf("[WHEP_PROXY] Failed to request keyframe for %s on upstream track: %v", stream.streamID, err)
				}
			}
		case webrtc.RTPCodecTypeAudio:
			stream.setAudioReady(true)
			localTrack = stream.mediaState().audioTrack
			if session.audioTrackLogged.CompareAndSwap(false, true) {
				traceLogf(stream.streamID, "first upstream audio track codec=%s payload=%d after=%s", track.Codec().MimeType, track.PayloadType(), time.Since(session.startedAt).Round(time.Millisecond))
			}
		default:
			return
		}

		go readReceiverRTCP(stream.streamID, track, receiver)
		go forwardTrack(stream.streamID, stream, session, track, localTrack)

		if track.Kind() == webrtc.RTPCodecTypeVideo {
			go func() {
				ticker := time.NewTicker(60 * time.Second)
				defer ticker.Stop()

				for range ticker.C {
					if stream.currentUpstream() != session || session.peerConnection == nil || session.peerConnection.ConnectionState() == webrtc.PeerConnectionStateClosed {
						return
					}
					if stream.whepClients.Load() == 0 {
						continue
					}
					if err := stream.requestVideoKeyframe("periodic downstream refresh"); err != nil {
						log.Printf("[WHEP_PROXY] Failed to request keyframe for %s: %v", stream.streamID, err)
						return
					}
				}
			}()
		}
	})

	go runUpstreamMessageLoop(stream, session, conn)

	if delayMs := os.Getenv("WHEP_SIGNALING_DELAY_MS"); delayMs != "" {
		if ms, err := strconv.Atoi(delayMs); err == nil && ms > 0 {
			d := time.Duration(ms) * time.Millisecond
			fmt.Printf("[WHEP_PROXY] Waiting %v before sending SDP_OFFER for %s\n", d, stream.streamID)
			time.Sleep(d)
		}
	}
	if err := createAndSendOffer(stream.streamID, session); err != nil {
		stream.handleUpstreamDisconnect(session, fmt.Sprintf("createAndSendOffer failed: %v", err))
		return err
	}
	watchUpstreamAnswer(stream, session, upstreamAnswerTimeout)

	return nil
}
