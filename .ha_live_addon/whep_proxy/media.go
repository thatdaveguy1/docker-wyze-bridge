package main

import (
	"fmt"
	"log"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v3"
)

func (stream *WebRTCStream) outputTracks() []*webrtc.TrackLocalStaticRTP {
	stream.mediaMu.RLock()
	defer stream.mediaMu.RUnlock()

	tracks := make([]*webrtc.TrackLocalStaticRTP, 0, 2)
	if !stream.videoReady.Load() {
		return tracks
	}
	if stream.videoTrack != nil {
		tracks = append(tracks, stream.videoTrack)
	}
	if stream.audioTrack != nil && stream.audioReady.Load() && stream.audioPacketsSeen.Load() > 0 {
		tracks = append(tracks, stream.audioTrack)
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
	stream.mediaMu.RLock()
	videoSource := stream.videoSource
	stream.mediaMu.RUnlock()
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

	stream.mediaMu.Lock()
	defer stream.mediaMu.Unlock()

	if start {
		reconstructedHeader := (pkt.Payload[0] & 0xE0) | origType
		stream.videoParamFUA = append([]byte{reconstructedHeader}, pkt.Payload[2:]...)
		stream.videoParamFUASeq = pkt.SequenceNumber
		stream.videoParamFUAOpen = true
	} else {
		if !stream.videoParamFUAOpen || pkt.SequenceNumber != stream.videoParamFUASeq+1 {
			stream.videoParamFUA = nil
			stream.videoParamFUAOpen = false
			return true
		}
		stream.videoParamFUA = append(stream.videoParamFUA, pkt.Payload[2:]...)
		stream.videoParamFUASeq = pkt.SequenceNumber
	}

	if !end {
		return true
	}

	payload := append([]byte(nil), stream.videoParamFUA...)
	stream.videoParamFUA = nil
	stream.videoParamFUAOpen = false
	spsBytes, ppsBytes := parseSTAPAParameterSets(payload)
	if spsBytes == 0 && ppsBytes == 0 {
		return true
	}

	paramPacket := cloneRTPPacket(pkt)
	paramPacket.Payload = payload
	stream.videoParamPacket = paramPacket
	stream.videoSPSPacket = nil
	stream.videoPPSPacket = nil
	stream.videoSPSBytes = spsBytes
	stream.videoPPSBytes = ppsBytes
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

	stream.mediaMu.Lock()
	defer stream.mediaMu.Unlock()

	switch naluType {
	case 7:
		stream.videoParamPacket = nil
		stream.videoSPSPacket = cloneRTPPacket(pkt)
		stream.videoSPSBytes = len(pkt.Payload)
	case 8:
		stream.videoParamPacket = nil
		stream.videoPPSPacket = cloneRTPPacket(pkt)
		stream.videoPPSBytes = len(pkt.Payload)
	case 24:
		spsBytes, ppsBytes := parseSTAPAParameterSets(pkt.Payload)
		if spsBytes == 0 && ppsBytes == 0 {
			return
		}
		if spsBytes > 0 && ppsBytes > 0 {
			stream.videoParamPacket = cloneRTPPacket(pkt)
			stream.videoSPSPacket = nil
			stream.videoPPSPacket = nil
			stream.videoSPSBytes = spsBytes
			stream.videoPPSBytes = ppsBytes
			return
		}
		if spsBytes > 0 {
			stream.videoSPSPacket = cloneRTPPacket(pkt)
			stream.videoSPSBytes = spsBytes
		}
		if ppsBytes > 0 {
			stream.videoPPSPacket = cloneRTPPacket(pkt)
			stream.videoPPSBytes = ppsBytes
		}
	}
}

func (stream *WebRTCStream) replayVideoParameterSets(
	localTrack *webrtc.TrackLocalStaticRTP,
	streamID string,
	timestamp uint32,
) bool {
	stream.mediaMu.RLock()
	paramPacket := cloneRTPPacket(stream.videoParamPacket)
	spsPacket := cloneRTPPacket(stream.videoSPSPacket)
	ppsPacket := cloneRTPPacket(stream.videoPPSPacket)
	spsBytes := stream.videoSPSBytes
	ppsBytes := stream.videoPPSBytes
	stream.mediaMu.RUnlock()

	if paramPacket != nil && spsBytes > 0 && ppsBytes > 0 {
		paramPacket.Timestamp = timestamp
		if err := stream.writeLocalTrack(localTrack, paramPacket); err != nil {
			log.Printf("[WHEP_PROXY] Failed replaying STAP-A SPS/PPS before IDR for %s: %v", streamID, err)
			return false
		}
		stream.videoPrimed.Store(true)
		if whepDebugEnabled() && stream.videoReplayLogged.CompareAndSwap(false, true) {
			log.Printf("[WHEP_PROXY] Replayed SPS (%d bytes) + PPS (%d bytes) before IDR for %s", spsBytes, ppsBytes, streamID)
		}
		stream.videoParamsMissed.Store(false)
		return true
	}

	if spsPacket == nil || ppsPacket == nil || spsBytes == 0 || ppsBytes == 0 {
		if stream.videoParamsMissed.CompareAndSwap(false, true) {
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

	stream.videoPrimed.Store(true)
	if whepDebugEnabled() && stream.videoReplayLogged.CompareAndSwap(false, true) {
		log.Printf("[WHEP_PROXY] Replayed SPS (%d bytes) + PPS (%d bytes) before IDR for %s", spsBytes, ppsBytes, streamID)
	}
	stream.videoParamsMissed.Store(false)
	stream.videoReplayMisses.Store(0)
	return true
}

func (stream *WebRTCStream) shouldForwardVideoPacket(pkt *rtp.Packet) bool {
	if pkt == nil {
		return false
	}
	if stream.videoPrimed.Load() {
		return true
	}
	isIDR, _ := h264PacketInfo(pkt.Payload)
	if isIDR {
		stream.videoPrimed.Store(true)
		return true
	}
	return false
}

func (stream *WebRTCStream) recordVideoReplayFailure() bool {
	return stream.videoReplayMisses.Add(1) >= maxVideoParamReplayFailures
}

func (stream *WebRTCStream) writeLocalTrack(localTrack *webrtc.TrackLocalStaticRTP, pkt *rtp.Packet) error {
	if localTrack == nil || pkt == nil {
		return fmt.Errorf("local track or packet unavailable")
	}

	var mu *sync.Mutex
	switch localTrack {
	case stream.videoTrack:
		mu = &stream.videoTrackMu
	case stream.audioTrack:
		mu = &stream.audioTrackMu
	}

	if mu != nil {
		mu.Lock()
		defer mu.Unlock()

		// Keep downstream RTP sequence numbers monotonic per local track.
		switch localTrack {
		case stream.videoTrack:
			if !stream.videoOutSeqSet {
				stream.videoOutSeq = pkt.SequenceNumber
				stream.videoOutSeqSet = true
			} else {
				stream.videoOutSeq++
				pkt.SequenceNumber = stream.videoOutSeq
			}
		case stream.audioTrack:
			if !stream.audioOutSeqSet {
				stream.audioOutSeq = pkt.SequenceNumber
				stream.audioOutSeqSet = true
			} else {
				stream.audioOutSeq++
				pkt.SequenceNumber = stream.audioOutSeq
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
	stream.forwardWg.Add(1)
	defer stream.forwardWg.Done()

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
				if whepDebugEnabled() && stream.videoIDRLogged.CompareAndSwap(false, true) {
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
			if track.Kind() == webrtc.RTPCodecTypeVideo && stream.videoPLIRequested.CompareAndSwap(true, false) {
				if pliErr := stream.requestVideoKeyframe("first downstream write"); pliErr != nil {
					log.Printf("[WHEP_PROXY] Failed to request keyframe for %s after first write: %v", streamID, pliErr)
					stream.videoPLIRequested.Store(true)
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
