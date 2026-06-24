package main

import (
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"strings"

	"github.com/gorilla/websocket"
	"github.com/pion/webrtc/v3"
)

func classifyWSReadError(err error) wsCloseInfo {
	var closeErr *websocket.CloseError
	if errors.As(err, &closeErr) {
		reason := strings.TrimSpace(closeErr.Text)
		return wsCloseInfo{code: closeErr.Code, reason: reason, normal: closeErr.Code == websocket.CloseNormalClosure || closeErr.Code == websocket.CloseGoingAway}
	}
	if errors.Is(err, io.EOF) {
		return wsCloseInfo{reason: "EOF"}
	}
	return wsCloseInfo{}
}

func closeNormalRotationWebsocket(session *UpstreamSession) {
	if session == nil {
		return
	}

	session.wsMu.Lock()
	defer session.wsMu.Unlock()
	if session.wsConn == nil {
		return
	}
	_ = session.wsConn.Close()
	session.wsConn = nil
}

func runUpstreamMessageLoop(stream *WebRTCStream, session *UpstreamSession, conn *websocket.Conn) {
	for {
		messageType, data, err := conn.ReadMessage()
		if err != nil {
			closeInfo := classifyWSReadError(err)
			state := webrtc.PeerConnectionStateClosed
			iceState := webrtc.ICEConnectionStateClosed
			if session.peerConnection != nil {
				state = session.peerConnection.ConnectionState()
				iceState = session.peerConnection.ICEConnectionState()
			}
			videoReady := stream.mediaState().videoReady.Load()
			audioReady := stream.mediaState().audioReady.Load()
			upstreamAlive := stream.upstreamAlive.Load()
			whepClients := stream.whepClients.Load()
			if closeInfo.normal {
				logNormalClose(session, stream.streamID, closeInfo, state, iceState, videoReady, audioReady, upstreamAlive, whepClients)
				if !shouldReconnectOnNormalWSClosure(state, videoReady, audioReady) {
					log.Printf(
						"[WHEP_PROXY] Keeping upstream peer alive for %s after normal websocket close: peer=%s video_ready=%t audio_ready=%t",
						stream.streamID,
						state.String(),
						videoReady,
						audioReady,
					)
					closeNormalRotationWebsocket(session)
					return
				}
			} else if closeInfo.code != 0 {
				log.Printf("[WHEP_PROXY] Websocket closed by peer for %s: code=%d reason=%q", stream.streamID, closeInfo.code, closeInfo.reason)
			} else if !errors.Is(err, io.EOF) {
				log.Printf("[WHEP_PROXY] Error reading websocket message for %s: %v", stream.streamID, err)
			} else if whepDebugEnabled() {
				log.Printf("[WHEP_PROXY] Websocket read EOF for %s", stream.streamID)
			}
			stream.handleUpstreamDisconnect(session, fmt.Sprintf("websocket closed: %v", err))
			return
		}

		if len(data) == 0 {
			if whepDebugEnabled() {
				log.Println("[WHEP_PROXY] Skipping empty keepalive frame")
			}
			continue
		}

		if whepDebugEnabled() {
			const rawLogLen = 200
			msgTypeStr := "other"
			switch messageType {
			case websocket.TextMessage:
				msgTypeStr = "text"
			case websocket.BinaryMessage:
				msgTypeStr = "binary"
			}
			sampleLen := len(data)
			if sampleLen > rawLogLen {
				sampleLen = rawLogLen
			}
			if messageType == websocket.BinaryMessage && sampleLen > 0 {
				fmt.Printf("[WHEP_PROXY] raw message type=%s len=%d first%d_hex=%s\n", msgTypeStr, len(data), sampleLen, hex.EncodeToString(data[:sampleLen]))
			} else if sampleLen > 0 {
				payload := string(data[:sampleLen])
				if strings.ContainsAny(payload, "\r\n") {
					payload = strings.ReplaceAll(strings.ReplaceAll(payload, "\r", "\\r"), "\n", "\\n")
				}
				fmt.Printf("[WHEP_PROXY] raw message type=%s len=%d first%d=%s\n", msgTypeStr, len(data), sampleLen, payload)
			} else {
				fmt.Printf("[WHEP_PROXY] raw message type=%s len=0\n", msgTypeStr)
			}
		}

		var jsonData []byte
		switch messageType {
		case websocket.TextMessage:
			jsonData = data
		case websocket.BinaryMessage:
			if jsonErr := json.Unmarshal(data, &(map[string]interface{}{})); jsonErr == nil {
				jsonData = data
			} else {
				decoded, decErr := base64.StdEncoding.DecodeString(string(data))
				if decErr != nil {
					fmt.Printf("[WHEP_PROXY] Binary message: not valid JSON (%v) and base64 decode failed: %v\n", jsonErr, decErr)
					continue
				}
				jsonData = decoded
			}
		default:
			fmt.Printf("[WHEP_PROXY] Ignoring websocket message type %d\n", messageType)
			continue
		}

		var msg map[string]interface{}
		if err := json.Unmarshal(jsonData, &msg); err != nil {
			fmt.Println("[WHEP_PROXY] Error unmarshaling signaling JSON:", err)
			continue
		}

		msgType, _ := msg["messageType"].(string)
		if msgType == "" {
			msgType, _ = msg["action"].(string)
		}
		if msgType == "" {
			fmt.Println("[WHEP_PROXY] Ignoring signaling message without type:", msg)
			continue
		}

		if correlationID, _ := msg["correlationId"].(string); correlationID != "" && correlationID != session.correlationID {
			fmt.Printf("[WHEP_PROXY] Ignoring %s for %s due to mismatched correlationId %q\n", msgType, stream.streamID, correlationID)
			continue
		}

		switch msgType {
		case "SDP_ANSWER":
			if err := handleRemoteAnswer(stream.streamID, session, msg); err != nil {
				fmt.Println("[WHEP_PROXY] Failed to handle SDP_ANSWER:", err)
			}
		case "ICE_CANDIDATE":
			if err := handleRemoteCandidate(stream.streamID, session, msg); err != nil {
				fmt.Println("[WHEP_PROXY] Failed to handle ICE_CANDIDATE:", err)
			}
		case "STATUS_RESPONSE":
			fmt.Println("[WHEP_PROXY] Received STATUS_RESPONSE for", stream.streamID, msg)
		default:
			fmt.Println("[WHEP_PROXY] Ignoring signaling message type:", msgType)
		}
	}
}
