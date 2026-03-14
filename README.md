# Docker Wyze Bridge V4.0 (thatdaveguy fork)

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/thatdaveguy1/docker-wyze-bridge?logo=github)](https://github.com/thatdaveguy1/docker-wyze-bridge/releases/latest)
[![Docker Image Size (latest semver)](https://img.shields.io/docker/image-size/thatdaveguy1/wyze-bridge?sort=semver&logo=docker&logoColor=white)](https://ghcr.io/thatdaveguy1/docker-wyze-bridge)
[![Home Assistant Add-on](https://img.shields.io/badge/home_assistant-add--on-blue.svg?logo=homeassistant&logoColor=white)](./docs/user_guide/install_ha.md)

### WebRTC/RTSP/RTMP/HLS Bridge for Wyze Cam

![Wyze Bridge UI](https://user-images.githubusercontent.com/67088095/224595527-05242f98-c4ab-4295-b9f5-07051ced1008.png)

Create a local WebRTC, RTSP, RTMP, or HLS/Low-Latency HLS stream for your Wyze cameras. This fork includes specific optimizations for the **Wyze Cam V4** and a polished **Home Assistant** integration.

- No modifications, third-party, or special firmware required.
- **It just works!**
- Streams direct from camera without additional bandwidth or subscriptions.
- Local high-performance WebRTC and RTSP backend.

---

## 🚀 Quick Start

### 1. Prerequisites
- Your Wyze account email and password.
- **API Key and API ID:** Required as of April 2024. Get them from the [Wyze Support Article](https://support.wyze.com/hc/en-us/articles/16129834216731).

### 2. Choose your Platform

| Platform | Guide |
| :--- | :--- |
| **Home Assistant (Add-on)** | [HA Install Guide](./docs/user_guide/install_ha.md) |
| **Docker (CLI / Compose)** | [Docker Install Guide](./docs/user_guide/install_docker.md) |
| **Unraid** | [Template XML](./unraid/docker-wyze-bridge.xml) |

---

## 🆕 What's New in V4.0

- **Wyze Cam V4 Support:** A new cloud-signaled KVS backend for first-class V4 support.
- **Polished Web UI:** One-click copy buttons for stream URLs and improved protocol status reporting.
- **Home Assistant Optimized:** New `docker_wyze_bridge_v4` slug, ingress-aware base URLs, and conflict-free port mapping.
- **MediaMTX V1.16.3:** Upgraded backend for lower latency and better stability.
- **Stability Fixes:** Resolved several long-running session and process-cleanup bugs.

---

## 📷 Supported Cameras

![Wyze Cam V1](https://img.shields.io/badge/wyze_v1-yes-success.svg)
![Wyze Cam V2](https://img.shields.io/badge/wyze_v2-yes-success.svg)
![Wyze Cam V3](https://img.shields.io/badge/wyze_v3-yes-success.svg)
![Wyze Cam V3 Pro](https://img.shields.io/badge/wyze_v3_pro-yes-success.svg)
![Wyze Cam V4](https://img.shields.io/badge/wyze_v4-yes-success.svg)
![Wyze Cam Floodlight](https://img.shields.io/badge/wyze_floodlight-yes-success.svg)
![Wyze Cam Floodlight V2](https://img.shields.io/badge/wyze_floodlight_v2-yes-success.svg)
![Wyze Cam Pan](https://img.shields.io/badge/wyze_pan-yes-success.svg)
![Wyze Cam Pan V2](https://img.shields.io/badge/wyze_pan_v2-yes-success.svg)
![Wyze Cam Pan V3](https://img.shields.io/badge/wyze_pan_v3-yes-success.svg)
![Wyze Cam Pan Pro](https://img.shields.io/badge/wyze_pan_pro-yes-success.svg)
![Wyze Cam Outdoor](https://img.shields.io/badge/wyze_outdoor-yes-success.svg)
![Wyze Cam Outdoor V2](https://img.shields.io/badge/wyze_outdoor_v2-yes-success.svg)
![Wyze Cam Doorbell](https://img.shields.io/badge/wyze_doorbell-yes-success.svg)
![Wyze Cam Doorbell V2](https://img.shields.io/badge/wyze_doorbell_v2-yes-success.svg)

---

## 🛠 Documentation & Support

- 🆕 [V4.0 Release Notes](./docs/user_guide/release_notes_v4.md)
- 🆙 [Upgrade & Migration Guide](./docs/user_guide/upgrade.md)
- ❓ [Troubleshooting Guide](./docs/user_guide/troubleshooting.md)
- 🏗 [Maintainer & Provenance Docs](./docs/maintainer/provenance-2026-03-11-local-patched-attribution.md)

---

## 💖 Credits & Attribution

This fork is built on the excellent work of the original authors and contributors.

- **idisposable/docker-wyze-bridge**: Base fork and release line.
- **akeslo/docker-wyze-bridge**: KVS/WebRTC signaling and architectural direction.
- **kroo/wyzecam**: Fundamental Wyze API and TUTK implementation.
- **aler9/mediamtx**: High-performance streaming backend.

Please consider starring this project if you find it useful!

> [!IMPORTANT]
> This project is not affiliated with Wyze Labs, Inc. Use at your own risk.
