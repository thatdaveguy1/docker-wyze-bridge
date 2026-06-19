package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
)

// refreshConfigError is returned by fetchKVSConfig when the bridge responds
// with a non-200 status.  isTerminalRefreshError distinguishes 404 (camera
// removed) from retryable failures (503, timeout, etc.).
type refreshConfigError struct {
	statusCode int
	body       string
}

func (e *refreshConfigError) Error() string {
	return fmt.Sprintf("refresh config status %d: %s", e.statusCode, strings.TrimSpace(e.body))
}

func isTerminalRefreshError(err error) bool {
	var refreshErr *refreshConfigError
	if !errors.As(err, &refreshErr) {
		return false
	}
	return refreshErr.statusCode == http.StatusNotFound
}

func kvsConfigURL(streamID string) string {
	host := os.Getenv("KVS_CONFIG_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := os.Getenv("KVS_CONFIG_PORT")
	if port == "" {
		port = os.Getenv("WB_APP_PORT")
	}
	if port == "" {
		port = "5000"
	}
	return fmt.Sprintf("http://%s:%s/kvs-config/%s", host, port, streamID)
}

func fetchKVSConfig(streamID string) (WebRTCConfig, error) {
	var config WebRTCConfig
	if whepDebugEnabled() {
		log.Printf("[WHEP_PROXY] Fetching KVS config from %s", kvsConfigURL(streamID))
	}

	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get(kvsConfigURL(streamID))
	if err != nil {
		return config, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		return config, &refreshConfigError{
			statusCode: resp.StatusCode,
			body:       string(body),
		}
	}

	if err := json.NewDecoder(resp.Body).Decode(&config); err != nil {
		return config, err
	}
	if config.SignalingURL == "" {
		return config, fmt.Errorf("refresh config missing signaling_url")
	}

	return config, nil
}

func recreateStreamFromBridge(streamID string, current *WebRTCStream, reason string) error {
	config, err := fetchKVSConfig(streamID)
	if err != nil {
		if isTerminalRefreshError(err) {
			log.Printf("[WHEP_PROXY] Destroying unrecoverable stream %s during health recreate: %v", streamID, err)
			destroyStreamIfCurrent(streamID, current)
		}
		return err
	}

	replacement, err := newWebRTCStream(streamID, config)
	if err != nil {
		return err
	}

	streamsMu.Lock()
	if existing := streams[streamID]; existing != current {
		streamsMu.Unlock()
		return nil
	}
	destroyStreamLocked(streamID, current)
	streams[streamID] = replacement
	streamsMu.Unlock()

	log.Printf("[WHEP_PROXY] Recreated upstream stream for %s after health check: %s", streamID, reason)
	startStreamUpstream(replacement)
	return nil
}

var recreateStreamFn func(string, *WebRTCStream, string) error

func init() {
	recreateStreamFn = recreateStreamFromBridge
}
