# Release Notes V4.0.1

The **Docker Wyze Bridge V4.0.1** is a focused patch release for the V4 fork line. It keeps the streaming behavior from `4.0.0` and concentrates on clearer Home Assistant packaging and support messaging.

## Why this Fork Exists?

The original upstream bridge (`idisposable/docker-wyze-bridge` and `mrlt8/docker-wyze-bridge`) provided the foundation for local Wyze streaming. However, as Wyze introduced newer cameras like the **Wyze Cam V4**, the bridge needed a more robust and scalable backend for WebRTC and KVS (Kinesis Video Streams).

This fork aims to:
- Provide first-class support for **Wyze Cam V4** and other newer models.
- Improve the beginner experience in Home Assistant.
- Stabilize the bridge for 24/7 streaming reliability.
- Support modern WebRTC-backed RTSP and HLS delivery.

## What's New in V4.0.1

### Home Assistant login defaults
- **Visible by default:** The standard Home Assistant login path now surfaces `Wyze email`, `Wyze password`, `Key ID`, and `API key` without requiring users to show hidden optional fields first.
- **Packaging coverage:** The add-on packaging test now locks in those defaults for both the production and dev add-on manifests.

### Home Assistant wording cleanup
- **Clearer login copy:** HA-facing docs and translations now describe the standard login path consistently as email, password, Key ID, and API key.
- **Support-aware sub-stream text:** HA-facing wording now explains that sub-stream support follows the internal capability map and that Pan V2 is not currently included.

### KVS scope for this patch
- **No KVS behavior change:** This patch does not change KVS startup or playback behavior.
- **Dev-lane validation only:** The reported KVS `400` warning was not reproduced in the Home Assistant dev add-on lane during this release pass, so no KVS logging or runtime fix is included in `4.0.1`.

## What V4.0 already introduced

### Wyze Cam V4 Support
- **New KVS/WebRTC Backend:** In the current code, the KVS/WebRTC path is the default for all WebRTC-capable cameras, not just V4. That includes V3, V3 Pro, V4, Pan, Pan V2, Pan V3, Floodlight V2, Floodlight Pro, OG, and other models that are not in the `NO_WEBRTC` list.
- **Improved Authentication:** Added support for modern Wyze API and signaling protocols.

### Home Assistant Integration
- **Polished Add-on:** The Home Assistant add-on has been redesigned with a clean configuration UI and better logging.
- **Ingress Support:** The Web UI is now fully integrated into Home Assistant with automatic authentication.
- **Port Conflict Resolution:** Default ports for Home Assistant users are now `58554` (RTSP), `58888` (HLS), and `58889` (WebRTC) to avoid common conflicts with other add-ons.

### Web UI and Usability
- **One-Click Copy Buttons:** Every stream in the Web UI now has a copy icon for easy URL retrieval.
- **Smart Protocol Availability:** The bridge now correctly identifies which protocols (RTSP, RTMP, HLS, WebRTC) are available for each camera.
- **Improved Status Reporting:** Clearer logs and status indicators in the Web UI for better troubleshooting.

### Stability and Performance
- **MediaMTX V1.16.3:** Upgraded to the latest MediaMTX release for better WebRTC track gathering and lower latency.
- **Process and Thread Safety:** Fixed several child-process and threading bugs to reduce CPU and memory leaks during long-running sessions.
- **Session Lifetime Fixes:** Improved handling of Wyze session contexts to ensure streams stay open during bridge reads.

## What's Inherited?
The V4 fork line builds on the excellent work of:
- **`idisposable/docker-wyze-bridge`**: For the core bridge architecture and Home Assistant packaging.
- **`akeslo/docker-wyze-bridge`**: For the initial KVS/WebRTC signaling and architectural direction.
- **`kroo/wyzecam`**: For the fundamental Wyze API and TUTK implementation.
- **`aler9/mediamtx`**: For the high-performance streaming server.

## Current Limits and Known Issues
- **H264 PPS Warnings:** `ffprobe` may still show transient H264 PPS warnings on the first cold attach. The stream will still work after a few seconds.
- **Wi-Fi Stability:** Newer KVS-backed streams are sensitive to Wi-Fi stability. If your camera is far from your router, you may experience connection timeouts.
- **Legacy Camera Support:** Some older Wyze models (e.g., V1, some doorbells) may still use the original TUTK path.

## Attribution and Provenance
Detailed file-by-file attribution and provenance records are maintained internally. For questions about specific changes or upstream contributions, please open an issue.
