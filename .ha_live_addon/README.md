# Docker Wyze Bridge V4.0 (thatdaveguy fork)

This Home Assistant add-on provides a local WebRTC, RTSP, RTMP, or HLS/Low-Latency HLS stream for your Wyze cameras. This fork includes specific optimizations for the **Wyze Cam V4** and a polished Home Assistant integration.

## 🚀 Installation

1.  Add the repository to Home Assistant: `https://github.com/thatdaveguy1/docker-wyze-bridge`
2.  Install the **Docker Wyze Bridge (V4.0)** add-on.
3.  Fill in your **Wyze Email**, **Wyze Password**, **API ID**, and **API Key**.
4.  **API Key and API ID:** Required as of April 2024. Get them from the [Wyze Support Article](https://support.wyze.com/hc/en-us/articles/16129834216731).
5.  Click **Start**.

## 🆕 What's New in V4.0

- **WebRTC-Capable Cameras:** The KVS/WebRTC path is now the default for WebRTC-capable cameras, including V3, V3 Pro, and V4.
- **Polished Web UI:** One-click copy buttons for stream URLs and improved protocol status reporting.
- **Home Assistant Optimized:** New `docker_wyze_bridge_v4` slug, ingress-aware base URLs, and conflict-free port mapping.
- **MediaMTX V1.16.3:** Upgraded backend for lower latency and better stability.
- **Stability Fixes:** Resolved several long-running session and process-cleanup bugs.

---

## 🛠 Documentation & Support

- 📖 [User Guide](https://github.com/thatdaveguy1/docker-wyze-bridge/blob/main/README.md)
- ❓ [Troubleshooting](https://github.com/thatdaveguy1/docker-wyze-bridge/blob/main/docs/user_guide/troubleshooting.md)
- 🆙 [Upgrade Guide](https://github.com/thatdaveguy1/docker-wyze-bridge/blob/main/docs/user_guide/upgrade.md)

---

## 💖 Credits & Attribution

This fork is built on the excellent work of the original authors and contributors:
- `idisposable/docker-wyze-bridge`
- `akeslo/docker-wyze-bridge`
- `kroo/wyzecam`
- `aler9/mediamtx`

> [!IMPORTANT]
> This project is not affiliated with Wyze Labs, Inc. Use at your own risk.
