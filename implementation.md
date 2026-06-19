# Architecture Refactor: Candidate #1 — WHEP Proxy God-File Split

Last updated: 2026-06-19
Status: in-progress

## Objective

Split the 2169-line `whep_proxy/main.go` into multiple files within
`package main`, creating file-level seams for the 7 conceptual modules
identified by the architecture review. This is the first step — making
the boundaries visible without changing any behavior or tests.

## Approach

Go allows multiple files in the same package. All files share the same
namespace, so splitting `main.go` into themed files creates seams without
breaking tests or requiring import changes. The tests in
`main_test.go` access `WebRTCStream` fields and methods directly —
keeping everything in `package main` means zero test changes.

The review says: "Internal seams are still seams even inside one Go
package." File-level seams make the conceptual boundaries visible,
reduce cognitive load, and set up for future deeper extraction
(interfaces, separate packages).

## File Split Plan

### `state.go` — StreamStateMachine (~280 lines)
Lifecycle, transitions, reaper, reuse logic.

**Types:** `WebRTCStream` struct (the god struct stays here for now)
**Constants:** `startupReuseWindow`, `maxNoMediaAge`, `maxRecoveryAge`,
  `maxNoVideoReconnectAttempts`, `maxVideoParamReplayFailures`,
  `streamHealthCheckInterval`, `upstreamAnswerTimeout`
**Vars:** `streams`, `streamsMu`
**Functions:**
- `canReuse()` (190-240)
- `markReconnectAttempt()` (242-248)
- `clearReconnectMetrics()` (250-252)
- `shouldForceRecreateNoVideo()` (254-262)
- `setVideoSource()` (264-276)
- `setAudioReady()` (278-280)
- `markAudioPacketSeen()` (282-284)
- `status()` (286-314)
- `resetUpstreamMediaState()` (468-512)
- `cleanupUpstreamLocked()` (984-990)
- `cleanupUpstreamIfCurrent()` (992-999)
- `destroyStreamLocked()` (1001-1005)
- `destroyStream()` (1007-1011)
- `destroyStreamIfCurrent()` (1013-1019)
- `reapStaleStreams()` (1098-1131)
- `runStreamHealthReaper()` (1133-1144)
- `scheduleReconnect()` (1146-1201)
- `handleUpstreamDisconnect()` (1203-1214)

### `upstream.go` — UpstreamSession (~530 lines)
Signaling, SDP, peer connection, websocket handling.

**Types:** `UpstreamSession` struct, `wsCloseInfo` struct
**Functions:**
- `currentUpstream()` (432-436)
- `setUpstream()` (450-455)
- `clearUpstreamIfCurrent()` (457-466)
- `closeUpstreamSession()` (514-528)
- `classifyWSReadError()` (346-356)
- `shouldLogTrackEnd()` (358-360)
- `shouldLogRTCPEnd()` (362-364)
- `logNormalClose()` (366-397)
- `shouldReconnectOnNormalWSClosure()` (399-408)
- `closeNormalRotationWebsocket()` (410-422)
- `sendSignalingMessage()` (1333-1361)
- `decodeSignalingPayload()` (1363-1369)
- `generateCorrelationID()` (1371-1380)
- `rewriteSessionLine()` (1382-1391)
- `decodeSignalingURL()` (1393-1395)
- `createPeerConnection()` (1397-1424)
- `handleRemoteAnswer()` (1426-1454)
- `markAnswerReceived()` (1456-1461)
- `watchUpstreamAnswer()` (1463-1486)
- `handleRemoteCandidate()` (1488-1521)
- `createAndSendOffer()` (1523-1567)
- `establishUpstream()` (1569-1875)

### `kvs_config.go` — KVSConfigClient (~70 lines)
Config fetch, retry, terminal error detection.

**Types:** `refreshConfigError` struct
**Functions:**
- `isTerminalRefreshError()` (424-430)
- `kvsConfigURL()` (1216-1229)
- `fetchKVSConfig()` (1231-1260)
- `recreateStreamFromBridge()` (1063-1090)
- `recreateStreamFn` var + `init()` (1092-1096)

### `media.go` — MediaForwarder (~420 lines)
RTP forwarding, keyframe handling, H.264 packet parsing.

**Functions:**
- `outputTracks()` (163-178)
- `ensureETag()` (180-188)
- `requestVideoKeyframe()` (316-344)
- `h264PacketInfo()` (530-576)
- `h264FUAState()` (578-586)
- `cloneRTPPacket()` (588-602)
- `parseSTAPAParameterSets()` (604-628)
- `bufferFragmentedSTAPA()` (630-680)
- `bufferVideoParameterSet()` (682-726)
- `replayVideoParameterSets()` (728-780)
- `shouldForwardVideoPacket()` (782-795)
- `recordVideoReplayFailure()` (797-799)
- `writeLocalTrack()` (801-840)
- `forwardTrack()` (842-961)
- `readReceiverRTCP()` (963-982)

### `handlers.go` — HTTP Handlers (~300 lines)
**Functions:**
- `websocketHandler()` (1877-1935)
- `statusHandler()` (1937-1957)
- `whepHandler()` (2000-2169)

### `main.go` — Main + Shared Types/Helpers (~200 lines)
**Types:** `ICEServer`, `WebRTCConfig`
**Functions:**
- `main()` (1282-1310)
- `newWebRTCStream()` (1021-1052)
- `startStreamUpstream()` (1054-1061)
- `whepListenAddress()` (1271-1280)
- `envListMatches()` (140-157)
- `upstreamVideoOnly()` (159-161)
- `isLoopbackRemoteAddr()` (1262-1269)
- `redactURL()` (1312-1331)
- `mustJSON()` (1959-1965)
- `whepDebugEnabled()` (1967-1970)
- `whepTraceStream()` (1972-1974)
- `whepTraceEnabled()` (1976-1979)
- `traceLogf()` (1981-1986)
- `sanitizeLogURL()` (1988-1994)
- `sdpHasMediaLine()` (1996-1998)

## Success Criteria

1. `whep_proxy/` directory has 6 Go files instead of 1 god-file.
2. `go test ./whep_proxy/... -v -count=1` passes with zero test changes.
3. `go vet ./whep_proxy/...` passes.
4. No function body changes — pure file split.
5. `./scripts/build.sh --check` passes (the build script copies
   `whep_proxy/` into `home_assistant/` and `.ha_live_addon/`).

## What This Does NOT Do (future candidates)

- Does not extract interfaces (e.g., `StreamState` interface for HTTP
  handlers to depend on instead of 30 fields)
- Does not create separate Go packages
- Does not add new tests for state transitions or keyframe buffer
- Does not change the `WebRTCStream` struct fields

These are deeper refactors that become easier once the file seams exist.
