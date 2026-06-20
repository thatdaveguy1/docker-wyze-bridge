"""Connect/auth state machine for WyzeIOTCSession.

Extracted from iotc.py to reduce deep nesting. Each function takes a
``session`` parameter (a WyzeIOTCSession instance) in place of ``self``.
The function bodies are identical to the original methods — only ``self``
has been renamed to ``session``.

Late imports of ``WyzeIOTCSessionState`` and ``_log_tutk_trace`` from
``wyzecam.iotc`` are used inside functions to avoid a circular import
(iotc.py imports this module at load time). The logger is obtained via
``logging.getLogger("wyzecam.iotc")`` so test patches on
``wyzecam.iotc.logger`` are visible here.
"""

import base64
import contextlib
import hashlib
import logging
import os
import threading
import time
import warnings
from ctypes import c_int, c_ubyte, c_uint, c_uint32
from typing import Callable, Optional

from wyzecam.iotc_helpers import (
    hl_cam4_connect_watchdog_secs,
    hl_cam4_main_probe_mode,
    redact_password,
)
from wyzecam.tutk import tutk
from wyzecam.tutk.tutk_protocol import K10000ConnectRequest, respond_to_ioctrl_10001

# Use the same logger as iotc.py so test patches on wyzecam.iotc.logger
# are visible to these functions.
logger = logging.getLogger("wyzecam.iotc")

# Backwards-compatible aliases (match iotc.py aliases)
_hl_cam4_main_probe_mode = hl_cam4_main_probe_mode
_hl_cam4_connect_watchdog_secs = hl_cam4_connect_watchdog_secs


def _connect_watchdog_timeout(session) -> Optional[float]:
    if session.camera.product_model != "HL_CAM4":
        return None

    if not (session.substream or _hl_cam4_main_probe_mode() in {"tutk_dtls", "tutk_parallel"}):
        return None

    return _hl_cam4_connect_watchdog_secs()


def _arm_connect_watchdog(
    session, connect_mode: str
) -> tuple[Optional[threading.Timer], threading.Event]:
    from wyzecam.iotc import _log_tutk_trace

    connect_done = threading.Event()
    session._connect_watchdog_fired = False
    timeout_s = session._connect_watchdog_timeout()
    if timeout_s is None or session.session_id is None:
        return None, connect_done

    _log_tutk_trace(
        session.camera,
        "connect_watchdog_armed",
        connect_mode=connect_mode,
        substream=session.substream,
        watchdog_timeout_s=round(timeout_s, 3),
    )

    def stop_connect() -> None:
        if connect_done.is_set() or session.session_id is None:
            return

        print(
            f"[DEBUG-IOTC] Connect watchdog firing after {timeout_s:.3f}s for {session.camera.nickname}",
            flush=True,
        )
        session._connect_watchdog_fired = True
        _log_tutk_trace(
            session.camera,
            "connect_watchdog_timeout",
            connect_mode=connect_mode,
            substream=session.substream,
            watchdog_timeout_s=round(timeout_s, 3),
        )
        err_no = tutk.iotc_connect_stop_by_session_id(
            session.tutk_platform_lib, session.session_id
        )
        _log_tutk_trace(
            session.camera,
            "connect_watchdog_stop",
            connect_mode=connect_mode,
            errno=int(err_no),
            substream=session.substream,
        )
        print(
            f"[DEBUG-IOTC] Connect watchdog stop returned {int(err_no)} for {session.camera.nickname}",
            flush=True,
        )

    watchdog = threading.Timer(timeout_s, stop_connect)
    watchdog.daemon = True
    watchdog.start()
    return watchdog, connect_done


def _release_connect_watchdog(
    session,
    watchdog: Optional[threading.Timer],
    connect_done: threading.Event,
) -> None:
    connect_done.set()
    if not watchdog:
        return

    watchdog.cancel()
    with contextlib.suppress(RuntimeError):
        watchdog.join(timeout=0.1)


def _run_connect_with_watchdog(
    session, connect_mode: str, connect_call: Callable[[], int]
) -> int:
    watchdog, connect_done = session._arm_connect_watchdog(connect_mode)
    try:
        return connect_call()
    finally:
        session._release_connect_watchdog(watchdog, connect_done)


def _retryable_connect_error(session, ex: tutk.TutkError) -> bool:
    if ex.code in {-13, -23}:
        return True
    return ex.code == -27 and session._connect_watchdog_fired


def _connect(
    session,
    timeout_secs: c_uint32 = c_uint32(20),
    channel_id: c_ubyte = c_ubyte(0),
    username: str = "admin",
    password: str = "888888",
    max_buf_size: c_uint = c_uint(10 * 1024 * 1024),
):
    max_retries = max(int(os.getenv("CONNECT_RETRIES", 3)), 1)
    retry_delay = max(float(os.getenv("CONNECT_RETRY_DELAY", 2.0)), 0.0)
    last_error = None

    for attempt in range(max_retries):
        try:
            session._connect_attempt(
                timeout_secs,
                channel_id,
                username,
                password,
                max_buf_size,
                attempt_no=attempt + 1,
                max_retries=max_retries,
            )
            return
        except tutk.TutkError as ex:
            last_error = ex
            if not session._retryable_connect_error(ex) or attempt == max_retries - 1:
                raise

            logger.warning(
                f"[IOTC] Connection failed for {session.camera.nickname} with {ex.code}; retrying {attempt + 2}/{max_retries} in {retry_delay:.1f}s"
            )
            session._disconnect()
            time.sleep(retry_delay)

    if last_error:
        raise last_error


def _connect_attempt(
    session,
    timeout_secs: c_uint32 = c_uint32(20),
    channel_id: c_ubyte = c_ubyte(0),
    username: str = "admin",
    password: str = "888888",
    max_buf_size: c_uint = c_uint(10 * 1024 * 1024),
    attempt_no: int = 1,
    max_retries: int = 1,
):
    from wyzecam.iotc import WyzeIOTCSessionState, _log_tutk_trace

    try:
        session.state = WyzeIOTCSessionState.IOTC_CONNECTING
        print(
            f"[DEBUG-IOTC] _connect() starting for {session.camera.nickname} ({session.camera.product_model})",
            flush=True,
        )
        print(
            f"[DEBUG-IOTC] P2P ID present: {bool(session.camera.p2p_id)}", flush=True
        )
        assert session.camera.p2p_id, "Missing p2p_id"

        print("[DEBUG-IOTC] Getting session ID...", flush=True)
        session_id = tutk.iotc_get_session_id(session.tutk_platform_lib)
        if int(session_id) < 0:
            print(f"[DEBUG-IOTC] get_session_id FAILED: {session_id}", flush=True)
            raise tutk.TutkError(session_id)
        session.session_id = session_id
        print(f"[DEBUG-IOTC] Got session ID: {session_id}", flush=True)

        force_v4_parallel_raw = os.getenv("FORCE_V4_PARALLEL", "")
        probe_mode = _hl_cam4_main_probe_mode()
        force_parallel_substream = session.substream and session.camera.product_model in {
            "HL_CAM3P",
            "HL_CAM4",
        }
        force_v4_parallel = (
            session.camera.product_model == "HL_CAM4"
            and (
                probe_mode == "tutk_parallel"
                or force_v4_parallel_raw.lower() in {"1", "true", "yes"}
            )
        )
        print(
            f"[DEBUG-IOTC] FORCE_V4_PARALLEL raw='{force_v4_parallel_raw}' active={force_v4_parallel or force_parallel_substream}",
            flush=True,
        )
        _log_tutk_trace(
            session.camera,
            "connect_start",
            attempt_no=attempt_no,
            av_chan_id=None,
            dtls=session.camera.dtls,
            force_v4_parallel=force_v4_parallel or force_parallel_substream,
            max_retries=max_retries,
            main_probe_mode=probe_mode,
            parent_dtls=session.camera.parent_dtls,
            session_id=int(session.session_id),
            substream=session.substream,
        )

        if force_parallel_substream or force_v4_parallel or (
            not session.camera.dtls and not session.camera.parent_dtls
        ):
            connect_mode = "parallel"
            print(
                "[DEBUG-IOTC] Using IOTC_Connect_ByUID_Parallel"
                + (
                    " (forced substream)"
                    if force_parallel_substream
                    else " (forced HL_CAM4)"
                    if force_v4_parallel
                    else " (no DTLS)"
                ),
                flush=True,
            )
            connect_started = time.monotonic()
            session_id = session._run_connect_with_watchdog(
                connect_mode,
                lambda: tutk.iotc_connect_by_uid_parallel(
                    session.tutk_platform_lib, session.camera.p2p_id, session.session_id
                ),
            )
            print(
                f"[DEBUG-IOTC] iotc_connect_by_uid_parallel elapsed={time.monotonic() - connect_started:.3f}s",
                flush=True,
            )
        else:
            connect_mode = "dtls_ex"
            print(
                f"[DEBUG-IOTC] Using IOTC_Connect_ByUIDEx (DTLS={session.camera.dtls})",
                flush=True,
            )
            password = (
                str(session.camera.parent_enr)
                if session.camera.parent_dtls
                else str(session.camera.enr)
            )
            print("[DEBUG-IOTC] Calling iotc_connect_by_uid_ex...", flush=True)
            connect_started = time.monotonic()
            session_id = session._run_connect_with_watchdog(
                connect_mode,
                lambda: tutk.iotc_connect_by_uid_ex(
                    session.tutk_platform_lib,
                    session.camera.p2p_id,
                    session.session_id,
                    session.get_auth_key(),
                    session.connect_timeout,
                ),
            )
            print(
                f"[DEBUG-IOTC] iotc_connect_by_uid_ex elapsed={time.monotonic() - connect_started:.3f}s",
                flush=True,
            )
        connect_elapsed = round(time.monotonic() - connect_started, 3)
        _log_tutk_trace(
            session.camera,
            "connect_result",
            attempt_no=attempt_no,
            connect_mode=connect_mode,
            elapsed_s=connect_elapsed,
            max_retries=max_retries,
            session_id=int(session_id),
            substream=session.substream,
            watchdog_fired=session._connect_watchdog_fired,
        )

        print(f"[DEBUG-IOTC] Connect returned: {session_id}", flush=True)
        if int(session_id) < 0:
            print(
                f"[DEBUG-IOTC] Session connection FAILED: {int(session_id)}",
                flush=True,
            )
            raise tutk.TutkError(session_id)
        session.session_id = session_id
        print(f"[DEBUG-IOTC] Session connected OK: {session_id}", flush=True)

        print("[DEBUG-IOTC] Calling session_check...", flush=True)
        session_info = session.session_check()
        print(
            f"[DEBUG-IOTC] Session mode: {session_info.mode} (0=P2P, 1=Relay, 2=LAN)",
            flush=True,
        )
        _log_tutk_trace(
            session.camera,
            "session_check",
            session_mode=int(session_info.mode),
            substream=session.substream,
        )
        resend = (
            c_int(1)
            if session.camera.product_model not in ("WVOD1", "HL_WCO2")
            and int(os.getenv("RESEND", 1)) != 0
            else c_int(0)
        )

        session.state = WyzeIOTCSessionState.AV_CONNECTING
        logger.debug(
            f"[IOTC] Calling av_client_start {session_id=} {username=} password: {redact_password(password)} {timeout_secs=} {channel_id=} {resend=}"
        )
        av_chan_id = tutk.av_client_start(
            session.tutk_platform_lib,
            session.session_id,
            username.encode("ascii"),
            password.encode("ascii"),
            timeout_secs,
            channel_id,
            resend,
        )
        logger.debug(f"[IOTC] av_client_start returned {av_chan_id=}")
        _log_tutk_trace(
            session.camera,
            "av_client_start",
            av_chan_id=int(av_chan_id),
            substream=session.substream,
        )

        if int(av_chan_id) < 0:
            logger.error(
                f"[DEBUG] AV client start failed with error code: {int(av_chan_id)}"
            )
            raise tutk.TutkError(av_chan_id)
        session.av_chan_id = av_chan_id
        session.state = WyzeIOTCSessionState.CONNECTED
        logger.info(f"[DEBUG] AV Client connected successfully: {av_chan_id}")
    except tutk.TutkError as e:
        _log_tutk_trace(
            session.camera,
            "connect_error",
            code=e.code,
            error=str(e),
            substream=session.substream,
        )
        logger.error(f"[DEBUG] TutkError in _connect: code={e.code}, message={e}")
        session._disconnect()
        raise
    finally:
        if session.state != WyzeIOTCSessionState.CONNECTED:
            session.state = WyzeIOTCSessionState.CONNECTING_FAILED

    logger.info(
        f"[IOTC] AV Client Start: {session.av_chan_id=} expected_chan={channel_id}"
    )

    session.tutk_platform_lib.avClientSetMaxBufSize(max_buf_size)
    tutk.av_client_set_recv_buf_size(
        session.tutk_platform_lib, session.av_chan_id or c_int(0), max_buf_size
    )


def get_auth_key(session) -> str:
    """Generate authkey using enr and mac address."""
    auth = (
        str(session.camera.parent_enr) + str(session.camera.parent_mac).upper()
        if session.camera.parent_dtls
        else str(session.camera.enr) + session.camera.mac.upper()
    )
    hashed_enr = hashlib.sha256(auth.encode("utf-8")).digest()
    auth_key = (
        base64.b64encode(hashed_enr[:6])
        .decode()
        .replace("+", "Z")
        .replace("/", "9")
        .replace("=", "A")
        # .encode() # https://github.com/kroo/wyzecam/compare/main...mrlt8:wyzecam:dev#diff-ed2b3d2defa5e765636d4536ebf34452e05bfec37377d62c71a9e58789e093dfR667
    )
    return auth_key


def _auth(session):
    from wyzecam.iotc import WyzeIOTCSessionState, _log_tutk_trace

    if session.state == WyzeIOTCSessionState.CONNECTING_FAILED:
        logger.error("[DEBUG] _auth() called but state is CONNECTING_FAILED")
        return

    assert session.state == WyzeIOTCSessionState.CONNECTED, (
        f"Auth expected state to be connected but not authed; state={session.state.name}"
    )

    session.state = WyzeIOTCSessionState.AUTHENTICATING
    logger.info(f"[DEBUG] _auth() starting for {session.camera.nickname}")
    _log_tutk_trace(session.camera, "auth_start", substream=session.substream)
    try:
        with session.iotctrl_mux() as mux:
            wake_mac = None
            if session.camera.product_model in {"WVOD1", "HL_WCO2"}:
                wake_mac = session.camera.mac
                logger.info(
                    f"[DEBUG] Using wake_mac for outdoor camera: {wake_mac}"
                )

            logger.info("[DEBUG] Sending K10000ConnectRequest...")
            challenge = mux.send_ioctl(K10000ConnectRequest(wake_mac))
            result = challenge.result()

            if not result:
                logger.error(f"[DEBUG] K10000ConnectRequest failed: {challenge}")
                warnings.warn(f"[IOTC] CONNECT FAILED: {challenge}")
                raise ValueError("CONNECT_REQUEST_FAILED")

            logger.info(f"[IOTC] {challenge.resp_protocol=}")
            logger.info(
                f"[DEBUG] Challenge result received, protocol: {challenge.resp_protocol}"
            )
            _log_tutk_trace(
                session.camera,
                "auth_challenge",
                resp_protocol=challenge.resp_protocol,
                substream=session.substream,
            )

            challenge_response = respond_to_ioctrl_10001(
                result,
                challenge.resp_protocol or 0,
                str(session.camera.enr) + str(session.camera.parent_enr),
                session.camera.product_model,
                session.camera.mac,
                session.account.phone_id,
                session.account.open_user_id,
                session.enable_audio,
            )

            if not challenge_response:
                logger.error("[DEBUG] challenge_response is None - AUTH_FAILED")
                raise ValueError("AUTH_FAILED")

            logger.info("[DEBUG] Sending challenge response...")
            auth_response = mux.send_ioctl(challenge_response).result()

            if not auth_response:
                logger.error("[DEBUG] auth_response is None - AUTH_RESPONSE_NONE")
                raise ValueError("AUTH_RESPONSE_NONE")

            logger.info(
                f"[DEBUG] Auth response received: connectionRes={auth_response.get('connectionRes')}"
            )
            _log_tutk_trace(
                session.camera,
                "auth_response",
                connection_res=auth_response.get("connectionRes"),
                substream=session.substream,
            )

            if auth_response["connectionRes"] == "2":
                logger.error("[DEBUG] connectionRes=2 - ENR_AUTH_FAILED")
                raise ValueError("ENR_AUTH_FAILED")

            if auth_response["connectionRes"] != "1":
                logger.error(
                    f"[DEBUG] connectionRes={auth_response.get('connectionRes')} - AUTH_FAILED"
                )
                warnings.warn(f"[IOTC] AUTH FAILED: {auth_response=}")
                raise ValueError("AUTH_FAILED")

            logger.info("[DEBUG] Authentication successful, setting camera info...")
            session.camera.set_camera_info(auth_response["cameraInfo"])

            mux.send_ioctl(session.set_resolving_bit()).result()
            session.state = WyzeIOTCSessionState.AUTHENTICATION_SUCCEEDED
            _log_tutk_trace(
                session.camera,
                "auth_success",
                bitrate=session.preferred_bitrate,
                frame_size=session.preferred_frame_size,
                substream=session.substream,
            )
            logger.info("[DEBUG] Authentication completed successfully")
    except tutk.TutkError as e:
        _log_tutk_trace(
            session.camera,
            "auth_error",
            code=e.code,
            error=str(e),
            substream=session.substream,
        )
        logger.error(f"[DEBUG] TutkError in _auth: code={e.code}, message={e}")
        session._disconnect()
        raise
    except ValueError as e:
        _log_tutk_trace(
            session.camera,
            "auth_error",
            error=str(e),
            substream=session.substream,
        )
        logger.error(f"[DEBUG] ValueError in _auth: {e}")
        raise
    finally:
        if session.state != WyzeIOTCSessionState.AUTHENTICATION_SUCCEEDED:
            session.state = WyzeIOTCSessionState.AUTHENTICATION_FAILED
            logger.error(
                f"[DEBUG] Authentication failed, state set to: {session.state.name}"
            )
    return session
