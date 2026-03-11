package main

import (
	"testing"

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
