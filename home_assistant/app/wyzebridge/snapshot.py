import contextlib
import os
import time
from collections.abc import Callable
from subprocess import DEVNULL, PIPE, Popen, TimeoutExpired
from threading import Thread
from typing import TYPE_CHECKING

from wyzebridge.snapshot_schedule import should_skip_snapshot, should_take_snapshot

if TYPE_CHECKING:
    from wyzebridge.wyze_api import WyzeApi

from wyzebridge.config import IMG_PATH, IMG_TYPE, SNAPSHOT_TYPE
from wyzebridge.ffmpeg import rtsp_snap_cmd, wait_for_purges
from wyzebridge.logging import logger
from wyzebridge.mqtt import update_preview
from wyzebridge.native_alias import (
    native_alias,
    preload_native_stream,
    recovery_aliases,
    write_native_snapshot,
)
from wyzebridge.preview_validation import (
    preview_content_hash,
    preview_file_is_image,
    preview_payload_matches_existing,
    record_preview_hash,
)
from wyzebridge.snapshot_health import (
    STATE_ONLINE,
    STATE_SNAPSHOT_DOWN,
    STATE_STALE_SNAPSHOT,
    SnapshotHealthTracker,
)


def _snapshot_decode_failed(stderr_output: bytes | None) -> bool:
    if not stderr_output:
        return False
    stderr_text = stderr_output.decode("utf-8", errors="ignore").lower()
    return any(marker in stderr_text for marker in ("error while decoding", "corrupt decoded frame", "bytestream"))


def _snapshot_matches_existing(temp_path: str, final_path: str) -> bool:
    try:
        with open(temp_path, "rb") as temp_file:
            return preview_payload_matches_existing(final_path, temp_file.read())
    except OSError:
        return False


def _finalize_snapshot_output(
    cam_name: str,
    temp_path: str,
    final_path: str,
    stderr_output: bytes | None,
) -> bool:
    try:
        if os.path.getsize(temp_path) <= 0:
            return False
        if not preview_file_is_image(temp_path):
            logger.warning(f"❗ [{cam_name}] Snapshot output was not a valid image; keeping previous preview")
            return False
        if _snapshot_decode_failed(stderr_output):
            logger.warning(f"❗ [{cam_name}] Snapshot decode failed; keeping previous preview")
            return False
        with open(temp_path, "rb") as temp_file:
            payload = temp_file.read()
        if preview_payload_matches_existing(final_path, payload):
            logger.warning(f"❗ [{cam_name}] Snapshot matched existing preview; treating as stale")
            return False
        os.replace(temp_path, final_path)
        record_preview_hash(final_path, payload, camera=cam_name, source="rtsp")
        return True
    except OSError as ex:
        logger.error(f"❗ [{cam_name}] [{type(ex).__name__}] {ex}")
        return False


class SnapshotManager:
    """Owns the snapshot pipeline: go2rtc → RTSP → API fallback chain.

    Extracted from StreamManager (architecture review candidate #3) to
    separate snapshot orchestration from stream lifecycle. The fallback
    order is: selected native alias → alternate native aliases → RTSP →
    cloud thumbnail. Validation (size, decode, hash) is centralized here.
    """

    __slots__ = (
        "streams",
        "api",
        "_stop_flag",
        "_enabled_streams",
        "_active_streams",
        "rtsp_snapshots",
        "native_preloads",
        "last_snap",
        "monitor_snapshots_thread",
        "health",
    )

    def __init__(
        self,
        streams: dict,
        api: "WyzeApi",
        stop_flag: Callable[[], bool],
        enabled_streams: Callable[[], list[str]],
        active_streams: Callable[[], list[str]],
    ) -> None:
        self.streams = streams
        self.api = api
        self._stop_flag = stop_flag
        self._enabled_streams = enabled_streams
        self._active_streams = active_streams
        self.rtsp_snapshots: dict[str, Popen] = {}
        self.native_preloads: set[str] = set()
        self.last_snap: float = 0
        self.monitor_snapshots_thread: Thread | None = None
        self.health: SnapshotHealthTracker = SnapshotHealthTracker()

    # --- Snapshot monitoring ---

    def monitor_snapshots(self) -> None:
        def wrapped():
            logger.info("[STREAM] Starting monitor_snapshots thread")
            try:
                # emit to MQTT the current snapshots on file system
                for cam in self.streams:
                    if not self._stop_flag():
                        update_preview(cam)

                while not self._stop_flag():
                    for cam, ffmpeg in list(self.rtsp_snapshots.items()):
                        if (
                            not self._stop_flag()
                            and ffmpeg is not None
                            and (returncode := ffmpeg.returncode) is not None
                        ):
                            if returncode == 0:
                                stderr_output = b""
                                with contextlib.suppress(Exception):
                                    _, stderr_output = ffmpeg.communicate(timeout=0.1)
                                temp_path = getattr(ffmpeg, "_wyze_snapshot_temp_path", "")
                                final_path = getattr(ffmpeg, "_wyze_snapshot_final_path", "")
                                if (
                                    temp_path
                                    and final_path
                                    and _finalize_snapshot_output(cam, temp_path, final_path, stderr_output)
                                ):
                                    update_preview(cam)
                            # we have some response, remove from queue
                            self.remove_from_rtsp_snapshots(cam)
                    time.sleep(1)
            except (
                Exception
            ) as e:  # monitor thread must survive any subprocess/dict error to keep snapshot polling alive
                logger.error(f"[STREAM] Unexpected error in monitor_snapshots: {e}")

        if self.monitor_snapshots_thread is not None:
            logger.info("[STREAM] Stopping previous monitor_snapshots thread")
            with contextlib.suppress(ValueError, AttributeError, RuntimeError):
                self.monitor_snapshots_thread.join(timeout=5)
            self.monitor_snapshots_thread = None

        self.monitor_snapshots_thread = Thread(target=wrapped, name="monitor_snapshots")
        self.monitor_snapshots_thread.daemon = True  # allow this thread to be abandoned
        self.monitor_snapshots_thread.start()

    def stop_monitoring(self) -> None:
        if self.monitor_snapshots_thread is not None:
            logger.info("[STREAM] Stopping monitor_snapshots thread")
            with contextlib.suppress(ValueError, AttributeError, RuntimeError):
                self.monitor_snapshots_thread.join(timeout=5)
            self.monitor_snapshots_thread = None
        wait_for_purges()

    def remove_from_rtsp_snapshots(self, cam: str):
        try:
            del self.rtsp_snapshots[cam]
        except KeyError:
            logger.warning(f"[STREAM] {cam} not found in rtsp snapshots.")
        except Exception as ex:  # dict state may be replaced mid-operation; log and continue rather than crash
            logger.error(f"[STREAM] [{type(ex).__name__}] removing {cam=} {ex}.")

    # --- Snapshot taking ---

    def snap_all(self, cams: list[str] | None = None, force: bool = False):
        """
        Take an rtsp snapshot of the streams in the list.

        Args:
        - cams (list[str], optional): names of the streams to take a snapshot of.
        - force (bool, optional): Ignore interval and force snapshot. Defaults to False.
        """
        if force or should_take_snapshot(SNAPSHOT_TYPE, self.last_snap):
            self.last_snap = time.time()
            snapshot_targets = cams or (self._enabled_streams() if SNAPSHOT_TYPE == "api" else self._active_streams())
            for cam_name in snapshot_targets:
                if should_skip_snapshot(cam_name):
                    continue
                if SNAPSHOT_TYPE == "rtsp":
                    self.stop_subprocess(cam_name)
                    self.rtsp_snap_popen(cam_name, True)
                elif SNAPSHOT_TYPE == "api":
                    self.refresh_preview(cam_name)

    def rtsp_snap_popen(self, cam_name: str, interval: bool = False) -> Popen | None:
        if not (stream := self.streams.get(cam_name)):
            return
        stream.start()
        ffmpeg = self.rtsp_snapshots.get(cam_name)
        if not ffmpeg or ffmpeg.poll() is not None:
            cmd = rtsp_snap_cmd(cam_name, interval)
            final_path = cmd[-1]
            temp_path = f"{final_path}.tmp"
            with contextlib.suppress(FileNotFoundError):
                os.remove(temp_path)
            cmd[-1] = temp_path
            ffmpeg = Popen(cmd, stderr=PIPE)
            ffmpeg._wyze_snapshot_temp_path = temp_path
            ffmpeg._wyze_snapshot_final_path = final_path
            self.rtsp_snapshots[cam_name] = ffmpeg
        return ffmpeg

    def get_rtsp_snap(self, cam_name: str) -> bool:
        if not (stream := self.streams.get(cam_name)):
            return False
        stream.start()
        temp_path = f"{IMG_PATH}{cam_name}.{IMG_TYPE}.tmp"
        final_path = f"{IMG_PATH}{cam_name}.{IMG_TYPE}"
        with contextlib.suppress(FileNotFoundError):
            os.remove(temp_path)

        for skip_early_frames, snapshot_timeout in ((True, 15), (False, 30)):
            with contextlib.suppress(FileNotFoundError):
                os.remove(temp_path)
            ffmpeg = Popen(
                rtsp_snap_cmd(cam_name, skip_early_frames=skip_early_frames)[:-1] + [temp_path],
                stdout=DEVNULL,
                stderr=PIPE,
            )
            timed_out = False
            try:
                _, stderr_output = ffmpeg.communicate(timeout=snapshot_timeout)
                if ffmpeg.returncode == 0 and os.path.getsize(temp_path) > 0:
                    if _finalize_snapshot_output(cam_name, temp_path, final_path, stderr_output):
                        return True
                    return False
            except TimeoutExpired:
                timed_out = True
                suffix = " without frame skip" if not skip_early_frames else ""
                logger.info(f"❗ [{cam_name}] Snapshot timed out{suffix}")
            except (
                Exception
            ) as ex:  # ffmpeg subprocess + file IO can raise various errors; snapshot failure must not crash the stream
                logger.error(f"❗ [{cam_name}] [{type(ex).__name__}] {ex}")
            finally:
                if ffmpeg.poll() is None:
                    ffmpeg.kill()
                    ffmpeg.communicate()
                with contextlib.suppress(FileNotFoundError):
                    os.remove(temp_path)
            if not (skip_early_frames and timed_out):
                break
        return False

    def _go2rtc_snapshot(
        self,
        cam_name: str,
        require_selected: bool = False,
        skip_primary_alias: bool = False,
    ) -> bool:
        if not (stream := self.streams.get(cam_name)):
            if require_selected:
                return False
            aliases = [native_alias(cam_name)]
            alternate_alias = native_alias(cam_name, substream=True)
            if alternate_alias not in aliases:
                aliases.append(alternate_alias)
            aliases.extend(a for a in recovery_aliases(cam_name) if a not in aliases)
            return any(write_native_snapshot(alias, cam_name, warn_on_failure=False) for alias in aliases)
        info = stream.get_info()
        if require_selected and not info.get("native_selected"):
            return False
        if not info.get("native_api_reachable"):
            return False
        alias = info.get("native_alias")
        if not alias:
            return False
        aliases = [] if skip_primary_alias else [alias]
        if not require_selected:
            alternate_alias = native_alias(cam_name, substream=True)
            if (not skip_primary_alias or alternate_alias != alias) and alternate_alias not in aliases:
                aliases.append(alternate_alias)
            aliases.extend(a for a in recovery_aliases(cam_name) if a not in aliases)
        for candidate_alias in aliases:
            warn_on_failure = require_selected and candidate_alias == alias
            for attempt in range(2):
                if attempt == 0:
                    should_preload = candidate_alias not in self.native_preloads
                else:
                    should_preload = True
                    self.native_preloads.discard(candidate_alias)
                    logger.info(f"♻️ [{cam_name}] Re-preloading stale native alias {candidate_alias}")

                if should_preload:
                    preload = preload_native_stream(candidate_alias)
                    if preload.get("ok"):
                        self.native_preloads.add(candidate_alias)

                if write_native_snapshot(
                    candidate_alias,
                    cam_name,
                    warn_on_failure=warn_on_failure,
                ):
                    return True
        return False

    def get_snapshot(self, cam_name: str) -> dict:
        stream = self.streams.get(cam_name)
        stream_info = stream.get_info() if stream else {}
        selected_alias_attempted = bool(
            stream_info.get("native_selected")
            and stream_info.get("native_api_reachable")
            and stream_info.get("native_alias")
        )
        if self._go2rtc_snapshot(cam_name, require_selected=True):
            self._record_snapshot_success(cam_name)
            return {"ok": True, "source": "go2rtc"}
        if self._go2rtc_snapshot(cam_name, skip_primary_alias=selected_alias_attempted):
            self._record_snapshot_success(cam_name)
            return {"ok": True, "source": "go2rtc"}
        rtsp_ok = self.get_rtsp_snap(cam_name)
        if rtsp_ok:
            self._record_snapshot_success(cam_name)
        else:
            self._record_snapshot_failure(cam_name)
        return {"ok": rtsp_ok, "source": "rtsp"}

    def _record_snapshot_success(self, cam_name: str) -> None:
        """Record a successful snapshot and publish MQTT if state changed."""
        payload_hash = ""
        with contextlib.suppress(OSError):
            img_path = f"{IMG_PATH}{cam_name}.{IMG_TYPE}"
            with open(img_path, "rb") as handle:
                payload_hash = preview_content_hash(handle.read())
        prev_state = self.health.get(cam_name).state
        new_state = self.health.record_success(cam_name, payload_hash)
        if new_state != prev_state:
            self._publish_health(cam_name, new_state)

    def _record_snapshot_failure(self, cam_name: str) -> None:
        """Record a failed snapshot and publish MQTT if state changed."""
        prev_state = self.health.get(cam_name).state
        new_state = self.health.record_failure(cam_name)
        if new_state != prev_state:
            self._publish_health(cam_name, new_state)

    def _publish_health(self, cam_name: str, state: str) -> None:
        """Publish snapshot health state change to MQTT."""
        logger.info(f"[SNAPSHOT_HEALTH] {cam_name}: {state}")
        with contextlib.suppress(Exception):
            publish_topic(f"{cam_name}/snapshot_health", state, retain=True)

    def _restart_stream_for_snapshot(self, cam_name: str) -> bool:
        if not (stream := self.streams.get(cam_name)):
            return False
        info = stream.get_info()
        if alias := info.get("native_alias"):
            self.native_preloads.discard(alias)
        logger.warning(f"♻️ [{cam_name}] Restarting stream after stale or failed snapshot")
        with contextlib.suppress(Exception):
            stream.stop()
        return stream.start()

    def refresh_preview(self, cam_name: str) -> dict:
        snapshot = self.get_snapshot(cam_name)
        if snapshot["ok"]:
            # Snapshot succeeded — check if the frame is stale (frozen).
            # If the tracker says we should restart for freshness, do so.
            if self.health.should_restart(cam_name):
                logger.info(f"♻️ [{cam_name}] Restarting stream for stale snapshot")
                if self._restart_stream_for_snapshot(cam_name):
                    self.health.mark_restarted(cam_name)
                    snapshot = self.get_snapshot(cam_name)
                    if snapshot["ok"]:
                        return snapshot | {"restarted": True}
            return snapshot
        # Snapshot failed — check the health tracker for restart decision.
        # On sustained failure (snapshot_down), the tracker enforces a cooldown
        # to prevent restart loops. On first failures, we still try one restart.
        if self.health.should_restart(cam_name):
            if self._restart_stream_for_snapshot(cam_name):
                self.health.mark_restarted(cam_name)
                snapshot = self.get_snapshot(cam_name)
                if snapshot["ok"]:
                    return snapshot | {"restarted": True}
        elif self._restart_stream_for_snapshot(cam_name):
            snapshot = self.get_snapshot(cam_name)
            if snapshot["ok"]:
                return snapshot | {"restarted": True}
        return {"ok": self.api.save_thumbnail(cam_name, ""), "source": "api"}

    def snapshot_health(self) -> dict[str, dict]:
        """Return per-camera snapshot health for the /api/snapshot-health endpoint."""
        return self.health.all_health()

    def stop_subprocess(self, cam: str):
        ffmpeg = self.rtsp_snapshots.get(cam)

        if ffmpeg is not None:
            self.remove_from_rtsp_snapshots(cam)

            if ffmpeg.poll() is None:
                ffmpeg.kill()
                ffmpeg.communicate()
