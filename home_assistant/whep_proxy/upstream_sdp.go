package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"time"

	"github.com/pion/webrtc/v3"
)

func rewriteSessionLine(sdp, correlationID string) string {
	if correlationID == "" {
		return sdp
	}
	rewritten := strings.Replace(sdp, "s=-\r\n", "s="+correlationID+"\r\n", 1)
	if rewritten != sdp {
		return rewritten
	}
	return strings.Replace(sdp, "s=-\n", "s="+correlationID+"\n", 1)
}

func handleRemoteAnswer(streamID string, session *UpstreamSession, msg map[string]interface{}) error {
	decoded, err := decodeSignalingPayload(msg)
	if err != nil {
		return err
	}

	var answer webrtc.SessionDescription
	if err := json.Unmarshal(decoded, &answer); err != nil {
		return fmt.Errorf("unmarshal SDP_ANSWER: %w", err)
	}

	if whepDebugEnabled() {
		fmt.Println("[WHEP_PROXY] Received SDP_ANSWER for", streamID)
	}

	answer.SDP = strings.ReplaceAll(answer.SDP, "\\r\\n", "\r\n")
	if err := session.peerConnection.SetRemoteDescription(answer); err != nil {
		return fmt.Errorf("set remote description: %w", err)
	}
	session.remoteDescription = &answer
	markAnswerReceived(session)

	for _, candidate := range session.pendingCandidates {
		if err := session.peerConnection.AddICECandidate(candidate); err != nil {
			fmt.Println("[WHEP_PROXY] Failed to add queued ICE candidate:", err)
		}
	}
	session.pendingCandidates = nil
	return nil
}

func markAnswerReceived(session *UpstreamSession) {
	if session == nil || session.answerReceived == nil {
		return
	}
	session.answerOnce.Do(func() { close(session.answerReceived) })
}

func watchUpstreamAnswer(stream *WebRTCStream, session *UpstreamSession, timeout time.Duration) {
	if stream == nil || session == nil || session.answerReceived == nil || timeout <= 0 {
		return
	}
	go func() {
		select {
		case <-session.answerReceived:
			return
		case <-time.After(timeout):
		}

		if stream.currentUpstream() != session || stream.destroyed.Load() {
			return
		}
		if session.remoteDescription != nil {
			return
		}

		reason := fmt.Sprintf("upstream SDP answer timeout after %s", timeout)
		log.Printf("[WHEP_PROXY] %s for %s", reason, stream.streamID)
		traceLogf(stream.streamID, "%s", reason)
		stream.handleUpstreamDisconnect(session, reason)
	}()
}

func handleRemoteCandidate(streamID string, session *UpstreamSession, msg map[string]interface{}) error {
	decoded, err := decodeSignalingPayload(msg)
	if err != nil {
		return err
	}

	var candidateMap map[string]interface{}
	if err := json.Unmarshal(decoded, &candidateMap); err != nil {
		return fmt.Errorf("unmarshal ICE_CANDIDATE: %w", err)
	}

	candidateString, ok := candidateMap["candidate"].(string)
	if !ok || candidateString == "" {
		return fmt.Errorf("candidate string missing")
	}

	candidate := webrtc.ICECandidateInit{Candidate: candidateString}
	if sdpMid, ok := candidateMap["sdpMid"].(string); ok && sdpMid != "" {
		candidate.SDPMid = &sdpMid
	}
	if mLineIndex, ok := candidateMap["sdpMLineIndex"].(float64); ok {
		uint16Val := uint16(mLineIndex)
		candidate.SDPMLineIndex = &uint16Val
	}

	if session.remoteDescription == nil {
		session.pendingCandidates = append(session.pendingCandidates, candidate)
		fmt.Println("[WHEP_PROXY] Queued ICE_CANDIDATE for", streamID)
		return nil
	}

	fmt.Println("[WHEP_PROXY] Received ICE_CANDIDATE for", streamID)
	return session.peerConnection.AddICECandidate(candidate)
}

func createAndSendOffer(streamID string, session *UpstreamSession) error {
	if session == nil || session.peerConnection == nil {
		return fmt.Errorf("upstream peer connection unavailable")
	}
	offer, err := session.peerConnection.CreateOffer(nil)
	if err != nil {
		return fmt.Errorf("create offer: %w", err)
	}
	if err := session.peerConnection.SetLocalDescription(offer); err != nil {
		return fmt.Errorf("set local description: %w", err)
	}

	// Wait for ICE gathering to complete so the offer SDP includes local candidates.
	// Some KVS/camera implementations expect at least one candidate before responding.
	gatherComplete := webrtc.GatheringCompletePromise(session.peerConnection)
	select {
	case <-gatherComplete:
		// done
	case <-time.After(10 * time.Second):
		fmt.Println("[WHEP_PROXY] ICE gathering timeout for", streamID, "; sending offer anyway")
	}

	localDescription := session.peerConnection.LocalDescription()
	if localDescription == nil {
		return fmt.Errorf("local description unavailable after SetLocalDescription")
	}

	envelope := map[string]interface{}{
		"type": "offer",
		"sdp":  rewriteSessionLine(localDescription.SDP, session.correlationID),
	}
	if whepDebugEnabled() {
		if decoded, err := json.Marshal(envelope); err == nil {
			fmt.Println("[WHEP_PROXY] SDP_OFFER payload for", streamID, string(decoded))
		}
		if payload, err := json.Marshal(map[string]interface{}{
			"action":         "SDP_OFFER",
			"messagePayload": base64.StdEncoding.EncodeToString(mustJSON(envelope)),
			"correlationId":  session.correlationID,
		}); err == nil {
			fmt.Println("[WHEP_PROXY] Sending envelope for", streamID, string(payload))
		}
	}
	return sendSignalingMessage(session, "SDP_OFFER", envelope, session.correlationID)
}
