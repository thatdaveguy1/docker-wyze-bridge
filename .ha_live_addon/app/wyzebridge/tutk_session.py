import contextlib
import json
import traceback
from ctypes import c_int
from subprocess import PIPE, Popen
from threading import Thread
from time import time

from wyzecam.iotc import WyzeIOTC, WyzeIOTCSession
from wyzecam.tutk.tutk import TutkError

from wyzebridge.bridge_utils import env_bool, env_cam
from wyzebridge.config import MQTT_TOPIC
from wyzebridge.ffmpeg import get_ffmpeg_cmd
from wyzebridge.logging import logger, isDebugEnabled
from wyzebridge.mqtt import publish_messages, update_mqtt_state
from wyzebridge.webhooks import send_webhook
from wyzebridge.wyze_control import camera_control


def start_tutk_stream(uri, stream, queue, state):
    """Connect and communicate with the camera using TUTK."""
    # Reset signal handlers in child process to prevent inherited handlers from running
    import signal

    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # Late import to avoid circular dependency
    from wyzebridge.wyze_stream import NET_MODE, StreamStatus

    # Log camera details at start
    cam = stream.camera
    logger.debug(f"Starting stream for {uri}")
    logger.debug(f"Camera model: {cam.product_model} ({cam.model_name})")
    logger.debug(f"Camera MAC: {cam.mac}")
    logger.debug(f"Camera IP: {cam.ip}")
    logger.debug(
        f"Camera P2P ID: {cam.p2p_id[:20]}..."
        if cam.p2p_id
        else "Camera P2P ID: None"
    )
    logger.debug(f"DTLS: {cam.dtls}, Parent DTLS: {cam.parent_dtls}")
    logger.debug(f"ENR: {cam.enr[:20]}..." if cam.enr else "ENR: None")
    logger.debug(f"is_2k: {cam.is_2k}, is_floodlight: {cam.is_floodlight}")

    was_offline = state.value == StreamStatus.OFFLINE
    state.value = StreamStatus.CONNECTING
    exit_code = StreamStatus.STOPPING
    control_thread = audio_thread = None
    try:
        logger.debug(f"{uri}: Entering WyzeIOTC context manager...")
        with WyzeIOTC() as iotc:
            logger.debug(f"{uri}: WyzeIOTC initialized, creating session...")
            with iotc.session(stream, state) as sess:
                logger.debug(f"{uri}: Session created, state={state.value}")
                assert state.value >= StreamStatus.CONNECTING, "Stream Stopped"
                logger.debug(f"{uri}: Getting camera params...")
                v_codec, audio = get_cam_params(sess, uri)
                logger.debug(f"{uri}: v_codec={v_codec}, audio={audio}")
                control_thread = (
                    setup_control(sess, queue) if not stream.options.substream else None
                )
                audio_thread = setup_audio(sess, uri) if sess.enable_audio else None
                logger.debug(f"{uri}: Starting ffmpeg...")
                ffmpeg_cmd = get_ffmpeg_cmd(
                    uri, v_codec, audio, stream.camera.is_vertical
                )
                assert state.value >= StreamStatus.CONNECTING, "Stream Stopped"
                state.value = StreamStatus.CONNECTED
                with Popen(ffmpeg_cmd, stdin=PIPE, stderr=None) as ffmpeg:
                    if ffmpeg.stdin is not None:
                        for frame, _ in sess.recv_bridge_data():
                            ffmpeg.stdin.write(frame)

    except TutkError as ex:
        trace = traceback.format_exc() if isDebugEnabled(logger) else ""
        logger.warning(f"𓁈‼️ [TUTK] {[ex.code]} {ex} {trace}")
        set_cam_offline(uri, ex, was_offline)
        if (
            ex.code in {-10, -13, -19, -68, -90}
        ):  # IOTC_ER_UNLICENSE, IOTC_ER_TIMEOUT, IOTC_ER_CAN_NOT_FIND_DEVICE, IOTC_ER_DEVICE_REJECT_BY_WRONG_AUTH_KEY, IOTC_ER_DEVICE_OFFLINE
            exit_code = ex.code
    except ValueError as ex:
        trace = traceback.format_exc() if isDebugEnabled(logger) else ""
        logger.warning(f"𓁈⚠️ [TUTK] Error: [{type(ex).__name__}] {ex} {trace}")
        if ex.args[0] == "ENR_AUTH_FAILED":
            logger.warning("⏰ Expired ENR?")
            exit_code = -19  # IOTC_ER_CAN_NOT_FIND_DEVICE
    except BrokenPipeError:
        logger.warning("𓁈✋ [TUTK] FFMPEG stopped")
    except Exception as ex:
        trace = traceback.format_exc() if isDebugEnabled(logger) else ""
        logger.error(f"𓁈‼️ [TUTK] Exception: [{type(ex).__name__}] {ex} {trace}")
    else:
        logger.warning("𓁈🛑 [TUTK] Stream stopped")
    finally:
        state.value = exit_code

        if audio_thread is not None:
            stop_and_wait(audio_thread)
            audio_thread = None

        if control_thread is not None:
            stop_and_wait(control_thread)
            control_thread = None


def stop_and_wait(thread: Thread):
    with contextlib.suppress(ValueError, AttributeError, RuntimeError):
        if thread and thread.is_alive():
            thread.join(timeout=5)


def setup_audio(sess: WyzeIOTCSession, uri: str) -> Thread:
    audio_thread = Thread(target=sess.recv_audio_pipe, name=f"{uri}_audio")
    audio_thread.start()
    return audio_thread


def setup_control(sess: WyzeIOTCSession, queue) -> Thread:
    control_thread = Thread(
        target=camera_control,
        args=(sess, queue.cam_resp, queue.cam_cmd),
        name=f"{sess.camera.name_uri}_control",
    )
    control_thread.start()
    return control_thread


def get_cam_params(sess: WyzeIOTCSession, uri: str) -> tuple[str, dict]:
    """Check session and return fps and audio codec from camera."""
    session_info = sess.session_check()
    net_mode = check_net_mode(session_info.mode, uri)
    v_codec, fps = get_video_params(sess)
    firmware, wifi = get_camera_info(sess)
    stream = (
        f"{sess.preferred_bitrate}kb/s {sess.resolution} stream ({v_codec}/{fps}fps)"
    )

    logger.info(f"📡 Getting {stream} via {net_mode} (WiFi: {wifi}%) FW: {firmware}")

    audio = get_audio_params(sess)
    mqtt = [
        (f"{MQTT_TOPIC}/{uri.lower()}/net_mode", net_mode),
        (f"{MQTT_TOPIC}/{uri.lower()}/wifi", wifi),
        (f"{MQTT_TOPIC}/{uri.lower()}/audio", json.dumps(audio) if audio else False),
        (f"{MQTT_TOPIC}/{uri.lower()}/ip", sess.camera.ip, 0, True),
    ]
    publish_messages(mqtt)
    return v_codec, audio


def get_camera_info(sess: WyzeIOTCSession) -> tuple[str, str]:
    if not (camera_info := sess.camera.camera_info):
        logger.warning("⚠️ cameraInfo is missing.")
        return "NA", "NA"
    logger.debug(f"[cameraInfo] {camera_info}")

    firmware = camera_info.get("basicInfo", {}).get("firmware", "NA")
    if sess.camera.dtls or sess.camera.parent_dtls:
        firmware += " 🔒"

    wifi = camera_info.get("basicInfo", {}).get("wifidb", "NA")
    if "netInfo" in camera_info:
        wifi = camera_info["netInfo"].get("signal", wifi)

    return firmware, wifi


def get_video_params(sess: WyzeIOTCSession) -> tuple[str, int]:
    cam_info = sess.camera.camera_info
    if not cam_info or not (video_param := cam_info.get("videoParm")):
        logger.warning("⚠️ camera_info is missing videoParm. Using default values.")
        video_param = {"type": "h264", "fps": 20}

    fps = int(video_param.get("fps", 0))

    if force_fps := int(env_cam("FORCE_FPS", sess.camera.name_uri, "0")):
        logger.info(f"🦾 Attempting to force fps={force_fps}")
        sess.update_frame_size_rate(fps=force_fps)
        fps = force_fps

    if fps % 5 != 0:
        logger.error(f"⚠️ Unusual FPS detected: {fps}")

    logger.debug(f"📽️ [videoParm] {video_param}")
    sess.preferred_frame_rate = fps

    return video_param.get("type", "h264"), fps


def get_audio_params(sess: WyzeIOTCSession) -> dict[str, str | int]:
    if not sess.enable_audio:
        return {}

    codec, rate = sess.identify_audio_codec()
    logger.info(f"🔊 Audio Enabled [Source={codec.upper()}/{rate:,}Hz]")

    if codec_out := env_bool("AUDIO_CODEC"):
        logger.info(f"🔊 [AUDIO] Re-Encode Enabled [AUDIO_CODEC={codec_out}]")
    elif rate > 8000 or codec.lower() == "s16le":
        codec_out = "pcm_mulaw"
        logger.info(f"🔊 [AUDIO] Re-Encode for RTSP compatibility [{codec_out=}]")

    return {"codec": codec, "rate": rate, "codec_out": codec_out.lower()}


def check_net_mode(session_mode: int, uri: str) -> str:
    """Check if the connection mode is allowed."""
    from wyzebridge.wyze_stream import NET_MODE

    net_mode = env_cam("NET_MODE", uri, "any")

    if "p2p" in net_mode and session_mode == 1:
        raise RuntimeError("☁️ Connected via RELAY MODE! Reconnecting")

    if "lan" in net_mode and session_mode != 2:
        raise RuntimeError("☁️ Connected via NON-LAN MODE! Reconnecting")

    mode = f"{NET_MODE.get(session_mode, f'UNKNOWN ({session_mode})')} mode"
    if session_mode != 2:
        logger.warning(f"☁️ Camera is connected via {mode}!!")
        logger.warning("Stream may consume additional bandwidth!")
    return mode


def set_cam_offline(uri: str, error: TutkError, was_offline: bool) -> None:
    """Do something when camera goes offline."""
    state = "offline" if error.code == -90 else error.name  # IOTC_ER_DEVICE_OFFLINE
    update_mqtt_state(uri.lower(), str(state))

    if str(error.code) not in env_bool("OFFLINE_ERRNO", "-90"):
        return
    if was_offline:  # Don't resend if previous state was offline.
        return

    send_webhook("offline", uri, f"{uri} is offline")


def is_timedout(start_time: float, timeout: int = 20) -> bool:
    return time() - start_time > timeout if start_time else False
