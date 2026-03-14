# Live Deployment Handoff

## Current live state

- Active HA add-on: `Docker Wyze Bridge (Local Patched)`
- HA slug: `local_docker_wyze_bridge_local`
- Status at last verification: `started`
- Stock add-on kept installed but inactive: `d44b2bbd_docker_wyze_bridge`
- Live RTSP base: `rtsp://homeassistant.local:58554`
- Live WebRTC base: `http://homeassistant.local:58889`

## SSL details

- Main live access is currently **not** using custom public TLS for the promoted endpoints:
  - RTSP is plain TCP on `:58554`
  - WebRTC/WHEP endpoint is plain HTTP on `:58889`
- The add-on **does** support SSL-backed LL-HLS if `LLHLS=true`:
  - `app/wyzebridge/mtx_server.py` checks `/ssl/privkey.pem` and `/ssl/fullchain.pem`
  - if those HA cert files exist, MediaMTX uses them for HLS encryption
  - otherwise it generates a local self-signed HLS cert under the token path
- Practical takeaway:
  - current production switchover is for RTSP/WebRTC on the promoted ports above
  - do not assume HTTPS/TLS on `58889` unless a separate reverse proxy is added

## Recent work

- Fixed previously validated bridge reliability issues already captured in `docs/maintainer/proposedPRs.md` and confirmed they are present in the live add-on copy:
  - child-process cleanup/assertion fix
  - session lifetime fix
  - connect watchdog / timeout sizing fix
- Built and validated a V4 bypass path using Wyze signaling + WHEP proxy instead of TUTK.
- Promoted the V4 path into the live patched add-on behind `ENABLE_V4_KVS_TRIAL=true`.
- Benchmarked V3 and V3 Pro (`garage`, `hamster`) and found RTC-backed delivery significantly better than the old TUTK path in this environment.
- Extended RTC backend routing to all WebRTC-capable cameras with `ENABLE_ALL_RTC_TRIAL=true`.
- Added live proxy refresh support with `/kvs-config/<name>` and normal Wyze WebRTC -> proxy config mapping.
- Fixed proxy signaling to include `recipientClientId` for non-KVS WebRTC signaling.
- Resolved HA/live deployment port conflicts by moving the promoted live service to a clean port block:
  - RTSP `58554`
  - HLS `58888`
  - WebRTC `58889`
  - MediaMTX API `59997`
  - internal Flask control path `5001`
- Added a resilient `/kvs-config/<name>` endpoint + configurable `KVS_CONFIG_HOST/PORT` helper in `whep_proxy` so every camera now refreshes its KVS metadata from the same port the Flask server really binds to (5000), and the HA copy buttons now fall back to a manual prompt when clipboard APIs are blocked.
- Removed temporary HA lab add-ons and moved local experiment artifacts to Trash.
- Verified repeated backend-only RTC soak results:
  - `garage`: `10/10` successful reconnect probes, median about `2.994s`
  - `hamster`: `10/10` successful reconnect probes, median about `2.995s`
  - broader 5-camera spot check also passed on the RTC-backed RTSP path

## Known quirks

- `ffprobe` can still print a transient H264 PPS warning on a cold attach.
- Even when that warning appears, the stream still returns valid `h264` video and `pcm_mulaw` audio.
- Repeated attaches are cleaner than the first cold attach after source startup.
- On-demand RTC logs can show disconnect / failed states after readers leave; that is expected for idle teardown.

## Validation snapshot

- Last stream verification command:

```bash
ffprobe -rtsp_transport tcp -v error -show_entries stream=codec_name,codec_type -of json \
  'rtsp://homeassistant.local:58554/garage'
```

- Last verified result:
  - `h264` video present
  - `pcm_mulaw` audio present

## Todo list

- [completed] Create a git commit for the promoted `local patched` deployment state.
- [completed] Audit repo-tracked downstream consumers for old ports and update the live add-on docs to `58554` / `58889` / `58888`.
- [completed] Leave Frigate untouched because it consumes a rebroadcast stream from Scrypted and needs separate manual follow-up.
- [pending] Decide whether to keep `ENABLE_ALL_RTC_TRIAL=true` as the long-term default or rename it to a non-trial setting.
- [pending] Decide whether the cold-attach H264 PPS warning is worth deeper work; current behavior is usable.
- [pending] Optional: capture packet-level evidence later if tcpdump-capable tooling is available on the HA host.
- [pending] Optional: run a non-HA Linux control path for comparison if the local bridged VM tooling is restored.

## Files most relevant to the live deployment

- `.ha_live_addon/config.yml`
- `.ha_live_addon/options_payload.json`
- `.ha_live_addon/app/wyzebridge/wyze_api.py`
- `.ha_live_addon/app/wyzebridge/wyze_stream.py`
- `.ha_live_addon/app/wyzebridge/mtx_server.py`
- `.ha_live_addon/app/frontend.py`
- `.ha_live_addon/app/run`
- `.ha_live_addon/whep_proxy/main.go`
- `docs/maintainer/proposedPRs.md`
- `tasks/todo.md`
