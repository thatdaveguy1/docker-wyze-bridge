package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"sync/atomic"

	"github.com/gorilla/mux"
	"github.com/pion/rtcp"
	"github.com/pion/webrtc/v3"
)

func websocketHandler(w http.ResponseWriter, r *http.Request) {
	if !isLoopbackRemoteAddr(r.RemoteAddr) {
		http.Error(w, "Forbidden", http.StatusForbidden)
		return
	}

	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	streamID := mux.Vars(r)["streamID"]

	var config WebRTCConfig
	var stream *WebRTCStream
	if err := json.NewDecoder(r.Body).Decode(&config); err != nil {
		http.Error(w, "Invalid JSON configuration", http.StatusBadRequest)
		return
	}
	if config.SignalingURL == "" {
		http.Error(w, "Signaling URL is required", http.StatusBadRequest)
		return
	}
	// If we already have an active proxy for this stream (e.g. duplicate POST from setup_streams + runOnInit),
	// do not replace it — return 200 so the first session stays alive for ICE/media.
	streamsMu.Lock()
	if existing := streams[streamID]; existing != nil {
		if !existing.canReuse() {
			fmt.Println("[WHEP_PROXY] Existing stream is stale; replacing", streamID)
			destroyStreamLocked(streamID, existing)
		} else {
			existing.setConfig(config)
			streamsMu.Unlock()
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"status":"ok","reused":true}`))
			return
		}
	}

	stream, err := newWebRTCStream(streamID, config)
	if err != nil {
		streamsMu.Unlock()
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	streams[streamID] = stream
	streamsMu.Unlock()

	// Respond 200 immediately so the client (Python) does not time out and retry. The client uses
	// a 10s timeout; delay + ICE gathering + offer can exceed that and cause a second POST, which
	// would replace this stream and close the websocket before ICE/media establish.
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"status":"ok"}`))

	startStreamUpstream(stream)
}

func statusHandler(w http.ResponseWriter, r *http.Request) {
	if !isLoopbackRemoteAddr(r.RemoteAddr) {
		http.Error(w, "Forbidden", http.StatusForbidden)
		return
	}

	streamID := mux.Vars(r)["streamID"]

	streamsMu.Lock()
	stream, ok := streams[streamID]
	streamsMu.Unlock()
	if !ok {
		http.Error(w, fmt.Sprintf("Stream %s not found", streamID), http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(stream.status()); err != nil {
		http.Error(w, "Error encoding status", http.StatusInternalServerError)
	}
}

func whepHandler(w http.ResponseWriter, r *http.Request) {
	if !isLoopbackRemoteAddr(r.RemoteAddr) {
		http.Error(w, "Forbidden", http.StatusForbidden)
		return
	}

	streamID := mux.Vars(r)["streamID"]

	streamsMu.Lock()
	stream, ok := streams[streamID]
	streamsMu.Unlock()
	if !ok {
		http.Error(w, fmt.Sprintf("Stream %s not found", streamID), http.StatusNotFound)
		return
	}

	switch r.Method {
	case http.MethodOptions, http.MethodGet:
		w.Header().Set("Content-Type", "application/sdp")
		fmt.Fprint(w, "")
	case http.MethodPost:
		if !strings.HasPrefix(r.Header.Get("Content-Type"), "application/sdp") {
			http.Error(w, "Content-Type must be application/sdp", http.StatusUnsupportedMediaType)
			return
		}

		log.Printf("[WHEP_PROXY] WHEP offer received for %s from %s", streamID, r.RemoteAddr)

		body, err := io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, "Error reading request body", http.StatusBadRequest)
			return
		}
		offerSDP := string(body)
		log.Printf(
			"[WHEP_PROXY] WHEP offer for %s: video=%t audio=%t",
			streamID,
			sdpHasMediaLine(offerSDP, "video"),
			sdpHasMediaLine(offerSDP, "audio"),
		)

		peerConnection, err := createPeerConnection(WebRTCConfig{})
		if err != nil {
			http.Error(w, "Error creating peer connection", http.StatusInternalServerError)
			return
		}

		tracks := stream.outputTracks()
		if len(tracks) == 0 {
			_ = peerConnection.Close()
			http.Error(w, "Stream has no output tracks", http.StatusServiceUnavailable)
			return
		}

		var countedClient atomic.Bool
		peerConnection.OnConnectionStateChange(func(state webrtc.PeerConnectionState) {
			if whepDebugEnabled() || state == webrtc.PeerConnectionStateFailed || state == webrtc.PeerConnectionStateDisconnected || state == webrtc.PeerConnectionStateClosed {
				log.Printf("[WHEP_PROXY] Downstream WHEP peer for %s: state=%s", streamID, state.String())
			}
			switch state {
			case webrtc.PeerConnectionStateConnected:
				if countedClient.CompareAndSwap(false, true) {
					stream.whepClients.Add(1)
					stream.videoPLIRequested.Store(true)
				}
				if err := stream.requestVideoKeyframe("downstream connected"); err != nil {
					log.Printf("[WHEP_PROXY] Failed to request keyframe for %s on connect: %v", streamID, err)
				}
			case webrtc.PeerConnectionStateDisconnected, webrtc.PeerConnectionStateFailed, webrtc.PeerConnectionStateClosed:
				if countedClient.CompareAndSwap(true, false) {
					stream.whepClients.Add(-1)
					if stream.whepClients.Load() == 0 {
						stream.videoPLIRequested.Store(false)
					}
				}
				if state == webrtc.PeerConnectionStateFailed || state == webrtc.PeerConnectionStateClosed {
					_ = peerConnection.Close()
				}
			}
		})

		if err = peerConnection.SetRemoteDescription(webrtc.SessionDescription{
			Type: webrtc.SDPTypeOffer,
			SDP:  offerSDP,
		}); err != nil {
			_ = peerConnection.Close()
			http.Error(w, "Error setting remote description", http.StatusInternalServerError)
			return
		}

		videoAdded := false
		audioAdded := false
		for _, track := range tracks {
			rtpSender, addTrackErr := peerConnection.AddTrack(track)
			if addTrackErr != nil {
				_ = peerConnection.Close()
				http.Error(w, "Error adding track", http.StatusInternalServerError)
				return
			}
			switch strings.ToLower(track.Codec().MimeType) {
			case strings.ToLower(webrtc.MimeTypeH264):
				videoAdded = true
			case strings.ToLower(webrtc.MimeTypePCMU):
				audioAdded = true
			}

			go func(sender *webrtc.RTPSender) {
				rtcpBuf := make([]byte, 1500)
				for {
					n, _, rtcpErr := sender.Read(rtcpBuf)
					if rtcpErr != nil {
						return
					}

					packets, unmarshalErr := rtcp.Unmarshal(rtcpBuf[:n])
					if unmarshalErr != nil {
						continue
					}
					for _, pkt := range packets {
						switch pkt.(type) {
						case *rtcp.PictureLossIndication, *rtcp.FullIntraRequest:
							if err := stream.requestVideoKeyframe("downstream rtcp feedback"); err != nil {
								log.Printf("[WHEP_PROXY] Failed to forward keyframe request for %s: %v", streamID, err)
							}
						}
					}
				}
			}(rtpSender)
		}
		if whepDebugEnabled() {
			log.Printf("[WHEP_PROXY] WHEP tracks added for %s: video=%v audio=%v", streamID, videoAdded, audioAdded)
		}

		gatherComplete := webrtc.GatheringCompletePromise(peerConnection)
		answer, err := peerConnection.CreateAnswer(&webrtc.AnswerOptions{})
		if err != nil {
			_ = peerConnection.Close()
			http.Error(w, "Error creating SDP answer", http.StatusInternalServerError)
			return
		}
		if err = peerConnection.SetLocalDescription(answer); err != nil {
			_ = peerConnection.Close()
			http.Error(w, "Error setting local description", http.StatusInternalServerError)
			return
		}

		etag := stream.ensureETag()
		<-gatherComplete
		localDescription := peerConnection.LocalDescription()
		if localDescription == nil {
			_ = peerConnection.Close()
			http.Error(w, "Local description unavailable", http.StatusInternalServerError)
			return
		}
		log.Printf(
			"[WHEP_PROXY] WHEP answer for %s: video=%t audio=%t",
			streamID,
			sdpHasMediaLine(localDescription.SDP, "video"),
			sdpHasMediaLine(localDescription.SDP, "audio"),
		)

		w.Header().Set("Content-Type", "application/sdp")
		w.Header().Set("Location", fmt.Sprintf("/whep/%s", streamID))
		w.Header().Set("ETag", etag)
		w.WriteHeader(http.StatusCreated)
		fmt.Fprint(w, localDescription.SDP)
	default:
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
	}
}
