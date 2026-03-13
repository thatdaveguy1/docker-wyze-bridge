package main

import (
	"errors"
	"io"
	"os"
	"testing"

	"github.com/gorilla/websocket"
	"github.com/pion/rtp"
)

func TestShouldForwardVideoPacketDropsPreIDRFrames(t *testing.T) {
	stream := &WebRTCStream{}

	nonIDR := &rtp.Packet{Payload: []byte{0x41, 0x00}}
	if stream.shouldForwardVideoPacket(nonIDR) {
		t.Fatal("expected pre-IDR video packet to be dropped until stream is primed")
	}

	stream.videoReady.Store(true)
	if !stream.shouldForwardVideoPacket(nonIDR) {
		t.Fatal("expected video packet to pass once stream is primed")
	}
}

func TestShouldForwardVideoPacketPrimesOnFirstIDR(t *testing.T) {
	stream := &WebRTCStream{}
	idrStart := &rtp.Packet{Payload: []byte{0x7c, 0x85, 0x00}}

	if !stream.shouldForwardVideoPacket(idrStart) {
		t.Fatal("expected first IDR packet to prime and pass through")
	}

	if !stream.videoReady.Load() {
		t.Fatal("expected first IDR to mark stream video ready")
	}
}

func TestClassifyWSReadErrorTreatsGoingAwayAsNormal(t *testing.T) {
	closeInfo := classifyWSReadError(&websocket.CloseError{Code: websocket.CloseGoingAway, Text: "Going away"})
	if !closeInfo.normal {
		t.Fatal("expected close 1001 to be classified as normal")
	}
	if closeInfo.code != websocket.CloseGoingAway {
		t.Fatalf("expected code %d, got %d", websocket.CloseGoingAway, closeInfo.code)
	}
}

func TestShouldLogTrackEndSuppressesEOF(t *testing.T) {
	if shouldLogTrackEnd(io.EOF) {
		t.Fatal("expected EOF track end to be suppressed outside debug logging")
	}
	if !shouldLogTrackEnd(errors.New("boom")) {
		t.Fatal("expected non-EOF track end to remain visible")
	}
}

func TestWHEPTraceEnabledMatchesConfiguredStream(t *testing.T) {
	t.Setenv("WHEP_TRACE_STREAM", "dog-run")
	if !whepTraceEnabled("dog-run") {
		t.Fatal("expected configured trace stream to be enabled")
	}
	if whepTraceEnabled("deck") {
		t.Fatal("expected non-configured stream to remain untraced")
	}
	if os.Getenv("WHEP_TRACE_STREAM") != "dog-run" {
		t.Fatal("expected trace env to stay available during test")
	}
}

func TestWHEPTraceDisabledWithoutConfiguredStream(t *testing.T) {
	t.Setenv("WHEP_TRACE_STREAM", "")
	if whepTraceEnabled("dog-run") {
		t.Fatal("expected tracing to stay disabled without explicit opt-in")
	}
	if whepTraceEnabled("deck") {
		t.Fatal("expected other streams to remain untraced by default")
	}
}

func TestSanitizeLogURLRedactsQueryString(t *testing.T) {
	got := sanitizeLogURL("wss://example.test/signal?token=secret&x=1")
	if got != "wss://example.test/signal" {
		t.Fatalf("expected sanitized URL, got %q", got)
	}
}
