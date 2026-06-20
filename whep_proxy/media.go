package main

import (
	"fmt"
	"log"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v3"
)

// MediaForwarder holds the per-stream RTP/H.264 forwarding state that was
// previously inlined in the WebRTCStream god-struct.  It owns the local and
// remote track references, the SPS/PPS replay buffers, the downstream sequence
// number counters, and the media-readiness atomics.  WebRTCStream holds it by
// pointer (see WebRTCStream.media) so the file-level split between state.go
// (lifecycle) and media.go (forwarding) is backed by a real type boundary.
type MediaForwarder struct {
	mu sync.RWMutex

	videoSource *webrtc.TrackRemote
	videoTrack  *webrtc.TrackLocalStaticRTP
	audioTrack  *webrtc.TrackLocalStaticRTP
	videoTrackMu sync.Mutex
	audioTrackMu sync.Mutex

	forwardWg sync.WaitGroup

	videoParamPacket  *rtp.Packet
	videoSPSPacket    *rtp.Packet
	videoPPSPacket    *rtp.Packet
	videoSPSBytes     int
	videoPPSBytes     int
	videoParamFUA     []byte
	videoParamFUASeq  uint16
	videoParamFUAOpen bool

	videoOutSeq    uint16
	audioOutSeq    uint16
	videoOutSeqSet bool
	audioOutSeqSet bool

	videoReady        atomic.Bool
	videoPrimed       atomic.Bool
	audioReady        atomic.Bool
	audioPacketsSeen  atomic.Uint64
	videoPLIRequested atomic.Bool
	videoReplayLogged atomic.Bool
	videoIDRLogged    atomic.Bool
	videoParamsMissed atomic.Bool
	videoReplayMisses atomic.Int32
}

func (stream *WebRTCStream) outputTracks() []*webrtc.TrackLocalStaticRTP {
	m := stream.mediaState()
	m.mu.RLock()
	defer m.mu.RUnlock()

	tracks := make([]*webrtc.TrackLocalStaticRTP, 0, 2)
	if !m.videoReady.Load() {
		return tracks
	}
	if m.videoTrack != nil {
		tracks = append(tracks, m.videoTrack)
	}
	if m.audioTrack != nil && m.audioReady.Load() && m.audioPacketsSeen.Load() > 0 {
		tracks = append(tracks, m.audioTrack)
	}
	return tracks
}

func (stream *WebRTCStream) ensureETag() string {
	stream.mediaMu.Lock()
	defer stream.mediaMu.Unlock()

	if stream.etag == "" {
		stream.etag = fmt.Sprintf("\"%x\"", time.Now().UnixNano())
	}
	return stream.etag
}

func (stream *WebRTCStream) requestVideoKeyframe(reason string) error {
	m := stream.mediaState()
	m.mu.RLock()
	videoSource := m.videoSource
	m.mu.RUnlock()
	session := stream.currentUpstream()
	var peerConnection *webrtc.PeerConnection
	if session != nil {
		peerConnection = session.peerConnection
	}

	if videoSource == nil || peerConnection == nil {
		if whepDebugEnabled() {
			log.Printf("[WHEP_PROXY] Skipping keyframe request (%s): video source unavailable", reason)
		}
		return nil
	}

	err := peerConnection.WriteRTCP([]rtcp.Packet{
		&rtcp.PictureLossIndication{MediaSSRC: uint32(videoSource.SSRC())},
	})
	if err != nil {
		return err
	}

	if whepDebugEnabled() && reason != "downstream rtcp feedback" && reason != "periodic downstream refresh" {
		log.Printf("[WHEP_PROXY] Requested keyframe (%s) for SSRC=%d", reason, videoSource.SSRC())
	}
	return nil
}

func h264PacketInfo(payload []byte) (isIDR bool, desc string) {
	if len(payload) == 0 {
		return false, "empty"
	}

	naluType := payload[0] & 0x1F
	switch naluType {
	case 5:
		return true, "single-idr"
	case 24:
		types := make([]string, 0, 4)
		hasIDR := false
		for i := 1; i+2 <= len(payload); {
			naluSize := int(payload[i])<<8 | int(payload[i+1])
			i += 2
			if naluSize <= 0 || i+naluSize > len(payload) {
				break
			}
			aggType := payload[i] & 0x1F
			types = append(types, strconv.Itoa(int(aggType)))
			if aggType == 5 {
				hasIDR = true
			}
			i += naluSize
		}
		if len(types) == 0 {
			return false, "stap-a"
		}
		return hasIDR, "stap-a[" + strings.Join(types, ",") + "]"
	case 28:
		if len(payload) < 2 {
			return false, "fu-a-short"
		}
		start := payload[1]&0x80 != 0
		end := payload[1]&0x40 != 0
		origType := payload[1] & 0x1F
		if origType == 5 && start {
			return true, "fu-a-idr-start"
		}
		if origType == 5 && end {
			return false, "fu-a-idr-end"
		}
		return false, fmt.Sprintf("fu-a-%d", origType)
	default:
		return false, fmt.Sprintf("nalu-%d", naluType)
	}
}

func h264FUAState(payload []byte) (isFUA bool, start bool, end bool) {
	if len(payload) < 2 {
		return false, false, false
	}
	if payload[0]&0x1F != 28 {
		return false, false, false
	}
	return true, payload[1]&0x80 != 0, payload[1]&0x40 != 0
}

func cloneRTPPacket(pkt *rtp.Packet) *rtp.Packet {
	if pkt == nil {
		return nil
	}

	clone := &rtp.Packet{
		Header:      pkt.Header,
		PaddingSize: pkt.PaddingSize,
	}
	clone.CSRC = append([]uint32(nil), pkt.CSRC...)
	clone.Extensions = append([]rtp.Extension(nil), pkt.Extensions...)
	clone.Payload = append([]byte(nil), pkt.Payload...)
	clone.Raw = append([]byte(nil), pkt.Raw...)
	return clone
}

func parseSTAPAParameterSets(payload []byte) (int, int) {
	if len(payload) < 3 {
		return 0, 0
	}

	var spsBytes int
	var ppsBytes int
	for i := 1; i+2 <= len(payload); {
		naluSize := int(payload[i])<<8 | int(payload[i+1])
		i += 2
		if naluSize <= 0 || i+naluSize > len(payload) {
			break
		}

		switch payload[i] & 0x1F {
		case 7:
			spsBytes = naluSize
		case 8:
			ppsBytes = naluSize
		}
		i += naluSize
	}

	return spsBytes, ppsBytes
}

func (stream *WebRTCStream) bufferFragmentedSTAPA(pkt *rtp.Packet) bool {
	if pkt == nil || len(pkt.Payload) < 2 || pkt.Payload[0]&0x1F != 28 {
		return false
	}

	start := pkt.Payload[1]&0x80 != 0
	end := pkt.Payload[1]&0x40 != 0
	origType := pkt.Payload[1] & 0x1F
	if origType != 24 {
		return false
	}

	stream.mediaState().mu.Lock()
	defer stream.mediaState().mu.Unlock()

	if start {
		reconstructedHeader := (pkt.Payload[0] & 0xE0) | origType
		stream.mediaState().videoParamFUA = append([]byte{reconstructedHeader}, pkt.Payload[2:]...)
		stream.mediaState().videoParamFUASeq = pkt.SequenceNumber
		stream.mediaState().videoParamFUAOpen = true
	} else {
		if !stream.mediaState().videoParamFUAOpen || pkt.SequenceNumber != stream.mediaState().videoParamFUASeq+1 {
			stream.mediaState().videoParamFUA = nil
			stream.mediaState().videoParamFUAOpen = false
			return true
		}
		stream.mediaState().videoParamFUA = append(stream.mediaState().videoParamFUA, pkt.Payload[2:]...)
		stream.mediaState().videoParamFUASeq = pkt.SequenceNumber
	}

	if !end {
		return true
	}

	payload := append([]byte(nil), stream.mediaState().videoParamFUA...)
	stream.mediaState().videoParamFUA = nil
	stream.mediaState().videoParamFUAOpen = false
	spsBytes, ppsBytes := parseSTAPAParameterSets(payload)
	if spsBytes == 0 && ppsBytes == 0 {
		return true
	}

	paramPacket := cloneRTPPacket(pkt)
	paramPacket.Payload = payload
	stream.mediaState().videoParamPacket = paramPacket
	stream.mediaState().videoSPSPacket = nil
	stream.mediaState().videoPPSPacket = nil
	stream.mediaState().videoSPSBytes = spsBytes
	stream.mediaState().videoPPSBytes = ppsBytes
	return true
}

func (stream *WebRTCStream) bufferVideoParameterSet(pkt *rtp.Packet) {
	if pkt == nil || len(pkt.Payload) == 0 {
		return
	}
	if stream.bufferFragmentedSTAPA(pkt) {
		return
	}

	naluType := pkt.Payload[0] & 0x1F

	stream.mediaState().mu.Lock()
	defer stream.mediaState().mu.Unlock()

	switch naluType {
	case 7:
		stream.mediaState().videoParamPacket = nil
		stream.mediaState().videoSPSPacket = cloneRTPPacket(pkt)
		stream.mediaState().videoSPSBytes = len(pkt.Payload)
	case 8:
		stream.mediaState().videoParamPacket = nil
		stream.mediaState().videoPPSPacket = cloneRTPPacket(pkt)
		stream.mediaState().videoPPSBytes = len(pkt.Payload)
	case 24:
		spsBytes, ppsBytes := parseSTAPAParameterSets(pkt.Payload)
		if spsBytes == 0 && ppsBytes == 0 {
			return
		}
		if spsBytes > 0 && ppsBytes > 0 {
			stream.mediaState().videoParamPacket = cloneRTPPacket(pkt)
			stream.mediaState().videoSPSPacket = nil
			stream.mediaState().videoPPSPacket = nil
			stream.mediaState().videoSPSBytes = spsBytes
			stream.mediaState().videoPPSBytes = ppsBytes
			return
		}
		if spsBytes > 0 {
			stream.mediaState().videoSPSPacket = cloneRTPPacket(pkt)
			stream.mediaState().videoSPSBytes = spsBytes
		}
		if ppsBytes > 0 {
			stream.mediaState().videoPPSPacket = cloneRTPPacket(pkt)
			stream.mediaState().videoPPSBytes = ppsBytes
		}
	}
}

func (stream *WebRTCStream) replayVideoParameterSets(
	localTrack *webrtc.TrackLocalStaticRTP,
	streamID string,
	timestamp uint32,
) bool {
	stream.mediaState().mu.RLock()
	paramPacket := cloneRTPPacket(stream.mediaState().videoParamPacket)
	spsPacket := cloneRTPPacket(stream.mediaState().videoSPSPacket)
	ppsPacket := cloneRTPPacket(stream.mediaState().videoPPSPacket)
	spsBytes := stream.mediaState().videoSPSBytes
	ppsBytes := stream.mediaState().videoPPSBytes
	stream.mediaState().mu.RUnlock()

	if paramPacket != nil && spsBytes > 0 && ppsBytes > 0 {
		paramPacket.Timestamp = timestamp
		if err := stream.writeLocalTrack(localTrack, paramPacket); err != nil {
			log.Printf("[WHEP_PROXY] Failed replaying STAP-A SPS/PPS before IDR for %s: %v", streamID, err)
			return false
		}
		stream.mediaState().videoPrimed.Store(true)
		if whepDebugEnabled() && stream.mediaState().videoReplayLogged.CompareAndSwap(false, true) {
			log.Printf("[WHEP_PROXY] Replayed SPS (%d bytes) + PPS (%d bytes) before IDR for %s", spsBytes, ppsBytes, streamID)
		}
		stream.mediaState().videoParamsMissed.Store(false)
		return true
	}

	if spsPacket == nil || ppsPacket == nil || spsBytes == 0 || ppsBytes == 0 {
		if stream.mediaState().videoParamsMissed.CompareAndSwap(false, true) {
			log.Printf("[WHEP_PROXY] Missing buffered SPS/PPS before IDR for %s: sps=%d pps=%d", streamID, spsBytes, ppsBytes)
		}
		return false
	}

	spsPacket.Timestamp = timestamp
	ppsPacket.Timestamp = timestamp
	if err := stream.writeLocalTrack(localTrack, spsPacket); err != nil {
		log.Printf("[WHEP_PROXY] Failed replaying SPS before IDR for %s: %v", streamID, err)
		return false
	}
	if err := stream.writeLocalTrack(localTrack, ppsPacket); err != nil {
		log.Printf("[WHEP_PROXY] Failed replaying PPS before IDR for %s: %v", streamID, err)
		return false
	}

	stream.mediaState().videoPrimed.Store(true)
	if whepDebugEnabled() && stream.mediaState().videoReplayLogged.CompareAndSwap(false, true) {
		log.Printf("[WHEP_PROXY] Replayed SPS (%d bytes) + PPS (%d bytes) before IDR for %s", spsBytes, ppsBytes, streamID)
	}
	stream.mediaState().videoParamsMissed.Store(false)
	stream.mediaState().videoReplayMisses.Store(0)
	return true
}

func (stream *WebRTCStream) shouldForwardVideoPacket(pkt *rtp.Packet) bool {
	if pkt == nil {
		return false
	}
	if stream.mediaState().videoPrimed.Load() {
		return true
	}
	isIDR, _ := h264PacketInfo(pkt.Payload)
	if isIDR {
		stream.mediaState().videoPrimed.Store(true)
		return true
	}
	return false
}

func (stream *WebRTCStream) recordVideoReplayFailure() bool {
	return stream.mediaState().videoReplayMisses.Add(1) >= maxVideoParamReplayFailures
}

func (stream *WebRTCStream) writeLocalTrack(localTrack *webrtc.TrackLocalStaticRTP, pkt *rtp.Packet) error {
	if localTrack == nil || pkt == nil {
		return fmt.Errorf("local track or packet unavailable")
	}

	m := stream.mediaState()
	var mu *sync.Mutex
	switch localTrack {
	case m.videoTrack:
		mu = &m.videoTrackMu
	case m.audioTrack:
		mu = &m.audioTrackMu
	}

	if mu != nil {
		mu.Lock()
		defer mu.Unlock()

		// Keep downstream RTP sequence numbers monotonic per local track.
		switch localTrack {
		case m.videoTrack:
			if !m.videoOutSeqSet {
				m.videoOutSeq = pkt.SequenceNumber
				m.videoOutSeqSet = true
			} else {
				m.videoOutSeq++
				pkt.SequenceNumber = m.videoOutSeq
			}
		case m.audioTrack:
			if !m.audioOutSeqSet {
				m.audioOutSeq = pkt.SequenceNumber
				m.audioOutSeqSet = true
			} else {
				m.audioOutSeq++
				pkt.SequenceNumber = m.audioOutSeq
			}
		}
	}

	return localTrack.WriteRTP(pkt)
}

func forwardTrack(
	streamID string,
	stream *WebRTCStream,
	session *UpstreamSession,
	track *webrtc.TrackRemote,
	localTrack *webrtc.TrackLocalStaticRTP,
) {
	m := stream.mediaState()
	m.forwardWg.Add(1)
	defer m.forwardWg.Done()

	var readCount uint64
	var writtenCount uint64
	var droppedCount uint64
	var lastVideoSeq uint16
	var haveLastVideoSeq bool
	var fuActive bool

	for {
		pkt, _, err := track.ReadRTP()
		if err != nil {
			if whepDebugEnabled() || shouldLogTrackEnd(err) {
				log.Printf(
					"[WHEP_PROXY] Track ended for %s (%s): read=%d written=%d dropped=%d err=%v",
					streamID,
					track.Kind().String(),
					readCount,
					writtenCount,
					droppedCount,
					err,
				)
			}
			stream.handleUpstreamDisconnect(session, fmt.Sprintf("%s track ended: %v", track.Kind().String(), err))
			return
		}

		readCount++
		if track.Kind() == webrtc.RTPCodecTypeAudio {
			stream.markAudioPacketSeen()
		}
		videoFUAEnded := false
		if track.Kind() == webrtc.RTPCodecTypeVideo {
			stream.bufferVideoParameterSet(pkt)
			isIDR, packetDesc := h264PacketInfo(pkt.Payload)
			isFUA, fuaStart, fuaEnd := h264FUAState(pkt.Payload)
			if isFUA {
				if haveLastVideoSeq && fuActive && pkt.SequenceNumber != lastVideoSeq+1 {
					fuActive = false
					if whepDebugEnabled() {
						log.Printf("[WHEP_PROXY] Dropping broken FU-A sequence for %s: expected seq=%d got=%d", streamID, lastVideoSeq+1, pkt.SequenceNumber)
					}
				}
				if fuaStart {
					fuActive = true
				} else if !fuActive {
					haveLastVideoSeq = true
					lastVideoSeq = pkt.SequenceNumber
					droppedCount++
					continue
				}
			} else {
				fuActive = false
			}
			videoFUAEnded = isFUA && fuaEnd
			if isIDR {
				if !stream.replayVideoParameterSets(localTrack, streamID, pkt.Timestamp) {
					if stream.recordVideoReplayFailure() {
						log.Printf("[WHEP_PROXY] Reconnecting upstream for %s: missing SPS/PPS across %d consecutive IDR frames", streamID, maxVideoParamReplayFailures)
						stream.handleUpstreamDisconnect(session, "missing SPS/PPS across consecutive IDR frames")
						return
					}
					droppedCount++
					continue
				}
				if whepDebugEnabled() && m.videoIDRLogged.CompareAndSwap(false, true) {
					log.Printf(
						"[WHEP_PROXY] First IDR for %s: seq=%d marker=%t bytes=%d desc=%s",
						streamID,
						pkt.SequenceNumber,
						pkt.Marker,
						len(pkt.Payload),
						packetDesc,
					)
				}
			} else if !stream.shouldForwardVideoPacket(pkt) {
				droppedCount++
				continue
			}
			haveLastVideoSeq = true
			lastVideoSeq = pkt.SequenceNumber
		}
		if stream.whepClients.Load() == 0 {
			droppedCount++
		} else if err = stream.writeLocalTrack(localTrack, pkt); err != nil {
			droppedCount++
		} else {
			writtenCount++
			if track.Kind() == webrtc.RTPCodecTypeVideo && m.videoPLIRequested.CompareAndSwap(true, false) {
				if pliErr := stream.requestVideoKeyframe("first downstream write"); pliErr != nil {
					log.Printf("[WHEP_PROXY] Failed to request keyframe for %s after first write: %v", streamID, pliErr)
					m.videoPLIRequested.Store(true)
				}
			}
			if track.Kind() == webrtc.RTPCodecTypeVideo && videoFUAEnded {
				fuActive = false
			}
		}

		if whepDebugEnabled() && readCount%20000 == 0 {
			log.Printf(
				"[WHEP_PROXY] RTP stats for %s (%s): read=%d written=%d dropped=%d clients=%d",
				streamID,
				track.Kind().String(),
				readCount,
				writtenCount,
				droppedCount,
				stream.whepClients.Load(),
			)
		}
	}
}

func readReceiverRTCP(streamID string, track *webrtc.TrackRemote, receiver *webrtc.RTPReceiver) {
	for {
		pkts, _, err := receiver.ReadRTCP()
		if err != nil {
			if whepDebugEnabled() || shouldLogRTCPEnd(err) {
				log.Printf("[WHEP_PROXY] Receiver RTCP ended for %s (%s): %v", streamID, track.Kind().String(), err)
			}
			return
		}

		for _, pkt := range pkts {
			switch pkt.(type) {
			case *rtcp.PictureLossIndication, *rtcp.FullIntraRequest:
				if whepDebugEnabled() {
					log.Printf("[WHEP_PROXY] Upstream RTCP feedback for %s (%s): %T", streamID, track.Kind().String(), pkt)
				}
			}
		}
	}
}
