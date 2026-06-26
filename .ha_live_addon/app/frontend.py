import contextlib
import json
import logging
import os
import tempfile
import time
from functools import wraps
from pathlib import Path
from urllib.parse import quote_plus

from flask import (
    Flask,
    Response,
    abort,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
)
from werkzeug.exceptions import NotFound

from wyze_bridge import WyzeBridge
from wyzebridge import config, web_ui
from wyzebridge.auth import WbAuth
from wyzebridge.build_config import VERSION
from wyzebridge.camera_settings import set_camera_stream_mode
from wyzebridge.go2rtc import go2rtc_probe
from wyzebridge.native_talkback import send_native_talkback
from wyzebridge.network_utils import (
    _truthy_query_value,
    network_snapshot,
)
from wyzebridge.preview_validation import (
    preview_file_is_image,
    read_snapshot_hash_registry,
    snapshot_hash_entry,
)
from wyzebridge.web_ui import url_for

logger = logging.getLogger(__name__)


def create_app():
    app_root = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        static_folder=str(app_root / "static"),
        template_folder=str(app_root / "templates"),
    )
    app.jinja_env.globals["url_for"] = url_for
    wb = WyzeBridge()
    talkback_dir = Path(tempfile.gettempdir()) / "wyze-talkback-http"
    talkback_dir.mkdir(parents=True, exist_ok=True)
    try:
        wb.start()
    except RuntimeError as ex:
        logger.error(ex)
        logger.error("Please ensure your host is up to date.")
        exit()
    if _truthy_query_value(os.getenv("NETWORK_TRACE")):
        logger.info(f"NETWORK_TRACE {json.dumps(network_snapshot(), sort_keys=True)}")

    def camera_catalog():
        if hasattr(wb, "camera_catalog"):
            return wb.camera_catalog()
        return wb.streams.get_all_cam_info()

    def camera_info(cam_name: str):
        if hasattr(wb, "camera_info"):
            return wb.camera_info(cam_name)
        return wb.streams.get_info(cam_name)

    def catalog_loading(cameras: dict | None = None) -> bool:
        return bool(wb.api.total_cams and not (cameras if cameras is not None else camera_catalog()))

    def ready_response(cameras: dict | None = None) -> tuple[dict, int]:
        catalog = cameras if cameras is not None else camera_catalog()
        if catalog_loading(catalog):
            return {"status": "loading"}, 503

        expected_aliases = {
            camera.get("native_alias")
            for camera in catalog.values()
            if isinstance(camera, dict)
            and camera.get("native_selected")
            and camera.get("native_alias")
            and camera.get("source") == "go2rtc"
        }
        try:
            probe = go2rtc_probe(timeout=0.5, include_streams=bool(expected_aliases))
        except Exception:  # go2rtc probe can fail with HTTP/timeout/JSON errors; treat as still loading
            return {"status": "loading"}, 503
        if not probe.get("api", {}).get("reachable"):
            return {"status": "loading"}, 503

        aliases = probe.get("aliases")
        if expected_aliases:
            alias_set = set(aliases) if isinstance(aliases, list) else set()
            if not expected_aliases.issubset(alias_set):
                return {"status": "loading"}, 503

        return {"status": "ready"}, 200

    def auth_required(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if not wb.api.auth:
                return redirect(url_for("wyze_login"))
            if request.path.startswith("/kvs-config/") and request.remote_addr in {
                "127.0.0.1",
                "::1",
            }:
                return view(*args, **kwargs)
            return web_ui.auth.login_required(view)(*args, **kwargs)

        return wrapped_view

    @app.route("/login", methods=["GET", "POST"])
    def wyze_login():
        if wb.api.auth:
            return redirect(url_for("index"))
        if request.method == "GET":
            return render_template(
                "login.html",
                api=WbAuth.api,
                base_href=url_for("wyze_login").rstrip("/") + "/",
                version=VERSION,
            )

        tokens = request.form.get("tokens")
        refresh = request.form.get("refresh")

        if tokens or refresh:
            wb.api.token_auth(tokens=tokens, refresh=refresh)
            return {"status": "success"}

        credentials = {
            "email": request.form.get("email"),
            "password": request.form.get("password"),
            "key_id": request.form.get("keyId"),
            "api_key": request.form.get("apiKey"),
        }

        if all(credentials.values()):
            wb.api.creds.update(
                email=credentials["email"] or "",
                password=credentials["password"] or "",
                key_id=credentials["key_id"] or "",
                api_key=credentials["api_key"] or "",
            )
            return {"status": "success"}

        return {"status": "missing credentials"}

    @app.route("/")
    @auth_required
    def index():
        if not (columns := request.args.get("columns")):
            columns = request.cookies.get("number_of_columns", "2")

        if not (refresh := request.args.get("refresh")):
            refresh = request.cookies.get("refresh_period", "30")

        number_of_columns = int(columns) if columns.isdigit() else 0
        refresh_period = int(refresh) if refresh.isdigit() else 0
        show_video = bool(request.cookies.get("show_video"))
        autoplay = bool(request.cookies.get("autoplay"))

        if "autoplay" in request.args:
            autoplay = True

        if "video" in request.args:
            show_video = True
        elif "snapshot" in request.args:
            show_video = False

        video_format = request.cookies.get("video", "webrtc")

        if req_video := ({"webrtc", "hls", "kvs"} & set(request.args)):
            video_format = req_video.pop()

        resp = make_response(
            render_template(
                "index.html",
                cam_data=web_ui.all_cams(wb.streams, wb.api.total_cams, cameras=camera_catalog()),
                number_of_columns=number_of_columns,
                refresh_period=refresh_period,
                api=WbAuth.api,
                base_href=url_for("index").rstrip("/") + "/",
                version=VERSION,
                webrtc=bool(config.BRIDGE_IP),
                show_video=show_video,
                video_format=video_format.lower(),
                autoplay=autoplay,
            )
        )
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Pragma"] = "no-cache"

        resp.set_cookie("number_of_columns", str(number_of_columns))
        resp.set_cookie("refresh_period", str(refresh_period))
        resp.set_cookie("show_video", "1" if show_video else "")
        resp.set_cookie("video", video_format)
        fullscreen = "fullscreen" in request.args or bool(request.cookies.get("fullscreen"))
        resp.set_cookie("fullscreen", "1" if fullscreen else "")
        if order := request.args.get("order"):
            resp.set_cookie("camera_order", quote_plus(order))

        return resp

    @app.route("/health")
    def health():
        """Add-on health check."""
        health_data = wb.health()
        return Response(json.dumps(health_data), mimetype="application/json")

    @app.route("/health/details")
    def health_details():
        details = wb.health_details(request.args.get("stream"))
        if _truthy_query_value(request.args.get("network")):
            details["network"] = network_snapshot()
        return Response(json.dumps(details), mimetype="application/json")

    @app.route("/api/sse_status")
    @auth_required
    def sse_status():
        """Server sent event for camera status."""
        return Response(
            web_ui.sse_generator(wb.streams.get_sse_status),
            mimetype="text/event-stream",
        )

    @app.route("/api/status")
    @auth_required
    def api_status():
        return wb.streams.get_sse_status()

    @app.route("/kvs-config/<string:cam_name>")
    @auth_required
    def kvs_config(cam_name: str):
        if not wb.streams.get(cam_name):
            return {"error": f"camera [{cam_name}] not found"}, 404
        config = wb.api.get_kvs_proxy_config(cam_name)
        if not config:
            return {"error": f"KVS config not ready for {cam_name}"}, 503
        return config

    @app.route("/api")
    @auth_required
    def api_all_cams():
        cameras = camera_catalog()
        if catalog_loading(cameras):
            return {"status": "loading"}
        return web_ui.all_cams(wb.streams, wb.api.total_cams, cameras=cameras)

    @app.route("/api/ready")
    @auth_required
    def api_ready():
        payload, status = ready_response(camera_catalog())
        return payload, status

    @app.route("/api/snapshot-hashes")
    @auth_required
    def api_snapshot_hashes():
        return {
            "registry": read_snapshot_hash_registry(config.IMG_PATH),
            "source": config.IMG_PATH,
        }

    @app.route("/api/<string:cam_name>")
    @auth_required
    def api_cam(cam_name: str):
        if cam := camera_info(cam_name):
            return cam | web_ui.format_stream(cam_name)
        return {"error": f"Could not find camera [{cam_name}]"}

    @app.route("/api/<string:cam_name>/stream-config", methods=["GET", "PUT", "POST"])
    @app.route("/api/<string:cam_name>/stream-mode", methods=["GET", "PUT", "POST"])
    @auth_required
    def api_cam_stream_mode(cam_name: str):
        camera = wb.api.get_camera(cam_name)
        if not camera:
            return {"status": "error", "response": f"Camera [{cam_name}] not found"}, 404

        if request.method == "GET":
            config = wb.camera_stream_config(camera)
            return {"status": "success", "camera": camera.name_uri} | config

        payload = request.get_json(silent=True) or {}
        try:
            if {"hd_enabled", "sd_enabled", "hd_kbps", "sd_kbps"} & set(payload):
                config = wb.apply_camera_stream_config(camera, payload)
            elif "mode" in payload:
                mode = (
                    str(payload.get("mode") or request.values.get("mode") or request.args.get("mode") or "")
                    .strip()
                    .lower()
                )
                saved_mode = set_camera_stream_mode(camera.name_uri, mode)
                config = wb.camera_stream_config(camera)
                config["mode"] = saved_mode
            else:
                config = wb.camera_stream_config(camera)
        except ValueError as ex:
            message = str(ex) or "Invalid stream configuration"
            return {"status": "error", "response": message}, 409 if "not available" in message else 400

        wb.refresh_cams()
        return {"status": "success", "camera": camera.name_uri} | config

    @app.route("/api/<string:cam_name>/talkback", methods=["POST"])
    @auth_required
    def api_cam_talkback(cam_name: str):
        if not (stream := wb.streams.get(cam_name)):
            return {"status": "error", "response": f"Camera [{cam_name}] not found"}, 404

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return {
                "status": "error",
                "response": "Talkback requires a JSON object payload",
            }, 400

        stream_info = stream.get_info()
        if not stream_info.get("talkback_supported"):
            return {
                "status": "error",
                "response": stream_info.get("talkback_reason") or "Talkback is not available for this camera",
            }, 409

        alias = stream_info.get("talkback_alias") or stream_info.get("native_alias")
        if not alias:
            return {
                "status": "error",
                "response": "Talkback alias is unavailable for this camera",
            }, 500

        if payload.get("audio_b64") and not payload.get("audio_url"):
            suffix = str(payload.get("file_ext") or payload.get("format") or "wav").strip().lower()
            suffix = "".join(ch for ch in suffix if ch.isalnum()) or "wav"
            token = next(tempfile._get_candidate_names())
            path = talkback_dir / f"{token}.{suffix}"
            try:
                path.write_text(str(payload["audio_b64"]), encoding="ascii")
            except OSError as ex:
                return {
                    "status": "error",
                    "response": f"Unable to stage talkback upload: {ex}",
                }, 500
            payload = dict(payload)
            payload.pop("audio_b64", None)
            payload["audio_url"] = f"http://127.0.0.1:5000/api/talkback-file/{path.name}"

        result = send_native_talkback(payload, alias)
        status_code = 200 if result.get("status") == "success" else 502
        return result, status_code

    @app.route("/api/talkback-file/<string:file_name>")
    def api_talkback_file(file_name: str):
        if request.remote_addr not in {"127.0.0.1", "::1"}:
            abort(404)
        path = talkback_dir / Path(file_name).name
        if not path.is_file():
            abort(404)
        try:
            import base64

            audio_bytes = base64.b64decode(path.read_text(encoding="ascii"), validate=True)
        except (OSError, ValueError):
            abort(404)
        response = make_response(audio_bytes)
        response.headers["Content-Type"] = "audio/wav"
        return response

    @app.route("/api/<cam_name>/<cam_cmd>", methods=["GET", "PUT", "POST"])
    @app.route("/api/<cam_name>/<cam_cmd>/<path:payload>")
    @auth_required
    def api_cam_control(cam_name: str, cam_cmd: str, payload: str | dict = ""):
        """API Endpoint to send tutk commands to the camera."""
        if not payload and (args := request.values.to_dict()):
            args.pop("api", None)
            payload = next(iter(args.values())) if len(args) == 1 else args
        if not payload and request.is_json:
            json = request.get_json()
            if isinstance(json, dict):
                payload = json if len(json) > 1 else list(json.values())[0]
            else:
                payload = json
        elif not payload and request.data:
            payload = request.data.decode()

        return wb.streams.send_cmd(cam_name, cam_cmd.lower(), payload)

    @app.route("/signaling/<string:name>")
    @auth_required
    def webrtc_signaling(name):
        if "kvs" in request.args:
            return wb.api.get_kvs_signal(name)
        return web_ui.get_webrtc_signal(name, WbAuth.api)

    @app.route("/webrtc/<string:name>")
    @auth_required
    def webrtc(name):
        """View WebRTC direct from camera."""
        if (webrtc := wb.api.get_kvs_signal(name)).get("result") == "ok":
            return make_response(render_template("webrtc.html", webrtc=webrtc))
        return webrtc

    @app.route("/snapshot/<string:img_file>")
    @auth_required
    def rtsp_snapshot(img_file: str):
        """Use ffmpeg to take a snapshot from the rtsp stream."""
        if config.SNAPSHOT_TYPE == "api":
            return thumbnail(img_file)
        if wb.streams.get_snapshot(Path(img_file).stem)["ok"]:
            return send_from_directory(config.IMG_PATH, img_file)

        return thumbnail(img_file)

    @app.route("/img/<string:img_file>")
    @auth_required
    def img(img_file: str):
        """
        Serve an existing local image or take a new snapshot from the rtsp stream.

        Use the exp parameter to fetch a new snapshot if the existing one is too old.
        """
        try:
            img_path = config.IMG_PATH + img_file
            if os.path.getsize(img_path) <= 0:
                with contextlib.suppress(OSError):
                    os.remove(img_path)
                raise NotFound
            if not preview_file_is_image(img_path):
                with contextlib.suppress(OSError):
                    os.remove(img_path)
                raise NotFound
            if exp := request.args.get("exp"):
                created_at = snapshot_hash_entry(config.IMG_PATH, Path(img_file).stem).get("recorded_at", 0)
                if time.time() - created_at > int(exp):
                    raise NotFound
            return send_from_directory(config.IMG_PATH, img_file)
        except (NotFound, FileNotFoundError, ValueError):
            if config.SNAPSHOT_TYPE == "api":
                return thumbnail(img_file)
            return rtsp_snapshot(img_file)

    @app.route("/thumb/<string:img_file>")
    @auth_required
    def thumbnail(img_file: str):
        path = Path(img_file)
        stem = path.stem
        candidates = [stem]
        if stem.endswith("-sub"):
            candidates.append(stem.removesuffix("-sub"))
        for uri in candidates:
            if wb.streams.refresh_preview(uri)["ok"]:
                return send_from_directory(config.IMG_PATH, f"{uri}{path.suffix}")

        return redirect("/static/notavailable.svg", code=307)

    @app.route("/photo/<string:img_file>")
    @auth_required
    def boa_photo(img_file: str):
        """Take a photo on the camera and grab it over the boa http server."""
        uri = Path(img_file).stem
        if not (cam := wb.streams.get(uri)):
            return redirect("/static/notavailable.svg", code=307)
        if photo := web_ui.boa_snapshot(cam):
            return send_from_directory(config.IMG_PATH, f"{uri}_{photo[0]}")
        return redirect(f"/img/{img_file}", code=307)

    @app.route("/restart/<string:restart_cmd>")
    @auth_required
    def restart_bridge(restart_cmd: str):
        """
        Restart parts of the wyze-bridge.

        /restart/cameras:       Restart camera connections.
        /restart/rtsp_server:   Restart rtsp-simple-server.
        /restart/all:           Restart camera connections and rtsp-simple-server.
        """
        if restart_cmd == "cameras":
            wb.streams.stop_all()
            wb.streams.monitor_streams(wb.mtx.health_check)
        elif restart_cmd == "rtsp_server":
            wb.mtx.restart()
        elif restart_cmd == "cam_data":
            wb.refresh_cams()
            restart_cmd = "cameras"
        elif restart_cmd == "all":
            wb.restart(fresh_data=True)
            restart_cmd = "cameras,rtsp_server"
        else:
            return {"result": "error"}
        return {"result": "ok", "restart": restart_cmd.split(",")}

    @app.route("/cams.m3u8")
    @auth_required
    def iptv_playlist():
        """
        Generate an m3u8 playlist with all enabled cameras.
        """
        hostname = request.host
        cameras = web_ui.format_streams(wb.streams.get_all_cam_info())
        resp = make_response(render_template("m3u8.html", cameras=cameras, hostname=hostname))
        resp.headers.set("content-type", "application/x-mpegURL")
        return resp

    # ------------------------------------------------------------------
    # Babysitter blueprint (feature-flagged via ENABLE_BABYSITTER)
    # ------------------------------------------------------------------
    babysitter_enabled = (
        os.environ.get("ENABLE_BABYSITTER", "").lower() in ("1", "true", "yes", "on")
    )
    app.jinja_env.globals["babysitter_enabled"] = babysitter_enabled
    if babysitter_enabled:
        from babysitter.routes import create_blueprint as create_babysitter_bp

        bp = create_babysitter_bp()
        app.register_blueprint(bp)
        # Start the watchdog background thread.
        wd = bp.watchdog  # type: ignore[attr-defined]
        wd.start_background()
        logger.info("Babysitter blueprint registered and watchdog started")

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=False, host="0.0.0.0", port=5000)
