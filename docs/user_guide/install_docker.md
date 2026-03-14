# Docker Install Guide

This guide covers setting up the **Docker Wyze Bridge V4.0** using Docker or Docker Compose.

## Prerequisites

- A Wyze account.
- **API Key and API ID:** Required as of April 2024. Get them from the [Wyze Support Article](https://support.wyze.com/hc/en-us/articles/16129834216731).
- [Docker](https://docs.docker.com/get-docker/) installed on your system.

## Quick Start (Docker Run)

Run the following command to start the bridge with the web UI enabled and authentication disabled:

```bash
docker run -d \
  --name wyze-bridge \
  --restart unless-stopped \
  -p 8554:8554 \
  -p 8888:8888 \
  -p 8889:8889 \
  -p 5000:5000 \
  -e WYZE_EMAIL='your-email@example.com' \
  -e WYZE_PASSWORD='your-password' \
  -e API_ID='your-api-id' \
  -e API_KEY='your-api-key' \
  -e WB_AUTH=false \
  ghcr.io/thatdaveguy1/docker-wyze-bridge:latest
```

Wait a few seconds, then open the web interface at `http://localhost:5000`.

## Docker Compose (Recommended)

Docker Compose is the easiest way to manage your configuration. Create a `docker-compose.yml` file:

```yaml
services:
  wyze-bridge:
    container_name: wyze-bridge
    image: ghcr.io/thatdaveguy1/docker-wyze-bridge:latest
    restart: unless-stopped
    network_mode: host # Recommended for WebRTC and performance
    environment:
      - WYZE_EMAIL=your-email@example.com
      - WYZE_PASSWORD=your-password
      - API_ID=your-api-id
      - API_KEY=your-api-key
      # [OPTIONAL] Set to false to disable Web UI and stream auth.
      - WB_AUTH=True
      - WB_USERNAME=admin
      - WB_PASSWORD=password
    volumes:
      - ./media:/app/media
      - ./config:/app/config
```

Run `docker compose up -d` to start the bridge.

## Common Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `WYZE_EMAIL` | Your Wyze account email. | Required |
| `WYZE_PASSWORD` | Your Wyze account password. | Required |
| `API_ID` | Your Wyze API ID. | Required |
| `API_KEY` | Your Wyze API Key. | Required |
| `WB_AUTH` | Enable Web UI and stream authentication. | `True` |
| `ON_DEMAND` | Only start streams when a reader is connected. | `True` |
| `ENABLE_AUDIO` | Enable audio for all cameras. | `True` |
| `NET_MODE` | Connection mode: `LAN`, `P2P`, or `ANY`. | `ANY` |

For more advanced options, check the sample compose files in the root of the repository and the add-on config examples in this repo. A fuller advanced configuration guide can be added later without changing the basic install flow here.

## Ports

If you are not using `network_mode: host`, you may need to map these ports:

- `5000`: Web UI (HTTP)
- `8554`: RTSP Stream
- `8888`: HLS Stream
- `8889`: WebRTC / WHEP Stream
- `1935`: RTMP Stream
