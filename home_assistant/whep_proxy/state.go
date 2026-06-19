package main

import (
	"fmt"
	"log"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/rtp"
	"github.com/pion/webrtc/v3"
)

// WebRTCStream is the per-stream state object.  It carries lifecycle flags,
// media readiness atomics, upstream session state, and RTP forwarding buffers.
// The architecture review (candidate #1) identifies this as a god-struct that
// mixes 7 conceptual modules; future refactors will split it into separate
// types behind interfaces.  For now the file-level seams (state.go,
// upstream.go, media.go, kvs_config.go, handlers.go) make the boundaries
// visible without changing behavior.
type WebRTCStream struct {
	streamID          string
	configMu          sync.RWMutex
	config            WebRTCConfig
	upstreamMu        sync.RWMutex
	upstream          *UpstreamSession
	mediaMu           sync.RWMutex
	etag              string
	videoTrack        *webrtc.TrackLocalStaticRTP
	audioTrack        *webrtc.TrackLocalStaticRTP
	videoTrackMu      sync.Mutex
	audioTrackMu      sync.Mutex
	forwardWg         sync.WaitGroup
	videoSource       *webrtc.TrackRemote
	whepClients       atomic.Int32
	videoPLIRequested atomic.Bool
	videoParamPacket  *rtp.Packet
	videoSPSPacket    *rtp.Packet
	videoPPSPacket    *rtp.Packet
	videoSPSBytes     int
	videoPPSBytes     int
	videoParamFUA     []byte
	videoParamFUASeq  uint16
	videoParamFUAOpen bool
	videoOutSeq       uint16
	audioOutSeq       uint16
	videoOutSeqSet    bool
	audioOutSeqSet    bool
	videoReady        atomic.Bool
	videoPrimed       atomic.Bool
	audioReady        atomic.Bool
	audioPacketsSeen  atomic.Uint64
	upstreamAlive     atomic.Bool
	reconnecting      atomic.Bool
	reconnectAttempts atomic.Int32
	destroyed         atomic.Bool
	videoReplayLogged atomic.Bool
	videoIDRLogged    atomic.Bool
	videoParamsMissed atomic.Bool
	videoReplayMisses atomic.Int32
	// streamCreatedAt is set once when the stream is first registered and never
	// reset.  Together with hasEverHadMedia it lets canReuse() detect streams
	// that have been wedged since birth (upstream never reaches "connected") and
	// force a destroy/recreate cycle instead of recycling them indefinitely.
	streamCreatedAt   time.Time
	recoveryStartedAt time.Time
	hasEverHadMedia   atomic.Bool
	staleLogged       atomic.Bool
}

var streams = make(map[string]*WebRTCStream)
var streamsMu sync.Mutex

const startupReuseWindow = 20 * time.Second

// maxNoMediaAge is the wall-clock age at which a stream that has never
// produced any media is considered permanently wedged and declared non-reusable,
// forcing a destroy/recreate cycle on the next config POST from the Python
// bridge.  Two minutes is generous: a healthy KVS camera connects in < 30s.
const maxNoMediaAge = 2 * time.Minute
const maxRecoveryAge = 90 * time.Second
const maxNoVideoReconnectAttempts int32 = 3
const maxVideoParamReplayFailures int32 = 3
const streamHealthCheckInterval = 30 * time.Second
const upstreamAnswerTimeout = 90 * time.Second

func (stream *WebRTCStream) canReuse() bool {
	if stream == nil || stream.destroyed.Load() {
		return false
	}
	if stream.hasEverHadMedia.Load() {
		stream.mediaMu.RLock()
		recoveryStartedAt := stream.recoveryStartedAt
		stream.mediaMu.RUnlock()
		if !recoveryStartedAt.IsZero() && time.Since(recoveryStartedAt) > maxRecoveryAge {
			if stream.staleLogged.CompareAndSwap(false, true) {
				log.Printf("[WHEP_PROXY] Stream %s declared stale: recovery exceeded %s",
					stream.streamID, maxRecoveryAge)
			}
			return false
		}
	}
	// Hard timeout: if no media has ever flowed and the stream has been alive
	// longer than maxNoMediaAge, declare it stale.  This breaks the perpetual
	// "new" wedge where each reconnect attempt resets the per-session startedAt
	// clock, keeping startupReuseWindow alive forever.  Once hasEverHadMedia is
	// true this guard is skipped — existing reconnect / startup-window logic
	// handles temporary media loss.
	if !stream.hasEverHadMedia.Load() && !stream.streamCreatedAt.IsZero() &&
		time.Since(stream.streamCreatedAt) > maxNoMediaAge {
		if stream.staleLogged.CompareAndSwap(false, true) {
			log.Printf("[WHEP_PROXY] Stream %s declared stale: no media in %s (maxNoMediaAge=%s)",
				stream.streamID, time.Since(stream.streamCreatedAt).Round(time.Second), maxNoMediaAge)
		}
		return false
	}
	if stream.videoReady.Load() || stream.audioReady.Load() {
		return true
	}
	if stream.reconnecting.Load() {
		return true
	}
	// If no media has ever flowed but the stream is still within maxNoMediaAge,
	// keep it alive so the upstream goroutine has time to finish ICE/DTLS
	// negotiation.  Without this guard the 20-second startupReuseWindow check
	// below fires first, causing repeated stale-replace cycles that never let
	// any single connection attempt run to completion.
	if !stream.hasEverHadMedia.Load() && !stream.streamCreatedAt.IsZero() &&
		time.Since(stream.streamCreatedAt) < maxNoMediaAge {
		return true
	}
	session := stream.currentUpstream()
	if session == nil || session.startedAt.IsZero() {
		return false
	}
	return time.Since(session.startedAt) < startupReuseWindow
}

func (stream *WebRTCStream) markReconnectAttempt(attempt int) {
	if !stream.videoReady.Load() && !stream.hasEverHadMedia.Load() {
		stream.reconnectAttempts.Add(1)
		return
	}
	stream.reconnectAttempts.Store(int32(attempt))
}

func (stream *WebRTCStream) clearReconnectMetrics() {
	stream.reconnectAttempts.Store(0)
}

func (stream *WebRTCStream) shouldForceRecreateNoVideo() bool {
	if stream == nil || stream.destroyed.Load() {
		return false
	}
	return stream.reconnecting.Load() &&
		!stream.videoReady.Load() &&
		!stream.hasEverHadMedia.Load() &&
		stream.reconnectAttempts.Load() > maxNoVideoReconnectAttempts
}

func (stream *WebRTCStream) setVideoSource(track *webrtc.TrackRemote) {
	stream.mediaMu.Lock()
	defer stream.mediaMu.Unlock()
	stream.videoSource = track
	stream.videoReady.Store(track != nil)
	if track != nil {
		// Once we have a video track the stream has produced real media; disable
		// the maxNoMediaAge guard for the rest of this stream's lifetime.
		stream.hasEverHadMedia.Store(true)
		stream.recoveryStartedAt = time.Time{}
		stream.clearReconnectMetrics()
	}
}

func (stream *WebRTCStream) setAudioReady(ready bool) {
	stream.audioReady.Store(ready)
}

func (stream *WebRTCStream) markAudioPacketSeen() {
	stream.audioPacketsSeen.Add(1)
}

func (stream *WebRTCStream) status() map[string]interface{} {
	upstreamState := ""
	if session := stream.currentUpstream(); session != nil && session.peerConnection != nil {
		upstreamState = session.peerConnection.ConnectionState().String()
	}
	stream.mediaMu.RLock()
	recoveryStartedAt := stream.recoveryStartedAt
	stream.mediaMu.RUnlock()
	if !recoveryStartedAt.IsZero() && upstreamState == webrtc.PeerConnectionStateNew.String() {
		upstreamState = "recovering"
	}

	streamAgeSec := 0.0
	if !stream.streamCreatedAt.IsZero() {
		streamAgeSec = time.Since(stream.streamCreatedAt).Seconds()
	}

	return map[string]interface{}{
		"upstream_state":     upstreamState,
		"upstream_alive":     stream.upstreamAlive.Load(),
		"can_reuse":          stream.canReuse(),
		"video_ready":        stream.videoReady.Load(),
		"audio_ready":        stream.audioReady.Load(),
		"audio_packets_seen": stream.audioPacketsSeen.Load(),
		"whep_clients":       stream.whepClients.Load(),
		"has_ever_had_media": stream.hasEverHadMedia.Load(),
		"stream_age_sec":     streamAgeSec,
	}
}

func (stream *WebRTCStream) resetUpstreamMediaState() {
	if stream.hasEverHadMedia.Load() {
		stream.mediaMu.Lock()
		stream.videoSource = nil
		if stream.recoveryStartedAt.IsZero() {
			stream.recoveryStartedAt = time.Now()
		}
		stream.videoParamPacket = nil
		stream.videoSPSPacket = nil
		stream.videoPPSPacket = nil
		stream.videoSPSBytes = 0
		stream.videoPPSBytes = 0
		stream.videoParamFUA = nil
		stream.videoParamFUASeq = 0
		stream.videoParamFUAOpen = false
		stream.mediaMu.Unlock()
		stream.videoPLIRequested.Store(false)
		stream.videoPrimed.Store(false)
		stream.videoReplayLogged.Store(false)
		stream.videoIDRLogged.Store(false)
		stream.videoParamsMissed.Store(false)
		stream.videoReplayMisses.Store(0)
		return
	}

	stream.setVideoSource(nil)
	stream.setAudioReady(false)
	stream.videoPLIRequested.Store(false)
	stream.videoPrimed.Store(false)
	stream.videoReplayLogged.Store(false)
	stream.videoIDRLogged.Store(false)
	stream.videoParamsMissed.Store(false)
	stream.mediaMu.Lock()
	stream.videoParamPacket = nil
	stream.videoSPSPacket = nil
	stream.videoPPSPacket = nil
	stream.videoSPSBytes = 0
	stream.videoPPSBytes = 0
	stream.videoParamFUA = nil
	stream.videoParamFUASeq = 0
	stream.videoParamFUAOpen = false
	stream.mediaMu.Unlock()
	stream.audioPacketsSeen.Store(0)
	stream.videoReplayMisses.Store(0)
}

func cleanupUpstreamLocked(stream *WebRTCStream) {
	current := stream.upstream
	stream.upstream = nil
	stream.upstreamAlive.Store(false)
	stream.resetUpstreamMediaState()
	closeUpstreamSession(current)
}

func cleanupUpstreamIfCurrent(stream *WebRTCStream, session *UpstreamSession) bool {
	if !stream.clearUpstreamIfCurrent(session) {
		return false
	}
	stream.resetUpstreamMediaState()
	closeUpstreamSession(session)
	return true
}

func destroyStreamLocked(streamID string, stream *WebRTCStream) {
	stream.destroyed.Store(true)
	cleanupUpstreamLocked(stream)
	delete(streams, streamID)
}

func destroyStream(streamID string, stream *WebRTCStream) {
	streamsMu.Lock()
	defer streamsMu.Unlock()
	destroyStreamLocked(streamID, stream)
}

func destroyStreamIfCurrent(streamID string, stream *WebRTCStream) {
	streamsMu.Lock()
	defer streamsMu.Unlock()
	if current, ok := streams[streamID]; ok && current == stream {
		destroyStreamLocked(streamID, stream)
	}
}

func reapStaleStreams() int {
	type candidate struct {
		id     string
		stream *WebRTCStream
		reason string
	}

	candidates := make([]candidate, 0)
	streamsMu.Lock()
	for streamID, stream := range streams {
		switch {
		case stream.shouldForceRecreateNoVideo():
			candidates = append(candidates, candidate{
				id:     streamID,
				stream: stream,
				reason: fmt.Sprintf("no video after %d reconnect attempts", stream.reconnectAttempts.Load()),
			})
		case !stream.canReuse():
			candidates = append(candidates, candidate{
				id:     streamID,
				stream: stream,
				reason: "stale stream is no longer reusable",
			})
		}
	}
	streamsMu.Unlock()

	for _, c := range candidates {
		if err := recreateStreamFn(c.id, c.stream, c.reason); err != nil {
			log.Printf("[WHEP_PROXY] Failed to recreate stale stream %s: %v", c.id, err)
		}
	}
	return len(candidates)
}

func runStreamHealthReaper(stop <-chan struct{}) {
	ticker := time.NewTicker(streamHealthCheckInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			reapStaleStreams()
		case <-stop:
			return
		}
	}
}

func (stream *WebRTCStream) scheduleReconnect(reason string) {
	if stream.destroyed.Load() {
		return
	}
	if !stream.reconnecting.CompareAndSwap(false, true) {
		return
	}

	go func() {
		stream.forwardWg.Wait()
		for attempt := 1; ; attempt++ {
			if stream.destroyed.Load() {
				stream.reconnecting.Store(false)
				return
			}
			stream.markReconnectAttempt(attempt)
			if stream.shouldForceRecreateNoVideo() {
				stream.reconnecting.Store(false)
				reason := fmt.Sprintf("no video after %d reconnect attempts", attempt)
				if err := recreateStreamFn(stream.streamID, stream, reason); err != nil {
					log.Printf("[WHEP_PROXY] Failed to recreate %s after reconnect wedge: %v", stream.streamID, err)
				}
				return
			}

			delay := time.Duration(attempt*2) * time.Second
			if delay > 30*time.Second {
				delay = 30 * time.Second
			}
			log.Printf("[WHEP_PROXY] Reconnecting upstream for %s, attempt %d in %s (reason=%s)", stream.streamID, attempt, delay, reason)
			time.Sleep(delay)

			config, err := fetchKVSConfig(stream.streamID)
			if err != nil {
				if isTerminalRefreshError(err) {
					log.Printf("[WHEP_PROXY] Stopping reconnect for %s: %v", stream.streamID, err)
					stream.reconnecting.Store(false)
					destroyStreamIfCurrent(stream.streamID, stream)
					return
				}
				log.Printf("[WHEP_PROXY] Failed to refresh KVS config for %s: %v", stream.streamID, err)
				continue
			}
			stream.setConfig(config)

			if err := establishUpstream(stream); err != nil {
				log.Printf("[WHEP_PROXY] Reconnect attempt %d failed for %s: %v", attempt, stream.streamID, err)
				continue
			}

			log.Printf("[WHEP_PROXY] Upstream reconnected for %s on attempt %d", stream.streamID, attempt)
			stream.reconnecting.Store(false)
			return
		}
	}()
}

func (stream *WebRTCStream) handleUpstreamDisconnect(session *UpstreamSession, reason string) {
	if stream.destroyed.Load() {
		return
	}
	if !cleanupUpstreamIfCurrent(stream, session) {
		return
	}
	if whepDebugEnabled() || !strings.Contains(reason, "websocket closed: websocket: close 1001") {
		log.Printf("[WHEP_PROXY] Upstream session ended for %s: %s", stream.streamID, reason)
	}
	stream.scheduleReconnect(reason)
}
