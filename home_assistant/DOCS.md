# Docker Wyze Bridge

## Authentication

The standard Home Assistant setup path uses the visible `Wyze email`, `Wyze password`, `Key ID`, and `API key` fields on the add-on configuration page.

As of April 2024, Wyze developer credentials are required. See the official guide:

`https://support.wyze.com/hc/en-us/articles/16129834216731`

## Stream Surfaces

### Standard bridge ports

- RTSP: `rtsp://homeassistant.local:58554/camera-name`
- HLS: `http://homeassistant.local:58888/camera-name/stream.m3u8`
- WebRTC / WHEP: `http://homeassistant.local:58889/camera-name`
- Web UI: `http://homeassistant.local:5000`

### Native Home Assistant `go2rtc` sidecar

- Supported RTSP surface: `rtsp://homeassistant.local:19554/camera-name`
- Internal sidecar API: `http://homeassistant.local:11984/api`

Important:

- `:19554` is the supported public sidecar surface in `4.1`.
- `:11984` is an internal implementation detail and not part of the stable public interface.
- A `-sd` alias may exist for some cameras, but it is only useful when the camera exposes a meaningful second stream.

## Camera-Specific Limits

The public `4.1` release intentionally documents current limits rather than implying every modern Wyze model behaves the same.

- Wyze Cam V3: bridge main and firmware-gated bridge substream are supported; validated V3-class paths have reached `1920x1080`.
- Wyze Cam V3 Pro: bridge main validated at `2560x1440`; `4.1` does not promise a fixed substream ceiling for every installation.
- Wyze Cam V4: standard bridge RTSP may remain `640x360`; validated Home Assistant native sidecar output reached `2560x1440` main and `640x360` substream.
- Wyze Bulb Cam: supported, but current public validation keeps both main and `-sd` at `640x360`.

See [Camera Support](../docs/user_guide/camera_support.md) for the detailed matrix.

## Stream and API Authentication

When `WB_AUTH` is enabled, streams and API endpoints require authentication.

- REST API example: `http://homeassistant.local:5000/api/<camera-name>/state?api=<your-wb-api-key>`
- Stream username: `wb`
- Stream password: your unique Web UI password or API key, depending on configuration

Do not forward bridge or RTSP ports to the public internet unless you understand the security implications.

## Camera-Specific Options

Camera-specific options can be passed using `CAM_OPTIONS`.

```yaml
- CAM_NAME: Front
  AUDIO: true
  ROTATE: true
- CAM_NAME: Back door
  QUALITY: SD50
  RECORD: true
```

Available options:

- `AUDIO` enables audio for that camera.
- `FFMPEG` uses a custom ffmpeg command for that camera.
- `LIVESTREAM` sends that camera to an RTMP target.
- `NET_MODE` overrides the allowed network mode.
- `ROTATE` rotates the camera clockwise.
- `QUALITY` adjusts the requested main-stream quality.
- `SUB_QUALITY` adjusts the requested substream quality when supported.
- `FORCE_FPS` sets frames per second.
- `RECORD` enables recording for that camera.
- `SUB_RECORD` enables recording for the substream when supported.
- `SUBSTREAM` enables a substream only when the camera path supports it.
- `MOTION_WEBHOOKS` posts to a webhook when motion is detected.
