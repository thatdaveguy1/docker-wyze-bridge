# Lessons

[2026-06-19] - Lazy imports inside __init__ cause circular re-imports in test stubs
Oops: `SnapshotManager.__init__` had `from wyzebridge.stream_manager import StreamManager` for a type hint. When tests stubbed `wyzebridge.wyze_events` and `wyzebridge.wyze_stream` in `sys.modules`, then imported `stream_manager`, the `SnapshotManager(self)` call inside `StreamManager.__init__` re-triggered the full `stream_manager` module load from file, bypassing the stubs and hitting `wyzecam.tutk.tutk.TutkError` which can't load outside Docker.
Why: Python re-imports a module from file if it's not yet in `sys.modules`. The lazy import inside `__init__` ran before `stream_manager` was fully loaded, but the import system still tried to resolve the full chain.
Fix: Remove the lazy import. Use a string type annotation `"StreamManager"` instead — Python doesn't evaluate string annotations at runtime.
Next time: Never put `from mymodule import MyClass` inside `__init__` for type hints. Use `from __future__ import annotations` or string annotations. If you need the class at runtime, pass it as a parameter instead.

[2026-06-19] - Delegate methods must call self.method() not self._delegate.method() for test patches to work
Oops: After extracting `SnapshotManager` from `StreamManager`, `send_cmd` called `self._snapshots.refresh_preview(cam_name)` directly. But tests patch `StreamManager.refresh_preview` with `@patch.object(StreamManager, "refresh_preview", ...)`. The patch didn't intercept the call because it went straight to the delegate, bypassing the wrapper.
Why: Python `@patch.object` replaces the attribute on the class. If code calls `self._snapshots.method()`, it goes to `SnapshotManager.method`, not `StreamManager.method`. The wrapper method on `StreamManager` is never invoked.
Fix: `send_cmd` must call `self.refresh_preview(cam_name)` (the wrapper) instead of `self._snapshots.refresh_preview(cam_name)` (the delegate target). This way test patches on `StreamManager.refresh_preview` still intercept the call.
Next time: When extracting a class and leaving wrapper methods on the original for backward compatibility, internal callers in the original class must use `self.wrapper_method()`, not `self._extracted.method()`. Otherwise test patches on the wrapper are bypassed.

[2026-06-19] - Go file splits in the same package need import auditing per file
Oops: After splitting `whep_proxy/main.go` into 6 files, `go vet` caught missing imports — `state.go` used `strings.Contains` and `rtp.Packet` without importing `strings` or `github.com/pion/rtp`; `upstream.go` used `hex.EncodeToString`, `os.Getenv`, and `atomic.Bool` without importing `encoding/hex`, `os`, or `sync/atomic`; `main.go` used `http.ListenAndServe` and `log.Printf` without importing `net/http` or `log`.
Why: Go imports are per-file, not per-package. When moving functions between files in the same package, each file must import everything it directly references. The original god-file had all imports at the top; splitting them means each new file needs its own import block.
Fix: After creating each new file, audit which packages its functions reference and add the imports. `go vet` catches missing imports immediately — run it right after the split, before running tests.
Next time: When splitting a Go god-file, write the import block for each new file by scanning every function body for package references. Run `go vet` after each file is created, not at the end.

[2026-06-19] - Python script imports from a sibling module need sys.path injection when loaded by importlib in tests
Oops: After extracting shared functions into `scripts/ffmpeg_helpers.py`, the test files that load scripts via `importlib.util.spec_from_file_location` failed with `ModuleNotFoundError: No module named 'ffmpeg_helpers'`.
Why: `importlib.util` executes the script from a file path but doesn't add the script's directory to `sys.path`. The `import ffmpeg_helpers` statement could not find the sibling module.
Fix: Add `sys.path.insert(0, str(Path(__file__).resolve().parent))` before the import in each script. This is safe for both direct execution (`python3 scripts/foo.py`) and test-time loading via `importlib.util`.
Next time: When extracting a shared Python module for scripts that live in the same directory, add the `sys.path` injection at the top of each consuming script. Don't rely on the test runner's path — `importlib.util` bypasses it.

[2026-06-19] - Shell library injection over SSH requires piping the library into the heredoc, not sourcing it remotely
Oops: The first instinct was to source `ha_bridge_probe.sh` inside the remote `sh -s` heredoc, but the library file only exists locally — the remote HA host has no copy.
Why: `ha_ssh.sh` uses `exec ssh ... "$@"`, so stdin flows through to the remote shell. The remote `sh -s` only sees what we pipe to it. A `source` call on the remote side would fail because the file doesn't exist there.
Fix: Use the pipe pattern: `{ cat "$SCRIPT_DIR/ha_bridge_probe.sh"; cat <<'REMOTE' ... REMOTE; } | "$SCRIPT_DIR/ha_ssh.sh" "ENV=vars sh -s"`. The library functions are defined inline before the script body, so `section`, `mark_fail`, `redact_api_keys`, and `derive_bridge_token` are all available remotely. For local-only validators (`validate_slug`, `validate_base_url`), source the library locally with `. "$SCRIPT_DIR/ha_bridge_probe.sh"` before the SSH call.
Next time: When extracting a shared shell library that runs partly local (validation) and partly remote (probe execution), split the sourcing: `. library.sh` locally for validators, `cat library.sh |` into the SSH pipe for remote functions.

[2026-06-19] - Tests that assert inline patterns in script text must be updated to check the library when patterns move
Oops: After extracting `s/api=[^" ]+/api=<redacted>/g` and `xxd -r -p` into `ha_bridge_probe.sh`, the existing tests that asserted these patterns directly in each script's text started failing.
Why: The tests were designed to catch missing redaction/token logic by checking the script source. When the logic moved to a shared library, the scripts no longer contained those patterns inline.
Fix: Update each test to check the library for the moved pattern and assert the script references `ha_bridge_probe.sh`. For scripts with custom redact wrappers (Bearer tokens, rtsp:// credentials), keep the script-level assertion for the custom part while checking the library for the common `api=` pattern.
Next time: When planning a shell library extraction, audit every test that asserts on inline script patterns before moving them. Update the tests in the same commit as the extraction to avoid a red window.

[2026-06-08] - Local mixed-camera smoke tests should prefer direct proof surfaces over stale alias guesses
Oops: The tempting quick route was to reuse the older local Wyze smoke helpers exactly as-is, including a hardcoded bridge credential and fragile alias guesses like `garage_sd` and `south_yard_sd`.
Why: That shape is fast to sketch but brittle in repo reality. It leaks a secret into tracked code, confuses hyphen and underscore alias naming, and makes a local helper harder to trust or share safely.
Fix: Keep the local runner narrow and env-driven. The canonical entry point is now `scripts/local_camera_smoke_test.py`, which reuses the existing Reolink direct RTSP probe and pairs it with `scripts/wyze_cam_smoke_test.py` for bridge `/api/<camera>` plus `go2rtc` `frame.jpeg` checks. The bridge key now comes only from CLI/env, not a repo constant, and every run writes heartbeat-backed artifacts under `tmp/`.
Next time: If a local helper starts with a pasted credential or guessed alias spelling, stop there. Move the secret to env/CLI first, then build the smallest observable wrapper around the already-proven probe surfaces.

[2026-06-04] - A separate standalone go2rtc add-on can silently steal Frigate's WebRTC port on the HA host
Oops: The first instinct could have been to rewrite Frigate camera config again because the add-on would not start.
Why: The real June 4, 2026 blocker was lower and simpler. Frigate was failing before its camera graph even mattered because Docker could not bind host TCP `8555`. The separate standalone `go2rtc` add-on was using the same port through its default WebRTC listener because `/homeassistant/go2rtc.yaml` had `api` and `rtsp` listeners set but no explicit `webrtc.listen`.
Fix: Prove the bind failure first from `ha host logs`, then separate the ports instead of editing Frigate. Back up `/homeassistant/go2rtc.yaml`, set standalone `go2rtc` `webrtc.listen: ":18555"`, restart standalone `go2rtc`, and only then start Frigate. Validate with add-on stats, `ha resolution info`, `/api/stats`, a real soak, and fresh recording files.
Next time: When Frigate will not start on this HA box, check host-port collisions before touching `/addon_configs/ccab4aaf_frigate/config.yaml`. If the error is `failed to bind host port ... 8555`, fix the shared-host listener conflict first and treat any later camera-input churn as a second lane.

[2026-06-02] - Lightweight go2rtc stream detail probes can hide the same wedge that the full stream table shows
Oops: After proving North Yard's giant receiver-child buildup from the full `go2rtc /api/streams` dump, it would have been easy to assume the new bridge diagnostic fields would show those same counts automatically.
Why: The bridge's lightweight alias-status probe uses the narrower per-alias detail fetch first, and on this box that path can report `producer_count=1` while still surfacing `receiver_children=0` and `keyframe_consumers=0` even though the full stream table shows huge child lists for the same alias. So the diagnostic surface can be directionally useful without yet mirroring the fullest truth.
Fix: Keep the fast-fail behavior in `write_native_snapshot()` and expose the new fields, but treat the full `go2rtc /api/streams` dump as the stronger proof source when child/consumer pileup is the question. If the bridge-facing counters need to become authoritative, teach the status helper to fall back to the full stream table when the alias-scoped response looks too thin.
Next time: When a new diagnostic surface disagrees with a deeper raw probe, keep both pieces of evidence and say which one is stronger instead of assuming the new field is the source of truth.

[2026-06-02] - The smallest North Yard fix lives in snapshot fallback, not in public feed policy
Oops: After the HD-vs-SD live probe succeeded, it would have been easy to push that result into the broad published feed-selection logic and risk reopening the old alias bug in disguise.
Why: The proof was narrow. It only showed that the exact `north-yard` `HL_CAM4` camera could return real JPEGs through a `subtype=hd` recovery lane even though the normal selected SD snapshot path had been the one wedging.
Fix: Keep the public feed policy stable and land the rescue where still-image retries already live. The repo now seeds hidden alias `north-yard-v4-hd-recovery` from `north-yard-sd` with `subtype=hd`, and the snapshot manager tries it only after the selected `north-yard-sd` path fails its normal retry.
Next time: When one camera and one workflow prove a rescue lane, patch the narrowest layer that matches the evidence instead of broadening policy for every camera.

[2026-06-02] - North Yard can be Wi-Fi reachable while go2rtc keyframe extraction is wedged
Oops: After the user confirmed North Yard was healthy on Wi-Fi, it would have been easy to keep treating the blocker as physical camera health or to move back to Scrypted settings.
Why: HA could ping `192.168.1.179` with 0% loss and go2rtc showed a `wyze/dtls` producer for `north-yard-sd` with H264 bytes/packets, but `frame.jpeg?src=north-yard-sd` still timed out with 0 bytes. The go2rtc stream table showed keyframe/JPEG consumers piling up on the H264 receiver (`899` before one frame attempt, `906` after), so producer metadata and LAN reachability were not enough.
Fix: Treat this state as a native keyframe/JPEG extraction wedge. Compare keyframe consumer cleanup and receiver packet movement against a healthy SD alias, and fix readiness/reporting or native capture behavior before touching Scrypted routes.
Next time: Once Wi-Fi and HA ping are green, do not go back to physical troubleshooting unless reachability changes. The proof gate is a real non-empty `frame.jpeg?src=north-yard-sd` JPEG, not ping, go2rtc producer presence, or Scrypted HTTP 200.

[2026-06-02] - North Yard Scrypted HTTP 200 can still be the timeout placeholder image
Oops: After the direct Wyze-cloud reboot, Scrypted `211` improved from HTTP `500` to HTTP `200`, and it would have been easy to treat that as “good enough for now.”
Why: On this box, North Yard can return a valid JPEG response that is still just the Snapshot plugin fallback image. The current manual pull of `tmp/north_yard_scrypted_current.jpg` was only `9898` bytes and visibly said `Snapshot Timed Out`, even though the endpoint returned HTTP `200`.
Fix: When North Yard moves from HTTP `500` to HTTP `200`, inspect the actual image, not just the status code or hash. If the image is the timeout placeholder, the lane is still blocked on the live media path.
Next time: For North Yard, treat “HTTP 200 with a real yard image” and “HTTP 200 with `Snapshot Timed Out`” as completely different states. The latter is still a live blocker and should be closed honestly as such.

[2026-06-02] - A direct Wyze-cloud reboot can recover North Yard from HTTP 500 to HTTP 200 without fixing the real native freshness path
Oops: Once the direct camera reboot succeeded against the exact North Yard MAC, it would have been easy to treat the move from Scrypted HTTP `500` to HTTP `200` as a real recovery.
Why: On this box, North Yard can partially improve after a true cloud-side reboot and still stay broken where it matters. The reboot reached the correct live device `80482C31C9E7` at confirmed IP `192.168.1.179`, and Scrypted `211` did stop returning `500`, but `frame.jpeg?src=north-yard-sd` still returned `0` bytes, bridge `/snapshot/north-yard.jpg` still timed out, and Scrypted kept serving the same repeated `41468`-byte JPEG hash across samples.
Fix: Treat “HTTP 200 came back” as partial recovery only. Keep the direct camera reboot in the playbook, but re-run the native frame probe, bridge `/snapshot`, bridge `/img?exp=0`, and Scrypted snapshot freshness probe before calling North Yard fixed.
Next time: If North Yard improves from `500` to `200` after a reboot, immediately check whether the image hash actually changes or the visible timestamp advances. Do not confuse a non-error response with a fresh live image.

[2026-06-02] - North Yard can relapse into a dead live path even while the SD alias mapping still looks correct
Oops: Once `/api/north-yard` kept reporting `native_alias=north-yard-sd` and `go2rtc_wyze.yaml` still contained only `north-yard-sd`, it would have been easy to blame Scrypted again or assume a quick restart would clear the issue.
Why: This box can relapse into a different North Yard failure shape. The old fake-main-alias bug can stay fixed while the real still/live media path dies underneath it: `frame.jpeg?src=north-yard` still returns `404` as expected, but `frame.jpeg?src=north-yard-sd` returns `0` bytes or times out, bridge `/snapshot/north-yard.jpg` times out, bridge `/img/north-yard.jpg` stays stuck on one stale `42599`-byte JPEG hash, and Scrypted `211` keeps returning HTTP `500` with `Snapshot Timed Out`. In the same window, Scrypted logs still show the correct RTSP URL `rtsp://192.168.1.244:19554/north-yard-sd`, so the relapse is lower than Scrypted routing.
Fix: Treat this pattern as a live North Yard camera/media-path outage. `update_snapshot`, a production bridge restart, and a Scrypted restart did not clear it on June 2, so the next lane has to verify the actual camera/LAN health again instead of redoing Scrypted-only settings.
Next time: If North Yard relapses and the alias proof is still clean, stop after proving the real still/live failure with `frame.jpeg`, `/snapshot`, `/img`, and `/endpoint/@scrypted/snapshot/211/Camera`. Do not burn time re-fixing the old alias bug or Back Yard-style snapshot URL settings unless the evidence changes.

[2026-06-02] - Back Yard can stay stale even when `snapshotsFromPrebuffer` is already disabled
Oops: The approved next step assumed Back Yard was still stale because Snapshot `Default` was falling back to prebuffer, but the live device was already set to `snapshot:snapshotsFromPrebuffer=Disabled`.
Why: The real stale source was different. Device `265` still had `snapshot:snapshotUrl` pinned to `http://172.30.32.1:8123/local/wyze/back-yard-known-good.jpg`, which served the same frozen `44692`-byte JPEG that the Scrypted snapshot probe kept returning.
Fix: Inspect the actual live Scrypted settings with `getSettings()` before applying the assumed fix. Leave `snapshotsFromPrebuffer=Disabled`, and instead repoint `snapshot:snapshotUrl` to a proven fresh image source. On this pass, `http://172.30.32.1:5000/img/back-yard.jpg?exp=0&api=<derived_wb_api>` restored changing Scrypted snapshot hashes immediately and stayed green for the full 2-minute soak.
Next time: For Back Yard snapshot bugs on this box, inspect `urls`, `snapshot:snapshotUrl`, and `snapshot:snapshotsFromPrebuffer` together. Do not assume the prebuffer toggle is the remaining problem if the live still image is actually coming from a stale hardwired snapshot URL.

[2026-06-02] - Scrypted RTSP Camera plugin credentials can silently break a corrected Wyze RTSP URL
Oops: I first treated the Back Yard fix as just a route swap from the dead bridge substream path to the healthy native SD path.
Why: The live `Back Yard` device is using Scrypted's generic RTSP Camera plugin, which keeps separate `Username` and `Password` settings. After I changed the URL to `rtsp://192.168.1.244:19554/back-yard-sd`, Scrypted still tried `rtsp://admin:Cessna172@192.168.1.244:19554/back-yard-sd` and failed with `404 Not Found`.
Fix: Clear the RTSP Camera plugin credential fields when pointing a Wyze camera at a local go2rtc route that does not expect RTSP auth. After that cleanup and a Scrypted-only restart, Scrypted stopped targeting the old `back-yard-sub` path and stopped corrupting the new one with stale credentials.
Next time: When repointing a Scrypted RTSP Camera plugin device, always inspect all three fields together: `Username`, `Password`, and `RTSP Stream URL`. If the logs show an unexpected `user:pass@host` prefix, fix the plugin fields before blaming the camera or the bridge.

[2026-06-02] - Back Yard freshness should be judged by the user-facing age budget, not by quick hash churn
Oops: My first post-fix Scrypted probe still used the stricter rule that a healthy snapshot should change hash quickly across repeated short samples.
Why: The user clarified that Back Yard Wi‑Fi is flaky and that the real pass condition is more forgiving: if the Scrypted snapshot stays less than about 2 minutes old consistently, that is good enough even if it does not change every few seconds.
Fix: Add a longer Back Yard freshness soak against the exact Scrypted device-page image URL or snapshot endpoint and judge it against the 2-minute age budget. On this pass, the same 44,692-byte JPEG hash held for about 121 seconds, which still fails the updated rule but is a better measurement than a 20-second hash check.
Next time: For Back Yard on this box, run a 2-minute freshness soak before calling the Scrypted layer green or red. Short identical hashes are only a clue; the real gate is whether the user-facing image can stay within the allowed age window.

[2026-06-02] - North Yard can still fail after the obvious Scrypted `No Audio` fix
Oops: I went into the live pass expecting North Yard to recover once `No Audio` was set correctly in Scrypted.
Why: By the time of the June 2 pass, the easy settings were already mostly right: HA `CAM_OPTIONS` already had `AUDIO=false`, Scrypted device `211` was already on `rtsp://192.168.1.244:19554/north-yard-sd`, top-level `No Audio` was already on, and `FFmpeg (TCP)` was already selected. The only drift I found was the stream-level `STREAMS -> MANAGE -> No Audio` checkbox. Enabling it and restarting Scrypted still did not recover the path because the real failure was lower-level: go2rtc `frame.jpeg` returned no bytes, bridge `update_snapshot` returned `value=false`, and Scrypted prebuffer logs showed `timeout waiting for data` plus `Snapshot Timed Out`.
Fix: Treat June 2 North Yard as a live still-frame/prebuffer failure, not as an unset-audio checkbox bug. Keep the corrected stream-level `No Audio` setting, but do not call the camera fixed until the native still-frame path itself comes back with fresh bytes.
Next time: For North Yard, verify the whole chain in this order before changing code: HA `CAM_OPTIONS`, Scrypted RTSP URL, top-level `No Audio`, stream-level `No Audio`, parser, `frame.jpeg?src=north-yard-sd`, bridge `update_snapshot`, and finally Scrypted `/endpoint/@scrypted/snapshot/211/Camera`. If the first five are right and the last three still fail, stop blaming config drift and treat it as a live media-path blocker.

[2026-06-02] - A live go2rtc producer does not mean North Yard snapshots are healthy
Oops: Once I saw `go2rtc /api/streams` reporting live Wyze producers for `north-yard` and `north-yard-sd`, it would have been easy to declare the native path healthy and focus only on Scrypted.
Why: On this box, North Yard can sit in a split-brain state: `go2rtc` still shows active Wyze DTLS producers on `192.168.1.179`, but `frame.jpeg?src=north-yard` and `frame.jpeg?src=north-yard-sd` still hang for 12 seconds and return `0` bytes, bridge `/snapshot/north-yard.jpg` still hangs, and the muted rebroadcast URL still fails `ffprobe`.
Fix: Treat producer metadata as weaker evidence than actual frame extraction. Keep using `frame.jpeg` bytes, bridge `/snapshot`, and Scrypted `/endpoint/@scrypted/snapshot/211/Camera` as the pass/fail gates.
Next time: If `api/streams` says North Yard is live but still-frame fetches fail, record that contradiction explicitly and do not promote the issue to a Scrypted-only problem until a fresh JPEG actually comes back.

[2026-05-31] - Back Yard HomeKit snapshot identity can be masked by stale Scrypted/Bridge fallbacks
Oops: I initially treated Scrypted device `265` settings and hash divergence as enough proof that Back Yard was fixed, but the HomeKit-facing endpoint still served the South Driveway/RV scene.
Why: Scrypted can keep returning cached or fallback snapshot bytes even when `snapshot:snapshotUrl` has been repointed, and the live Bridge Back Yard image routes can time out when the Wyze camera is cloud-offline. In that state WHEP stays `video_ready=false`, RTSP returns `400`, and Wyze `power=restart` reports `code='3019' msg='device is offline'`.
Fix: Verify the actual `/endpoint/@scrypted/snapshot/265/Camera` image visually every time. For the live outage, point Scrypted snapshots at a reachable HA static known-good Back Yard JPEG (`/config/www/wyze/back-yard-known-good.jpg`) with `snapshotsFromPrebuffer=Disabled`, but treat live video as blocked until the physical/Wyze cloud camera path recovers.
Next time: Do not call Back Yard fixed from settings alone. Prove the Scrypted endpoint image is the green-yard scene, then separately prove live with WHEP `video_ready=true` or RTSP frame bytes; if Wyze reports the device offline, stop changing Scrypted and recover the camera/power path first.

[2026-05-29] - Scrypted snapshot proof must follow the HA integration login flow
Oops: I first tried to prove Scrypted snapshots through the Supervisor add-on proxy path and with the Supervisor bearer token, which only produced `404` or `403` noise and made the snapshot helper look broken.
Why: The live snapshot endpoint is served by Scrypted itself and expects a Scrypted bearer token minted from `https://<host>/login`, not the Supervisor token. The HA host also does not have `python3`, so shell helpers cannot depend on Python for local parsing.
Fix: Read the active `scrypted` config entry from `/homeassistant/.storage/core.config_entries`, mint the Scrypted bearer token via `/login`, and call `/endpoint/@scrypted/snapshot/<id>/Camera` directly with that token. Keep the helper pure shell plus `jq`/`curl`, and redact bearer material from output.
Next time: For HA-host Scrypted proof, mirror the custom Home Assistant `scrypted` integration auth flow instead of guessing at Supervisor proxy routes or assuming Python exists on-box.

[2026-05-29] - Force-refresh bridge previews before treating `/img` as a freshness gate
Oops: I initially treated repeated hashes from plain `/img/south-yard.jpg` as proof that South Yard was still stale, even after the live route patch made `south-yard-sd` native frames change again.
Why: The bridge can legally serve an older cached preview file from `/img/<camera>.jpg` until a caller forces a refresh. On this pass, `south-yard` stayed byte-identical on the cached `/img` route while `/img/south-yard.jpg?exp=0` and Scrypted snapshots both became fresh again.
Fix: Use `/img/<camera>.jpg?exp=0` when a probe is trying to prove live preview freshness, and reserve plain `/img/<camera>.jpg` for cached-file availability checks.
Next time: If `/img` looks stale but native `go2rtc` frames are moving, force-refresh the preview path before blaming the camera or the route policy.

[2026-05-29] - A failed North Yard LAN-pin test should be rolled back immediately
Oops: After South Yard was fixed, I still had one plausible North Yard rescue left from older repo notes: force the North Yard MAC override back to `192.168.1.175`.
Why: Older live evidence on this same HA box had shown `.175` producing changing SD frames while newer helper output preferred `.179`, so the `.175` pin was worth one controlled experiment. It still produced zero-byte `go2rtc` frames and Scrypted device `211` kept failing.
Fix: Treat that as a failed live rescue, restore the add-on back to the current helper/override state (`.179` here), and keep the failure evidence. The useful result was not a fix; it was proving that North Yard is currently blocked outside the remaining repo-side changes.
Next time: If a single-camera LAN override experiment does not improve live frames or Scrypted snapshots, roll it back the same turn and stop guessing. At that point the blocker is external state until the camera/network path itself changes.

[2026-05-30] - go2rtc producer presence is weaker than frame proof
Oops: North Yard kept reporting a go2rtc producer and `native_alias_ready=true`, which made the metadata look healthier than the actual frame path.
Why: go2rtc can retain a producer entry for a Wyze alias even when `frame.jpeg` returns empty bytes or times out. Preload and an add-on restart did not change that for North Yard, while South Yard produced valid changing frames after warm-up.
Fix: Treat `frame.jpeg` bytes plus JPEG magic/hash as the live proof gate. A producer entry is only routing metadata, not camera health.
Next time: When native metadata and snapshots disagree, trust repeated frame bytes and Scrypted endpoint proof over producer counts before changing more bridge code.

[2026-05-28] - Long Reolink soaks need graceful interrupt handling at the top level
Oops: The `deco-3-2308` 60-minute run was interrupted about 9 minutes in and left CSV/log artifacts without `summary.txt` or `summary.json`.
Why: The probe only wrote summaries after all workers returned, and the process had no `SIGINT`/`SIGTERM` path to tell workers to stop cleanly and then flush a partial artifact.
Fix: Add a shared stop event, trap `SIGINT`/`SIGTERM` in `main()`, let each camera worker terminate ffmpeg cleanly, then write a partial summary with interruption metadata.
Next time: Any long-running soak that may be stopped externally should treat termination as a first-class exit path and still write partial artifacts.

[2026-05-28] - End-of-run aggregation needs a fallback when one camera worker crashes
Oops: A severe `asus-4-noscrypted` run wrote per-camera logs and CSVs but no `summary.txt` or `summary.json`.
Why: `main()` assumed every `probe_camera()` future would return a complete dict, so one uncaught worker exception aborted the whole run before summary generation.
Fix: Catch per-camera future exceptions in `main()`, synthesize a failed camera summary with zero coverage and the fatal error text, then keep writing the overall artifact.
Next time: Any multi-camera or multi-worker soak should preserve partial results at the aggregation boundary instead of letting one worker failure erase the whole run.

[2026-05-28] - Initial RTSP metadata probes need a real subprocess timeout
Oops: The named `Asus` soak hung after North and Doorbell had already finished because South got stuck in the initial `ffprobe` metadata step before its worker ever reached the normal restart/watchdog loop.
Why: Passing an RTSP timeout flag to `ffprobe` was not enough to guarantee that the subprocess itself would exit on every failure mode, so one unreachable camera could block the whole summary write.
Fix: Wrap the metadata `ffprobe` call in a hard `subprocess.run(..., timeout=...)` deadline and treat timeout as a normal metadata failure instead of letting the full soak hang.
Next time: For any preflight probe that runs outside the main restart/watchdog loop, add an explicit subprocess timeout even if the underlying CLI also has its own timeout flags.

[2026-05-28] - Quiet long-running probes need heartbeat output and graceful end handling
Oops: The first full Reolink soak attempts were hard to supervise from VS Code because the probe stayed quiet until the end, then some wrapper paths backgrounded or killed it before the final summary write. The old loop also sent `SIGTERM` to ffmpeg right at the requested end time, which made otherwise successful short runs look like exit `255` failures.
Why: A quiet 10-minute task looks idle to some terminal wrappers, and forcing ffmpeg down at the exact timer boundary races the process's own clean `-t` exit.
Fix: Added periodic per-camera status output, kept the run noisy enough to babysit, and only force-stop ffmpeg after a short end-of-run grace period.
Next time: If a long media probe is meant to be supervised interactively, emit small periodic status lines and let the worker exit cleanly before reaching for `SIGTERM`.

[2026-05-28] - Reolink direct RTSP probe timeout handling
Oops: I first treated three direct-camera `ffprobe` failures as a URL or reachability problem.
Why: On this macOS/Homebrew FFmpeg build, RTSP timeout support is exposed as `-rw_timeout`, not `-stimeout`, so the probe was failing locally before it even touched the cameras.
Fix: Detect the supported timeout flag dynamically in the probe script (`-rw_timeout`, then `-timeout`, then `-stimeout`) before running any live RTSP validation. Keep direct Reolink credentials out of tracked files and pass them at runtime instead.
Next time: Before trusting an RTSP failure from a new shell or script, inspect the local FFmpeg build's supported timeout flags first.

[2026-05-20] - North Yard SD feed and Scrypted snapshot reliability
Oops: North Yard's go2rtc SD feed was alive, but the bridge sometimes decided the SD alias was not ready and fell back to the dead `north-yard` path. Scrypted was also still pointed at `rtsp://192.168.1.244:19554/north-yard`, which correctly returned 404 in SD_ONLY mode.
Why: The per-alias go2rtc readiness probe could time out under load even while the full stream table showed a healthy `north-yard-sd` producer on `192.168.1.175`. The stale Scrypted URL then kept HomeKit/Scrypted on the removed main alias.
Fix: Added a fallback from the per-alias go2rtc stream-details request to the full stream table, rebuilt the active local production add-on, and repointed Scrypted device `211` to `rtsp://192.168.1.244:19554/north-yard-sd` with snapshots-from-prebuffer enabled.
Next time: For North Yard in SD_ONLY mode, verify the bridge API reports `native_alias=north-yard-sd`, probe `http://172.30.32.1:11984/api/frame.jpeg?src=north-yard-sd`, and verify Scrypted device `211` with `/endpoint/@scrypted/snapshot/211/Camera`; do not trust the old `north-yard` RTSP URL.

[2026-05-17] - Bridge readiness must not reuse the catalog endpoint
Oops: The sidecar readiness loop could treat `/api` as ready even when `/api` was deliberately returning `{"status": "loading"}` during catalog startup.
Why: `/api` is a user/catalog endpoint, not a startup gate. Once loading is a valid response, HTTP 200 no longer proves aliases are seeded.
Fix: Added `/api/ready` as the explicit gate and made the sidecar wait on that route before using bridge catalog filtering.
Next time: When adding a loading state to a route, update every startup poller that used that route as a readiness signal.

- 2026-05-17 - WHEP startup guard must check the port, not just HTTP
Oops: The WHEP launcher was using an HTTP probe to decide whether an existing listener should block startup, and that left room for a bound-but-stuck listener to fool the check.
Why: HTTP success only proves the proxy answered a request. It does not prove the port is free to reuse or that the existing listener is healthy enough to trust.
Fix: Switched the startup guard to a raw TCP connect check with a short timeout, kept the cleanup recheck on the same probe, and added a packaging test so the runtime entrypoints keep using the port probe instead of the old stream route.
Next time: When the bug is about "duplicate listener" or "port already in use," verify the socket itself before trusting a route-level health URL.
- 2026-05-09 - Scrypted Wyze snapshot prebuffer bypass
Oops: HomeKit-facing Scrypted snapshot checks kept using `/endpoint/@scrypted/snapshot/<id>` and returned 500, which looked like the snapshot plugin was still broken after disabling prebuffer.
Why: The working Snapshot plugin HTTP path includes the requested interface, for example `/endpoint/@scrypted/snapshot/153/Camera`; the shorter path has an empty interface and returns 500 by design. Also, Scrypted Snapshot `Default` still falls back to prebuffer when the camera snapshot path fails.
Fix: Patched the live Snapshot plugin bundle to force camera ids 153, 154, and 223 away from prebuffer, restarted Scrypted, then verified `/endpoint/@scrypted/snapshot/<id>/Camera` returned 200 with non-empty JPEGs.
Next time: Verify the full endpoint shape before judging Scrypted snapshot health, and treat Snapshot `Default` as "URL first, prebuffer fallback" rather than "prebuffer disabled."
- 2026-05-10 - South Yard Scrypted snapshots and OpenCV analysis
Oops: South Yard's fast Wyze `/img/south-yard.jpg` and Scrypted snapshot endpoint were reliable, but go2rtc `south-yard-sd` frame probes returned `200` with `0B`, and Scrypted OpenCV analysis showed VAAPI-style `Failed to sync surface` / `Input/output error` failures.
Why: The snapshot path was not the failing part; the risky path was live RTSP/OpenCV analysis using hardware-backed frame download, while the go2rtc SD frame route was not a usable fallback for still frames.
Fix: Added South Yard device `155` to the targeted Snapshot plugin prebuffer bypass, then patched the live OpenCV plugin fallback format from `gray` to `rgb` so existing software conversion can make grayscale frames after buffer download. After restart, Scrypted snapshot was `200` in about `12ms`, bridge `/img` was `200` in about `3ms`, go2rtc SD still returned `0B`, and the Analyze trial had zero recent matching South Yard errors.
Next time: Do not switch South Yard to go2rtc `south-yard-sd`; benchmark it as broken for frames. If OpenCV errors return after a longer soak, focus on disabling or avoiding hardware frame download in the Scrypted analysis path rather than changing snapshot URLs.
- 2026-05-10 - Snapshot cache mtimes can lie
Oops: Several Wyze snapshots looked fresh because the HA refresher and some bridge snapshot probes rewrote or touched `/media/wyze/img/*.jpg` even when the visible picture was old.
Why: File mtime and HTTP `200` only prove a route returned bytes. They do not prove the camera produced a new frame; some routes can serve stale cache or update cache metadata without a visible timestamp change.
Fix: Patched `/config/wyze_north_yard_snapshot_fix.sh` on the HA host so it only replaces a cache file when the downloaded bytes differ, changed Garage to prefer the real `garage-sd` frame route, and refreshed South Yard/Back Yard top-level caches from their live `*-sub` snapshots. Verified South Yard, Back Yard, Garage, Deck, and Hamster by recent cache age plus OCR/visual timestamp where readable.
Next time: For camera snapshot work, always compare hashes across time and review the visible camera timestamp. Treat North Yard as unhealthy until `north-yard` produces a non-empty changing frame or visible current timestamp; a stale cache with a successful HTTP response is not enough.
- 2026-05-10 - North Yard stale snapshot root cause
Oops: North Yard kept serving a stale `/media/wyze/img/north-yard.jpg` while bridge/go2rtc probes returned `0B`, timeouts, or stale cached bytes.
Why: The live Wyze API reported North Yard at `192.168.1.179`, but the helper/go2rtc config had been forcing older IPs (`192.168.1.177`/`192.168.1.178`). A temporary SD-mode experiment exposed `north-yard-sub` as `connecting`, but the working route was the HD/native `north-yard` go2rtc path with the current IP.
Fix: Restored the bridge option for North Yard to HD/native mode, patched `go2rtc_wyze.yaml` and `/config/wyze_north_yard_snapshot_fix.sh` to `192.168.1.179`, restarted Wyze Bridge/go2rtc, and restarted a single refresher loop. Then `north-yard` produced non-empty changing frames and the visible timestamp read around `2026-05-10 10:16`.
Next time: Before changing stream modes, compare `/api/<camera>` IP/source data with `go2rtc_wyze.yaml`. For Wyze Cam V4 North Yard, a current IP on the HD/native route matters more than trying an SD/substream fallback.
- 2026-05-10 - Wyze HomeKit snapshot health loop
Oops: I initially treated HTTP 200 responses and cache mtimes as proof that snapshots were fresh, then a refresher loop made that lie worse by rewriting/touching unchanged or tiny cache files.
Why: Scrypted `Default` can fall back to prebuffer, Wyze Bridge `/img` and `/snapshot` can serve cached files, and probes can update the same cache they are measuring.
Fix: Verify at the HomeKit-facing Scrypted endpoint with status, size, hash, and visual timestamp when possible. Reapplied the live Scrypted Snapshot guard so devices `153`, `154`, `155`, and `223` avoid flaky prebuffer snapshots; confirmed Back Yard uses Scrypted device `182`, not the small bridge `back-yard` cache, and restarted the bridge to refresh North Yard.
Next time: Build the route matrix first, keep unsafe refresh helpers stopped until they have locking/unique temp files/size checks/no mtime touching, and do not call the system healthy until a multi-round Scrypted endpoint soak stays non-empty.
- 2026-05-12 - 4.3.1 camera freshness regression
Oops: Production 4.3.1 looked started, but North Yard stayed 24h stale and Garage/Deck HomeKit snapshots were old while local/dev Wyze Bridge add-ons were also running.
Why: The local/dev add-ons held native go2rtc ports `11984/19554`, so production's go2rtc API answered with an empty stream table. The cache refresher also tried dead native routes for Garage/Deck instead of the live bridge `*-sub` images.
Fix: Stopped `local_docker_wyze_bridge_local` and `local_docker_wyze_bridge_v4`, restarted production, rewrote `/config/wyze_north_yard_snapshot_fix.sh` to refresh South/Back/Garage/Deck from `/img/*-sub.jpg`, Hamster from `/img/hamster.jpg`, and North Yard from go2rtc `frame.jpeg?src=north-yard`, then verified Scrypted devices `153`, `154`, `155`, `211`, `223`, and Back Yard candidates `182`/`265`.
Next time: Before debugging camera routes, confirm only the production Wyze Bridge add-on is started and that `http://192.168.1.244:11984/api/streams` contains producers for the native aliases.
- Keep repo-tracked env files free of secrets. Use git-ignored local override files for any real keys or host-specific values.
- On this setup, do not inspect local Docker or OrbStack when debugging Wyze Bridge. The source of truth is the Home Assistant host over SSH, using `scripts/ha_ssh.sh`, `ha apps logs`, and HA-side diagnostics.
- When an SSH-driven inspection needs to pipe remote command output into a local parser, do not feed the local parser script through the same stdin with a heredoc. Write the remote output to a temp file first, then parse that file locally, or the parse step can hang or read an empty stream.
- On the Home Assistant SD-only production setup, an empty authenticated bridge `/api` camera catalog at startup is not proof that every camera has no enabled native feeds. If `go2rtc_sidecar.sh` treats that empty catalog as authoritative, it can rewrite `go2rtc_wyze.yaml` with an empty `streams:` block and make Scrypted/HomeKit native `:19554/*-sd` cameras fail with `404 Not Found`. Fall back to explicit `/api/<camera>/stream-config` feed flags until the catalog is populated.
- When Home Assistant exposes explicit per-camera `CAM_OPTIONS` `HD` or `SD` booleans, treat them as higher-priority than stale saved feed settings in `/config/wyze_camera_settings.json`. Otherwise a saved `sd=true` can silently resurrect a bridge `-sub` path like `north-yard-sub` even after Supervisor config changes it to `false`.
- For patch-release wrap-up, re-check the public repo homepage too. It is easy for `home_assistant/CHANGELOG.md` and `docs/user_guide/release_notes_v4.md` to mention the new patch while `README.md` still advertises the previous one.
- Never embed stream credentials into URLs rendered by the UI or returned by the API. Use request headers for authenticated playback instead.
- Keep internal bridge and proxy surfaces on loopback when they are only meant for local process-to-process traffic.
- Put `.dockerignore` at the build-context root so local override files like `*.env.local` and `build.env.local` cannot enter image builds.
- When a secret reaches git history, rewrite it from a clean clone, verify the literal value is absent from `git rev-list --all`, and only then update public refs.
- Home Assistant local add-ons need a distinct slug to coexist with production; if they also share `host_network` and fixed ports, only one should be started at a time during staging.
- Some Home Assistant boxes block Supervisor store reloads; in that case, reuse an already-indexed local add-on slot for the dev lane and mirror both `config.yml` and `config.yaml` so local discovery stays reliable.
- When posting JSON to the Home Assistant Supervisor API over SSH, do not try to reuse stdin for both the remote shell script and the request body. Upload the payload as a temp file first, then have the remote curl command read that file.
- On some Home Assistant systems, a reused local add-on slot can keep stale schema/translation metadata in Supervisor even after source sync, rebuild, reload, and reinstall. Verify the remote manifest files directly before trusting the config UI as a validation signal.
- On this HA box, even a freshly copied local add-on folder may not become indexable immediately after `ha supervisor reload`. If you need UI-proof validation, prefer a slot Home Assistant has already indexed or plan for heavier Supervisor-side refresh steps.
- On this HA box, `ha store reload` is the important local add-on metadata refresh step; run it before `ha supervisor reload` when validating local add-on schema or translation changes.
- Exclude macOS `.DS_Store` and `._*` files from HA local add-on syncs. Supervisor will try to parse files like `translations/._en.yml`, and that can poison local add-on metadata refreshes.
- In zsh, `status` is a special read-only parameter. When wrapping release commands in shell one-liners, use a different variable name for exit codes.
- Home Assistant backup restores only accept add-on payloads wrapped with the same SecureTar format Supervisor writes. A plain tar can look valid enough for `ha backups info` yet fail restore with a fake `Invalid password` error. When you need to inspect or patch `.local` backups, use Supervisor's `GET /backups/{slug}/download?location=.local` endpoint and rebuild inner add-on archives with `securetar`, not plain `tar`.
- On this HA box, manually patched backups are picky about outer-tar shape too: if you rebuild them on macOS, strip AppleDouble metadata and mirror the original member names/order (`09e60fb6_scrypted.tar*` first, then `./backup.json`) or Supervisor may refuse to index the backup even when the payload itself is valid.
- On this HA box, Scrypted Snapshot-plugin settings live under the Snapshot plugin device storage, keyed like `mixin:<camera-id>:snapshotsFromPrebuffer`. For `South Driveway E1 CX` and `Reolink Doorbell`, the live camera ids were `182` and `54`, and changing those values via a patched Scrypted backup plus partial restore did restart cleanly.
- For the March 23, 2026 snapshot experiment, forcing `mixin:182:snapshotsFromPrebuffer` and `mixin:54:snapshotsFromPrebuffer` from `Default` to `Enabled` did not produce enough clear improvement in the observed live log window to justify leaving it in place, so the box was restored back to the original `c20d03f1` backup immediately after verification.
- On this HA box, Frigate consumes Scrypted RTSP rebroadcast feeds, not direct Wyze Bridge URLs. If Frigate starts crash-looping on those feeds, removing `hwaccel_args: preset-vaapi` is not enough because Frigate can auto-detect VAAPI on restart; set `ffmpeg.hwaccel_args: ""` explicitly and add `input_args: preset-rtsp-restream` to the Scrypted RTSP inputs to stop the repeated `Failed to sync surface` / `Unable to read frames` failures.
- Disabling `north-yard` audio inside Scrypted changed the RTSP stream to video-only, but it did not stop `Unable to find sync frame in rtsp prebuffer` or Frigate `north_yard` decode/timestamp crashes. That points back to the upstream video/rebroadcast path, not audio transcoding, so roll that tweak back after testing.
- Before planning a Scrypted RTSP URL swap on this HA box, probe the live bridge paths directly. `north-yard-sub` and `north-yardfw` both returned `400 Bad Request`, and Wyze Bridge logs explicitly said those paths were not configured. In that situation, keep your hands off speculative Scrypted URL edits and follow the upstream bridge/WHEP failures instead.
- On this HA box, `north-yard` is a Wyze Cam V4 (`HL_CAM4`) and the live bridge starts it as a MediaMTX `WebRTC source`. With `CAM_OPTIONS` empty and no configured `-sub`/`fw` path, there is no clean per-camera source swap available from Scrypted alone; the next meaningful experiment has to happen in bridge-side config/code.
- On this HA box, seeing a patched line in `/addons/local/wyze_bridge/whep_proxy/main.go` is not enough to prove the running add-on picked it up. After a `ha apps rebuild --force`, confirm the live logs actually show the new text before trusting a WHEP proxy instrumentation change.
- When testing a "keep peer alive after websocket 1001" path, do not `continue` the websocket read loop after closing and nil-ing `session.wsConn`. Exit that goroutine cleanly or the experiment can destabilize the local WHEP proxy state.
- For the March 26 dev-lane retest, exiting the websocket reader cleanly after the healthy `1001 Going away` rotation avoided the previous immediate cascade of `127.0.0.1:8080` refusals and MediaMTX path failures in the initial post-rotation observation window.
- When probing add-on internals from the HA shell, do not assume a failed direct request to `127.0.0.1:<port>` proves the in-container listener died. Compare that probe with user-facing behavior (for example fresh Frigate `dog_run` snapshots) before concluding the bridge actually collapsed.
- Prefer an in-app diagnostics endpoint for WHEP proxy and MediaMTX checks when working with Home Assistant add-ons. That keeps the probe inside the bridge namespace and avoids false conclusions from shell-side network visibility.
- In the March 26 longer dev-lane soak, `dog-run` remained healthy well after a second observed `1001 Going away` rotation while in-app diagnostics still showed `whep_proxy` `upstream_alive=true` and `can_reuse=true`. That means the clean goroutine exit is enough to preserve the upstream peer at least across the next full rotation window.
- If the add-on sets `MTX_APIADDRESS` but not `MTX_API=true`, MediaMTX will not listen on the Control API port. Diagnostics should report that API is disabled instead of implying the listener is mysteriously broken.
- On this HA box, a dev-lane restart loop with `MediaMTX` `Process exited with 1` was caused by a host-network port race, not a broken bridge config. The key error was `listen udp :58000: bind: address already in use`, so the dev swap flow must wait for the shared prod/dev ports to fully clear before starting the next add-on.
- Keep `scripts/ha_dev_build.sh` minimal. It is a bridge handoff helper, not the place for stream-specific readiness logic. Use it to serialize prod/dev swaps and verify `/health`; use `scripts/ha_bridge_diag.sh` and live logs for the real bridge investigation.
- A WHEP stream should not look reusable or offer downstream tracks just because local `TrackLocalStaticRTP` objects were allocated. Gate reuse/output on real upstream media readiness (`video_ready` / `audio_ready`) or downstream clients can attach during the startup gap and amplify `deadline exceeded while waiting tracks` failures.
- On this HA box, a dev-lane swap can still report `/health` even when bridge initialization is half-broken. After the latest redeploy, Wyze auth succeeded but `get_user_info` returned `None`, `wyze_bridge.py` crashed on `.email`, and the bridge stayed up enough to answer health probes with `active_streams: 0`. Check init logs, not just `/health`, before trusting a dev-lane runtime validation.
- If Wyze login succeeds but `get_user_info` fails during startup, do not let bridge init crash on `self.api.get_user().email`. Build a fallback local user profile from the configured Wyze email so local auth and stream setup can continue, then log that the richer account profile is missing.
- After the fallback-user fix, the dev-lane `dog-run` startup path no longer reproduced the earlier immediate `deadline exceeded while waiting tracks` window in the validation pass; confirm with `/health/details?stream=dog-run` and live bridge logs, not just Frigate restart noise.
- For patch releases, bump the mirrored version surfaces together: public README, HA README, changelog, release notes, `config.yml`, mirrored dev manifests, and both visible build env files/tests. Leaving one behind creates noisy release prep and confusing add-on metadata.
- For patch releases, do not stop at the changelog and GitHub Release page. The GitHub repo homepage is the README, so make sure the current patch notes are visible there too or users will think the release notes are missing.
- For public release docs, distinguish between model-specific limits and one-box camera nicknames. It is fine to publish V3/V3 Pro/V4/Bulb Cam ceilings and whether a meaningful main/sub split exists, but not fine to describe public support in terms of household camera names or incident logs.
- Do not publish a broad public Home Assistant native `go2rtc` feature description while `home_assistant/app/run` is still hardcoded to a single camera alias. Either keep the docs narrow or generalize the runtime first so the public contract matches the shipped code.
- On this HA box, a healthy bridge diagnostic for `north-yard` does not prove the full path is healthy. March 27, 2026 showed `north-yard` fully ready in Wyze Bridge while Scrypted still logged repeated `Unable to find sync frame in rtsp prebuffer` and `non-existing PPS 0 referenced`, so keep bridge health and Scrypted rebroadcast health as separate checks.
- Do not assume `south-yard` is consumed downstream just because the production bridge publishes it. In the March 27, 2026 live check, Frigate had no `south_yard` camera configured, while an extracted Scrypted backup still exposed only stale snapshot/HomeKit traces for `south-yard` rather than a current active downstream route.
- The recurring Scrypted `EHOSTUNREACH 192.168.1.217:8000` noise on this HA box maps to an offline ONVIF/Reolink `Back Yard` device, not to `north-yard` or `south-yard`. Confirm by correlating the IP from extracted Scrypted backup data and a direct HA-side ping/port check before treating that error as part of the Wyze bridge path.
- The recurring Scrypted `ECONNREFUSED 127.0.0.1:38823` noise appears during `@scrypted/nvr` worker startup on this box. It can coincide with `north-yard` motion-analysis forks, but the stream may still recover and decode afterward, so do not treat that localhost refusal alone as proof of a fatal `north-yard` outage.
- When the local dev Wyze Bridge add-on is crash-looping on shared host-network ports, stop it before doing production bridge diagnostics. Leaving it running muddies log interpretation even if the production add-on stays up.
- A fresh raw `scrypted.zip.backup` from the live box can expose the current `ScryptedUser` token, and Scrypted will accept that token as the password on `POST /login`. On this HA box, that gave a safe live admin path without another HA add-on backup restore.
- If a Wyze camera should use Wyze MQTT motion instead of Scrypted software motion, replacing the `Custom Motion Sensor` target alone is not enough. Remove mixin `63` from the camera as well so the `ObjectDetection:true:63` software-motion interface disappears and Scrypted stops launching that camera's motion-analysis worker.
- When creating or repairing MQTT motion devices live through the Scrypted MQTT plugin, immediately re-audit the resulting device names and camera-to-motion assignments. In this March 27 repair pass, live device ids and names drifted enough that `Deck` and `HAMSTER` had to be explicitly reattached after creation.
- On this HA box, the production bridge could expose a Wyze WebRTC path to MediaMTX before video was ready. That let RTSP clients latch an audio-only stream (`1 track (G711)` on `deck`) and crash Frigate detection. In `whep_proxy/main.go`, only expose tracks after `videoReady` is true; audio-only should not be offered to RTSP consumers.
- The `deck` audio-only bug is reproducible as a bridge issue, not a Frigate issue: production repeatedly showed `is reading from path 'deck', with TCP, 1 track (G711)`, while the dev-lane patch changed the same path to `2 tracks (G711, H264)` and Frigate immediately recovered `GET /api/deck/latest.webp ... 200`.
- Direct bridge high-resolution control is still incomplete for some Wyze WebRTC/KVS paths on this branch. Even after live `CAM_OPTIONS` set `QUALITY=HD180` for `NORTH YARD` and `SOUTH YARD`, direct RTSP stayed `640x360`. The current `get_camera_stream()` path only requests `provider: webrtc` with `use_trickle: true` and does not appear to pass an explicit quality selector, so this is a strong `4.0.3` investigation candidate.
- When live-enabling global `SUBSTREAM` on the bridge, snapshot `StreamManager` dictionary iteration before health/status walks. The first `/health` probe can arrive while new `-sub` streams are still being added and otherwise crash with `RuntimeError: dictionary changed size during iteration`.
- Bridge-managed KVS `-sub` paths should derive their proxy config from the requested RTSP URI (`north-yard-sub` -> base camera `north-yard`) instead of assuming every substream model is in `SUBSTREAM_FW`. That groundwork is enough to expose `deck-sub`, `garage-sub`, and `south-yard-sub` live, but `HL_CAM4` (`north-yard`) still does not hold a clean parallel main+sub pair in this environment, so treat its remaining `503`/`400`/decode churn as a separate `4.0.3` problem.
- For KVS/WebRTC cameras, do not eagerly call `setup_mtx_proxy()` during `setup_streams()`. MediaMTX already starts WHEP sources on demand, and the eager boot-time POST can create duplicate early upstream sessions during add-on restart/cutover. In the March 27 dev retest, removing that eager bootstrap let `north-yard` and `north-yard-sub` come up cleanly after redeploy without the earlier immediate `400 Bad Request` churn, even though the quality split is still wrong.
- The local fork had accidentally dropped the public upstream TUTK `SDK_KEY` from `IDisposable/docker-wyze-bridge`; sync that key into `app/.env`, `.ha_live_addon/app/.env`, `.ha_live_addon/app/build.env`, `home_assistant/app/.env`, and `home_assistant/app/build.env` before judging any TUTK result. After restoring it on March 27, hybrid `KVS` main + `TUTK` substream tests moved past license failure into real `HL_CAM4` connection attempts.
- The internal FFmpeg tee target must follow the configured MediaMTX RTSP port (`MTX_RTSPADDRESS`), not assume `8554`. On the dev add-on, MediaMTX listens on `58554`, so TUTK-fed substreams silently fail to publish unless FFmpeg targets `rtsp://0.0.0.0:<configured-port>/<uri>`.
- Even after restoring the SDK key and fixing the FFmpeg internal RTSP port, `HL_CAM4` `north-yard-sub` TUTK fallback still does not stay up in dev. With substreams forced onto `IOTC_Connect_ByUID_Parallel`, live logs still end in `IOTC_ER_TIMEOUT` or `IOTC_ER_CAN_NOT_FIND_DEVICE` before `Getting camera params...`, so the remaining blocker is the actual `HL_CAM4` TUTK handshake/path, not MediaMTX publication anymore.
- Upstream `IDisposable/docker-wyze-bridge` also mutates `stream.user.phone_id = stream.user.phone_id[2:]` for substreams in `app/wyzecam/iotc.py`, so that quirk was not introduced by this fork. I removed the mutation locally and proved it does not change the current `HL_CAM4` hybrid failure mode: `north-yard-sub` still never gets past TUTK connect, and logs still end at `IOTC_ER_CAN_NOT_FIND_DEVICE` before authentication begins.
- When the remaining question is “what did Wyze actually return for this KVS camera?”, trace both layers: the raw `get_camera_stream()` payload and the derived `get_kvs_proxy_config()` output. Gate the trace behind `KVS_TRACE_STREAM`, and redact tokens, phone ids, usernames, and ICE credentials so the live logs stay safe to share.
- In the March 27/28 live `KVS_TRACE_STREAM` pass, `north-yard` `HL_CAM4` returned only the generic Kinesis signaling bundle plus simple device-state properties even when the bridge requested `quality=hd180`. The traced payload exposed no explicit alternative profile, stream variant, or resolution selector field, so the next fix is unlikely to be a missed local mapping of an obvious Wyze-provided HD/2K option.
- For the Home Assistant dev add-on lane, changing `/addons/local/<slug>/app/.env` after the add-on image is already built is not enough to change runtime behavior. Dev-only probe envs such as `HL_CAM4_MAIN_PROBE_MODE` and `TUTK_TRACE_STREAM` must be present in the source tree before `deploy_ha_local_addon.sh --target dev` rebuilds the add-on, or the running container will keep the old baked-in values.
- In the March 27 `HL_CAM4_MAIN_PROBE_MODE` live comparison, `north-yard` main cleanly took all three intended routes: `kvs` stayed on the WebRTC/KVS path, `tutk_dtls` used `IOTC_Connect_ByUIDEx (DTLS=1)`, and `tutk_parallel` used `IOTC_Connect_ByUID_Parallel`. Both TUTK main modes still left `rtsp://192.168.1.244:58554/north-yard` unreadable with `404 Not Found`, so the remaining `HL_CAM4` blocker is not just “KVS chose the wrong main path.”
- If gated debug output matters in the Home Assistant dev lane, do not rely on `logger.info(...)` alone inside the TUTK worker path. In the March 27 `HL_CAM4` probe follow-up, the new `TUTK_TRACE_STREAM` lines only became visible once they also used `print(..., flush=True)`. With that change in place, live `tutk_dtls` logs proved `north-yard` main reaches `Calling iotc_connect_by_uid_ex...` and then hangs before any `connect_result`, which is much more actionable than the earlier silent failure.
- The March 27 upstream compare against `kroo/wyzecam`, `mrlt8/docker-wyze-bridge`, and `IDisposable/docker-wyze-bridge` did not uncover a hidden `HL_CAM4` timeout fix. All three keep the low-level TUTK connect path as a blocking native call with no watchdog around `IOTC_Connect_ByUIDEx`; any retry logic only runs after that call returns an error.
- `AlexxIT/go2rtc` is not just a prettier wrapper around `docker-wyze-bridge` for Wyze cameras. Its native `wyze://` source uses a separate direct DTLS/P2P implementation with explicit 5-second handshake, AV login, and auth timeouts, while its `webrtc:...#format=wyze` path is only the legacy bridge integration. That makes `go2rtc` a useful design reference for bounded connect/auth timing, but not proof that the current bridge TUTK SDK path should already work for `HL_CAM4`.
- For the dev-only `HL_CAM4` connect-watchdog experiment, the narrow lever is `IOTC_Connect_Stop_BySID`, not `_disconnect()`. Arming a tiny timer only after `iotc_get_session_id()` succeeds and canceling it immediately after the native connect call returns is enough to release a wedged DTLS or parallel connect in local tests without false-firing on fast successful connects.
- In the March 27 live dev-lane retest, the `HL_CAM4` connect watchdog turned the old silent `north-yard` TUTK hang into a concrete SDK result on both main DTLS and main/substream parallel connects: after `IOTC_Connect_Stop_BySID`, the native call returned `-27 IOTC_ER_FAIL_CONNECT_SEARCH` in about 8 seconds instead of hanging forever. That is progress, but not a fix by itself.
- Once the watchdog started surfacing `-27`, the existing retry logic stopped helping because it only retried `-13` and `-23`. A small dev-only follow-up that treats watchdog-triggered `-27` as retryable did work live: the add-on now logs `retrying 2/3` and `retrying 3/3` for `north-yard`, but repeated retries still end in the same `404` / unreadable path. So the remaining blocker is the `HL_CAM4` search/connect path itself, not just retry classification.
- When you add dev-only attempt metadata to the `TUTK_TRACE_STREAM` `connect_result` event, include the retry attempt number there instead of inventing a second parallel trace. On the March 28 follow-up, `attempt_no`, `max_retries`, and `watchdog_fired` on the existing `connect_result` line were enough to make the live `north-yard` retry loop readable without broadening the experiment.
- On this HA box, native `go2rtc` `wyze://` sidecars are not a current shortcut around the bridge problem. A March 28 throwaway `go2rtc` binary on alternate ports used the official `api/wyze` endpoint to generate native URLs, and both `north-yard` (`HL_CAM4`) and `hamster` (`HL_CAM3P`) still failed with `wyze: connect failed: discovery timeout`. That means the native `go2rtc` timeout is box-wide here, not evidence of an `HL_CAM4`-only regression in the bridge.
- The March 29 north-yard retest overturned that earlier blanket conclusion. On this HA box, the dev add-on's live native `go2rtc` API can return working direct-DTLS helper URLs for `north-yard`, and externally seeding those exact URLs into `/api/streams` produces a real `2560x1440` main feed plus a `640x360` `-sd` feed on `:19554`.
- The stubborn part was not Wyze or `go2rtc`, it was startup orchestration. The in-container bootstrap could not reach its own `go2rtc` API reliably even when that same API was reachable from outside the add-on, so the safe fix was to preserve a previously seeded `/config/go2rtc_wyze.yaml` instead of overwriting it back to `streams:` on every restart.
- When enabling a Frigate `detect` role for a camera, do not leave `motion.enabled: false`. Frigate will reject the config and fall back to safe mode because object detection requires motion detection to stay enabled.
- The March 29 South Yard native retest confirms that `HL_BC` does not mirror the North Yard V4 breakthrough on this box. Even with working native `go2rtc` aliases and direct-DTLS helper URLs, both `south-yard` and `south-yard-sd` stayed `640x360` in RTSP and `frame.jpeg`.
- Once the North Yard native `go2rtc` path is proven in `.ha_live_addon`, mirror only that proven slice into `home_assistant/`. Do not "helpfully" copy South Yard into the production source tree just because the sidecar exists there too; on this box, `south-yard` still validates at `640x360`, so the correct production-source mirror is North-only until South proves otherwise.
- The March 29 South Yard six-option sweep closed the remaining AI-executable dead ends on this HA box. Extending the existing native `go2rtc` sidecar to include `south-yard`/`south-yard-sd` still served `640x360` on both aliases, so the sidecar itself is not the missing HD/substream lever for `HL_BC` here.
- For `HL_BC` on this box, forcing main-stream TUTK (`tutk_dtls`) can complete the connect/auth path, but it still does not rescue quality. The camera reports LAN/HD session metadata, yet the live frames stay `frame_size=1` / about `30` kbps, `south-yard` never becomes a stable readable RTSP path, and `south-yard-sub` remains unconfigured.
- Treat `PRO_CAMS += HL_BC`, alternate `QUALITY` strings (`HD240`, `HD60`, `SD120`, `HD`), and KVS signaling URL query-parameter injection as exhausted for `south-yard` on this box. The quality strings were all accepted by Supervisor but still produced `640x360`, and appending `definition=hd&quality=high&profile=main` to the live HL_BC WebRTC signaling URL only caused repeated websocket `bad handshake` retries before the stream settled back to the same `640x360` output.
- **March 29, 2026**: The AI-executable software-only path for HL_BC is functionally exhausted. The complete 8-option sweep (original 6 + tutk_parallel + combined PRO_CAMS+go2rtc) confirms no bridge-side configuration can unlock 2K streaming. The remaining viable options require either: (1) network-level reverse engineering (port scanning, traffic analysis, API brute force - documented in new testing suite), (2) firmware flashing (Thingino/OpenIPC), (3) hardware replacement (Wyze Cam V3 Pro), or (4) accepting 360p with max bitrate optimization.
- The one real TUTK gap after the first South Yard sweep was `HL_BC` main `tutk_parallel`. That path is worse than `tutk_dtls` on this box: it fails immediately with `IOTC_ER_DEVICE_REJECT_BY_WRONG_AUTH_KEY (-68)` before auth/stream setup, so it is not a viable fallback for `south-yard`.
- Combining `PRO_CAMS += HL_BC` with the native `go2rtc` sidecar also adds no value for South Yard here. After a clean rebuild with both changes present, `rtsp://...:19554/south-yard` and `south-yard-sd` still probed `640x360`, which means the go2rtc result does not depend on the bridge's pro-camera capability map.
- On March 28, the new in-app `NETWORK_TRACE` proved the Home Assistant dev add-on is not living in the same bridged Docker pocket as `core-ssh`. The add-on really runs with the host NIC/route (`enp1s0`, source IP `192.168.1.244`, gateway `192.168.1.254`) even though its `/etc/resolv.conf` still points at Supervisor DNS (`172.30.32.3`, `search local.hass.io`). Treat `core-ssh` and the host-network add-on as different environments.
- When two host-network Wyze Bridge add-ons are both marked `started`, `ha apps info` can still report the same add-on IP (`172.30.32.1`) and `scripts/ha_bridge_diag.sh` can only hit whichever bridge currently owns the shared host port. Do not trust `--target dev` versus `--target prod` on that script until the shared-port conflict is resolved.
- The March 28 host-network retest also showed the dev add-on can be "running" while already doomed by a shared-port collision: live logs repeatedly emitted `listen udp :58000: bind: address already in use`. Any fresh stream or environment conclusion from that state is suspect until prod/dev are separated or one side is stopped.
- The first dev-only native TUTK file-log probe tripped over a local ctypes bug: `LogAttr.log_level` expects an unsigned `c_uint32`, not `c_int`. After fixing that, the SDK did reach `IOTC_Set_Log_Attr`, but it still returned `-46 IOTC_ER_INVALID_ARG` for `/config/logs/wyze-tutk-iotc.log`, and no native log file was created. So native SDK logging is not a ready-made answer yet on this build.
- For this HA box, the clean way to keep prod and dev alive together is not more swap timing; it is a distinct dev host-network port block. Once the dev add-on was moved to `55000/59554/59888/59889/59189/18080/60997/...`, the old `:58000` collision disappeared and the dev-lane diagnostics became trustworthy again.
- `scripts/ha_ssh.sh` forwards arguments straight into `ssh`, which means a URL containing `&` can lose its tail on the remote shell if you do not escape it first. The `ha_bridge_diag.sh --network` bug on March 28 was exactly that: the helper looked correct locally, but Flask logs proved the remote request only arrived as `/health/details?stream=north-yard`. Escaping `&` before calling the SSH wrapper fixed it.
- When scraping hostnames out of `libIOTCAPIs_ALL.so`, filter out symbol-looking junk before treating the result as a DNS clue. The first March 28 pass surfaced fake "hosts" like `iotcapis.o` and `tutkssl.o`; requiring a plausible DNS-style TLD cleaned that list down to usable entries like `iotcplatform.com`, `kalay.net.cn`, and `kalayservice.com`.
- The March 28 `network=1` follow-up is strong evidence that generic external DNS resolution is not the main blocker on this HA box. From inside the real host-network dev add-on, the Wyze API hosts and the plausible TUTK/Kalay hosts all resolved successfully, even while `north-yard` stayed low-res and TUTK search/connect experiments still failed. Treat Supervisor DNS as an environment quirk, not yet as the root cause.
- For HL_CAM4 (V4) and HL_BC cameras on this HA box, both TUTK main-probe modes (`tutk_dtls` and `tutk_parallel`) fail at the IOTC connect layer: DTLS gives IOTC_ER_TIMEOUT (-13) and parallel gives IOTC_ER_FAIL_CONNECT_SEARCH (-27). The same host also fails native go2rtc `wyze://` discovery. This is a box-wide IOTC search/connect limitation, not a bridge-code bug. TUTK is not a viable quality rescue path for these cameras on this host.
- Adding `definition=hd` as a query parameter to both `webrtc.api.wyze.com/signaling/device/{mac}` and the `/v4/camera/get_streams` `parameters` dict has no effect. Wyze ignores those extra params and returns the same 360p KVS signaling URL. The `/v4/camera/get_streams` endpoint returns one stream object per device with no quality selector field — the camera firmware decides what resolution to push to its KVS channel independently. Until Wyze exposes a separate HD WebRTC profile endpoint, north-yard (HL_CAM4) and south-yard (HL_BC) are confirmed at 640x360 via all currently accessible API paths.
- On this HA box, Frigate snapshots return from inside the Frigate container at `http://172.30.33.6:5000/api/<cam>/latest.jpg`, not via the host-mapped port `8971` which requires HTTPS+auth from core_ssh. Always use the internal Docker bridge IP when probing Frigate from core_ssh.
- The production bridge WHEP proxy accumulates stale `-sub` session retry loops even when `SUBSTREAM=False`. These are harmless log noise from sessions created before the last restart; a bridge restart clears them. Do not confuse this churn with a runtime failure.
- March 28, 2026 full e2e validation confirmed: all 5 Wyze cameras (garage, deck, south-yard, north-yard, hamster) stream through the production bridge with video_ready=true, Frigate serves HTTP 200 snapshots for all 8 configured cameras, and MQTT-triggered motion from the bridge correctly fires Scrypted HKSV recordings on every Wyze camera without software motion.
- A later March 28, 2026 native go2rtc retest tightened the limit for `north-yard` and `south-yard` on this HA box. A dev-only go2rtc sidecar could be brought up cleanly on `:11984` API and a dedicated RTSP port `:19554`, and persistent `north-yard`, `north-yard-sd`, `south-yard`, and `south-yard-sd` aliases could be written into go2rtc config with canonical `wyze://<account>@<camera>?subtype=...` URLs. Even then, every native RTSP probe for those four aliases still returned `404 Not Found`, while the production bridge still exposed only `640x360` main feeds and both bridge `-sub` paths returned `400 Bad Request`. Treat that as current hard evidence that this box does not have an accessible high-res or working substream path for those two cameras through either the bridge or native go2rtc.
- For `4.1.1`, keep native talkback API-first and sidecar-mediated. Do not force it through the generic camera control route or claim browser-mic support until there is real live validation for a streaming-audio UX.
- The March 29, 2026 native talkback retest exposed two concrete bugs in the new API-first path: codec detection can false-negative on a cold native alias unless the helper probes `microphone=any` with a preload-and-retry step, and the `audio_url` branch must resolve `talkback_codec` before building the `ffmpeg:` source string. After fixing both, the dev add-on accepted an uploaded WAV via `/api/north-yard/talkback`, served it back over the loopback-only `/api/talkback-file/<name>` route, and `go2rtc` returned success with an `adts` pipe producer attached to `north-yard`.
- When running Python tests on macOS without Docker deps, stub `xxtea`, `paho.mqtt`, `wyzecam.iotc`, and other native/unavailable modules into `sys.modules` before importing any wyzebridge code. Do NOT stub `wyzecam` itself (still needed for `wyzecam.api_models`), but DO stub `wyzecam.iotc` to break the TUTK native `.so` chain. Set all `wyzebridge.*` stubs AFTER any block that clears `sys.modules["wyzebridge.*"]` entries — the clear wipes stubs set before it.
- `requests` stubs for test isolation need `PreparedRequest` and `Response` as attributes; `wyzecam/api.py` accesses them at import time.
- Flask `HTTPBasicAuth` returns 401 for all `/api/...` routes in tests unless auth is disabled. Set `WbAuth.enabled = False` inside the test `create_client()` helper to bypass it.
- When a test fixture class reads instance data at `__init__` time, reassigning the class variable mid-test has no effect on existing instances. Fix by making the instance method read directly from the class variable on each call instead of caching it at construction.
- Test port assertions must match the actual `app/frontend.py` port (`5000`), not the `.ha_live_addon/app/frontend.py` port (`55000`), when the test's `sys.path` points at `app/`.
- Even with the dev add-on on its own host-facing port block, do not trust a live validation window while prod and dev are both running if the dev logs show `whep_proxy` `listen tcp 127.0.0.1:8080: bind: address already in use`. The reliable pattern on this HA box is still: rebuild the dev slot, stop prod, start dev cleanly, run the camera probes, then restore prod.
- The March 29, 2026 HA SSH retest confirms the current Bulb Cam result did not change after the `4.1.1` build refresh. In a clean dev-only window, `south-yard` remained bridge-first in `/health/details`, `rtsp://homeassistant.local:59554/south-yard` stayed `640x360`, and both native go2rtc paths (`:19554/south-yard` and `:19554/south-yard-sd`) also stayed `640x360`, while bridge `south-yard-sub` still returned `400 Bad Request`.
- The Bulb Cam follow-up closed the gap in the settings model too: a camera can support a useful SD feed while still lacking a real HD feed. Do not model per-camera feed selection as only `main/sub/both`; expose independent `HD` and `SD` availability, let each feed carry its own kbps target, surface the feed's actual/reportable resolution, and grey out whichever feed level the camera does not truly support.
- On this HA box, the March 29, 2026 live settings-UX validation could not prove the disabled `Sub` button state from the real Web UI because every visible camera currently reported `supports_substream: true` from `/api/<cam>/stream-mode`. When that happens, finish the live proof with a real mode round-trip on a supported camera and rely on the repo tests/code paths for the unsupported-camera disable case instead of inventing a fake live failure.
- When a camera is configured `SD`-only, the main card can disappear and leave only the `-sub` card visible. Do not hide feed-selection controls on every sub card blindly. Show them on the surviving card when the main card is absent, and point that UI back at the base camera slug so an `SD`-only camera like the Bulb Cam still has a usable settings surface.
- Home Assistant add-on config pages do not support visual section headers, dividers, or markdown help. If the form feels overwhelming, the real tools are field order, better descriptions, and removing niche settings from the schema rather than trying to fake UI structure that Supervisor will not render.
- Home Assistant translations do support nested `fields:` entries for list-of-object schema items. Use that for `CAM_OPTIONS` so users see real labels and explanations instead of raw env-style keys like `HD_KBPS`.
- On this HA box, a local add-on uninstall/install really does clear required option values back to the manifest defaults. If you use reinstall as the heavy hammer to flush stale schema metadata, immediately restore the dev add-on settings afterward before trying to start it again.
- If a Home Assistant add-on needs fixed internal MediaMTX ports for prod/dev lanes, prefer baking those ports into the HA runtime env files instead of exposing a raw `MEDIAMTX` list in the user-facing config form.
- On this HA box, Supervisor metadata can stay stale across rebuilds, reloads, and even reinstall cycles for a reused local add-on slot. The reliable verification path is: check the remote manifest files directly, then confirm the loaded `ha apps info --raw-json` schema/translations changed before trusting the Home Assistant config page.
- For release-prep branches that mirror three runtime trees, normalize the shared frontend files from one canonical copy and preserve only the proven environment-specific deltas, such as the dev add-on's `:55000` loopback talkback URL versus `:5000` in the other trees.
- For patch or minor releases, bump every visible version surface together: root `.env`, production add-on `.env` and `build.env`, dev add-on `.env` and `build.env`, both add-on manifests, packaging tests, and release docs. Leaving one behind creates noisy last-mile cleanup.
- When bundling `go2rtc`, do not assume overriding only `api.listen` and `rtsp.listen` is enough. Upstream still enables WebRTC on default port `8555` unless the config explicitly overrides `webrtc.listen`, and on Home Assistant that can silently break Frigate startup. Keep a packaging test that checks the generated sidecar config disables or relocates the default WebRTC listener in every runtime tree.
- When Home Assistant add-on `CAM_OPTIONS` exposes explicit per-camera `HD` and `SD` booleans, `camera_feed_config()` must treat those env-backed values as the runtime default source before falling back to the older `STREAM` shortcut. Otherwise a user's visible add-on config and the actual running feed state can diverge unless `/config/wyze_camera_settings.json` happens to exist.
- For the Home Assistant `go2rtc` sidecar, fixing the generated default config is not enough if startup preserves a previously seeded `/config/go2rtc_wyze.yaml`. Normalize the preserved config's `api`, `rtsp`, and `webrtc` listener blocks on every boot so stale `:8555` settings cannot survive across rebuilds while the seeded stream aliases remain intact.
- On this HA box, `/addons/wyze_bridge_v4lab` is a separate local store entry (`local_docker_wyze_bridge_v4lab`), not the real production source for `0eb0428f_docker_wyze_bridge_v4`. If a rebuilt production container still shows old startup markers or old version labels after syncing that folder, stop assuming it is the live build root and find the true promotion path before trusting another production rebuild.
- For the managed `0eb0428f` production add-on, GitHub pushes can take an extra store refresh cycle before `ha apps info 0eb0428f_docker_wyze_bridge_v4 --raw-json` reports `version_latest: 4.2.1`. Use `ha store reload`, `ha store repair 0eb0428f`, then re-check the installed add-on metadata until `update_available` flips true before running `ha store apps update 0eb0428f_docker_wyze_bridge_v4`.
- On the March 29, 2026 Frigate CPU pass, camera-scoped `cameras.<name>.ffmpeg.hwaccel_args: preset-vaapi` was the safe way to test Intel N100 VAAPI on this HA box. Frigate 0.17 accepted that per-camera override, exposed active `intel-vaapi` clients in `/api/stats`, and let `south_driveway`, `north_driveway`, and `doorbell` be enabled one at a time without repeating the earlier global auto-detect crash loop. Keep the global `ffmpeg.hwaccel_args: ""` guard in place and add hwaccel only to proven cameras.
- On this HA box, do not stop at `motion recording finished` when debugging HomeKit Secure Video. In the March 29, 2026 `South Yard` follow-up, changing `HomeKit -> RTP Sender` to `Scrypted` got Scrypted to finish the motion-recording session, but HomeKit still showed no saved clip. The real clue came from comparing media details against working `Deck`: `South Yard` was the odd `h264/aac` feed with unset/non-monotonic timestamp warnings, while `Deck` was `h264/pcm_s16be`. Enabling both `HomeKit -> Transcode Audio` and `HomeKit -> Transcode Video` on `South Yard` removed those warnings from the live recording path. When only one camera still fails in HomeKit, compare its stream codecs/timestamps against a known-good camera before assuming the trigger path is the problem.
- If one HomeKit camera still refuses to save clips, clone the camera before concluding the stream itself is bad. In the March 29, 2026 `South Yard` follow-up, a fresh `South Yard 2` clone on the same RTSP source isolated the remaining bug to the motion-trigger path: the user only got a saved HomeKit clip once the clone used `OpenCV Motion Detection` instead of the external MQTT/custom-motion sensor. That narrowed the problem to the camera-specific external motion path, not generic HKSV on the stream. Be careful not to overstate blame on Wyze Bridge alone when synthetic MQTT publishes were part of the test; the strongest claim is that the external motion path failed and the video-derived motion path worked.
- On this HA box, a local dev add-on rebuild can fail with `Error: Version changed, use Update instead Rebuild` after the synced source bumps the local add-on's visible version. When that happens, do not keep hammering `ha apps rebuild --force`; sync the source, run `ha store reload`, then use `ha apps update <dev-slug>` before the prod/dev cutover.
- When proving or disproving a Wyze MQTT motion fix for Scrypted/HomeKit, a broker-only fake publish is not enough once the camera has been moved off the MQTT/custom-motion path. For the March 30, 2026 South Yard retest, the only meaningful check was to temporarily restore the actual Scrypted `Custom Motion Sensor` path, attach a known-good MQTT motion helper to `wyzebridge/south-yard/`, and replay the fake `1 -> 2` pulse through that exact wiring. That stricter replay still failed to flip South Yard to motion in Scrypted, so avoid claiming the bridge patch alone fixes the recording issue unless the exact Scrypted attachment path has been re-tested too.
- On this Scrypted build, do not assume a convenient REST `GET /api/devices` surface exists just because `/login` returns a bearer/query token. The reliable live path here was the Management Console itself: sign in through `http://<host>:11080/login`, open the UI with the returned `scryptedToken`, and use the real device pages to inspect, delete, and rewire MQTT helpers.
- The later March 30, 2026 strict South Yard replay changed the conclusion from the earlier handoff: with temporary helper cleanup done, `Garage Wyze Motion` repointed to `wyzebridge/south-yard/`, and `South Yard` reattached to that helper through `Custom Motion Sensor`, the fake broker pulse did reach Scrypted and logged `South Yard motionDetected: true (mixin)`. The missing step was now downstream: no `South Yard` HomeKit recording-start log appeared in the same capture window, so the remaining blocker is not broker publish or helper-to-camera delivery.
- For South Yard specifically, do not trust a positive `motionDetected: true (mixin)` log while `OpenCV Motion Detection` is still enabled. The later exact `Deck`-style replay on March 30, 2026 turned `OpenCV` off, left only `Custom Motion Sensor` active, replayed the same fake MQTT pulse, and the earlier positive signal disappeared. That means the mixed-mode replay was not strong evidence that the external MQTT/custom-motion path had really reached the camera.
- The cleanest March 30, 2026 South Yard replay used a fresh dedicated helper instead of repointing `Garage Wyze Motion`, configured that helper directly through the Scrypted client API, switched `South Yard` mixins from `OpenCV Motion Detection` (`63`) to `Custom Motion Sensor` (`255`), and reselected the new helper (`263`). In that setup, Scrypted logs showed the fresh helper receiving `motion topic payload: 1` and `motion topic payload: 2`, but `South Yard` never emitted its own motion transition and never logged `motion recording starting`. That isolates the remaining break to the helper-to-camera custom-motion handoff, not broker publish.
- The missing step for South Yard's custom-motion path was a Scrypted restart after selecting the replacement motion helper. In the final March 30, 2026 replay, a new helper (`264`) was created and attached to `South Yard` with `OpenCV Motion Detection` disabled. Before restarting Scrypted, the helper received the MQTT payloads but the camera/log path stayed inconsistent. After restarting Scrypted and replaying immediately, direct state polling showed `helper.motionDetected` and `south.motionDetected` both flip `false -> true -> false`, and Scrypted logged `motion recording starting` plus `motion recording finished`. If `Custom Motion Sensor` changes seem ignored on this host, restart Scrypted after selecting the helper before declaring the path broken.
- On the March 30, 2026 HA live integration repair, the custom `scrypted` integration failed on HA `2026.3.4` because it still called `async_register_built_in_panel(...)` with the old positional argument order. The safe fix was to switch to keyword arguments and use `frontend_url_path=f"scrypted_{config_entry.entry_id}"` instead of passing a config dict in the old positional slot.
- The same March 30, 2026 Scrypted repair exposed a second custom-component bug: storing `hass.data[DOMAIN]` by bearer token is unsafe when duplicate config entries point at the same Scrypted server, because both entries can receive the same token and overwrite each other. Key runtime state by `config_entry.entry_id`, store the token inside that state, and update the proxy view plus token sensor to read that shape.
- On this HA box, the clean March 30, 2026 fix for unhealthy `GOVEE Bluetooth Lights` was not "fight BLE harder." The custom `govee_light_ble` H5179 entries were failing on Bluetooth connection-slot exhaustion while the existing `Govee2MQTT` path was already publishing working MQTT thermometer entities for the same devices. Disable the redundant BLE config entries instead of leaving both paths active.
- On the same March 30, 2026 repair, the unhealthy `Frigate` integration was caused by a stale host in `/config/.storage/core.config_entries`: the custom integration still targeted `http://ccab4aaf-frigate-beta:5000` even though the live add-on slug/hostname was `ccab4aaf_frigate` / `ccab4aaf-frigate`. Fix the stored URL, then validate against fresh post-restart logs so you do not confuse old log noise with the repaired state.
- When the Web UI looks like raw HTML instead of the normal Bulma layout, check the rendered CSS/JS asset URLs before chasing template logic. On this branch, the root cause was frontend templates still using Flask's default `url_for`, which dropped the ingress prefix for static assets; explicitly wiring the ingress-aware helper into Jinja and binding `static/` plus `templates/` paths in each runtime fixed the issue cleanly.
- For the native `go2rtc` sidecar, `/api/wyze` alone is too thin to mirror the bridge's real per-camera feed state. Upstream only returns helper sources with `name`, `info`, and `url`, so if alias prep must honor disabled cameras or `HD`/`SD` feed toggles, query the bridge's own local `/api/<cam>` and `/api/<cam>/stream-config` endpoints after Flask is up instead of guessing from the helper payload.
- When the native `go2rtc` sidecar depends on the bridge's authenticated `/api` catalog, do not treat `/health` as the readiness signal. Wait until `/api?api=<token>` succeeds first, or the sidecar can race early auth setup, see `401`, and incorrectly fall back to helper-only alias filtering.
- When a Home Assistant feed is intentionally `path: native`, do not let the sidecar alias refresh logic treat it as disabled just because the bridge's live published `/api` catalog no longer contains a matching bridge stream. Honor the `stream-config` feed flags for native-only feeds or validated aliases like `hamster-sd` will be dropped.
- When a camera's only enabled feed is native-only, do not build `/api` and the Web UI solely from `StreamManager`. Native-only cameras may have no bridge-managed `Stream` object at all, so synthesize those catalog entries from filtered camera metadata plus `stream-config` and publish the selected native RTSP URL there too.
- On the April 1, 2026 Home Assistant OnStar live-box repair, the apparent post-TOTP redirect timeout was a downstream symptom. The real blocker was that the Microsoft MFA OTP value was not persisting in the live input before submit, so no real verification request ever happened. The working local add-on fix forced the OTP into the actual input via native value assignment plus `input`/`change` events, waited past GM's advertised 2000 ms client-side verification delay, and removed aggressive auth/access-denied retry loops that were extending lockouts.
-
## May 04, 2026 - SSL Certificate Verification Failure Debug

**Root cause**: `api.wyzecam.com` serves a certificate issued by `DigiCert TLS RSA SHA256 2020 CA1` but does NOT send the intermediate CA certificate in the TLS handshake. Python's `ssl` module (via `certifi` or system `ca-certificates`) needs to have this intermediate in its trust store, OR the server needs to send it.

The `python:3.13-slim-bookworm` base image does not include the `ca-certificates` apt package in its slim variant. Even after installing `ca-certificates` and pinning `certifi>=2024.12.0`, the Docker build cache on HA Supervisor (`docker buildx build` with layer caching) prevents the changes from taking effect without explicitly passing `--no-cache`.

**Fixes applied to codebase for v4.2.9:**
1. `docker/Dockerfile` & `home_assistant/docker/Dockerfile`: Added `ca-certificates` to the final stage `apt-get install`
2. `app/requirements.txt` & `home_assistant/app/requirements.txt`: Added explicit `certifi>=2024.12.0` dependency
3. `home_assistant/app/build.env`: Bumped VERSION from `4.2.8` to `4.2.9`
4. `home_assistant/app/wyzecam/api.py`: Added global `SSL_VERIFY` env var support (defaults to `True`), wraps `get()`/`post()` to pass `verify=SSL_VERIFY` on every request

**To deploy the fix on the live HA box**, the supervisor's Docker build cache must be cleared. The `ha apps rebuild --force` command does NOT pass `--no-cache` to `docker buildx build`. The reliable path is either:
- Push the changes to GitHub and let CI build a new release (`4.2.9`), then update the add-on normally
- Or SSH into the HA host directly and run `docker buildx build --no-cache ...` from the host namespace

**Temporary workaround**: Set the environment variable `SSL_VERIFY=false` in the running add-on container. On this HA box, the supervisor does not expose an env var passthrough in the add-on config schema, so the only way to inject it at runtime is via Docker exec or a host-level Docker restart with custom env.

- On this HA box, do not trust the first read of `/addon_configs/<slug>/go2rtc_wyze.yaml` immediately after a Wyze Bridge restart. The add-on can report `state=started` before the background native-alias refresh has finished fetching `/api/wyze`, consulting the authenticated bridge `/api/<cam>/stream-config`, and rewriting the file. On the April 2, 2026 production retest, `go2rtc_wyze.yaml` looked empty at first, but a later reread showed the expected `hamster-sd`, `deck-sd`, `garage-sd`, `back-yard-sd`, `south-yard-sd`, and `north-yard` aliases once startup settled.
- On the same April 2, 2026 Frigate retest, once those native aliases were live again, the safe production cutover for record-only Wyze cameras was to repoint Frigate from bridge `rtsp://...:58554/*-sub` inputs to native `rtsp://...:19554/*-sd` aliases. That restored `hamster` immediately and cut the worst per-camera Frigate ffmpeg CPU from about `19-24%` on `deck`/`garage` down to about `4-5%` each, with full-system Frigate CPU dropping from roughly `52-54%` to about `44-45%`.
- On the April 2, 2026 final production Frigate cleanup, a camera can still return `latest.jpg` HTTP `200` briefly even while its live source is broken or freshly restarted. Do not treat snapshot `200` alone as proof of recovery; confirm `camera_fps` and `process_fps` in `/api/stats` too. The decisive proof for `north_yard` was the post-restart `ffmpeg` cmdline using `rtsp://192.168.1.244:19554/north-yard-sd` plus `camera_fps=5.0` and `process_fps=5.0`.
- On the April 3, 2026 live Scrypted audit, the decisive safe verification pattern for Wyze MQTT motion cameras was: keep the current helper bindings in place, replay a short fake `motion 1 -> 2` publish to the real broker topic, and poll both the helper device and the camera device through the Scrypted client. For `Garage`, `Deck`, `North Yard`, and `HAMSTER`, the healthy proof was `helper.motionDetected` and `camera.motionDetected` both flipping `false -> true -> false`, followed by fresh Scrypted `motion recording starting` and `motion recording finished` lines for the same cameras.
- The same April 3, 2026 audit also confirmed the current production split for Wyze HKSV on this box: `South Yard` should stay on `OpenCV Motion Detection` unless there is a specific reason to retest its external MQTT path, while `North Yard` and `HAMSTER` should stay on the repaired native `rtsp://192.168.1.244:19554/*-sd` sources with `FFmpeg (TCP)` parsing and `noAudio=true`. `South Yard`'s current live recording path still shows both HomeKit transcodes active through `homekit:debugMode = ["Transcode Audio", "Transcode Video"]` and fresh `motion recording starting` / `motion recording finished` log lines.
- On the April 14, 2026 Hamster native follow-up, remember that Home Assistant `CAM_OPTIONS` override `/config/wyze_camera_settings.json` for feed enablement. Editing `wyze_camera_settings.json` alone did not move `HAMSTER` off the dead native path because production still injected `HD_HAMSTER=False` and `SD_HAMSTER=True` from Supervisor options, so `camera_feed_config()` kept selecting SD/native. The live fix had to change the Supervisor option entry itself (`HD=true`, `SD=false`, `STREAM=main`) before restarting the add-on.
- The same April 14, 2026 pass also proved the current `local_docker_wyze_bridge_local` lane is not a trustworthy prod-parity validator on this box. Even after newer source trees were synced into `/addons/local/wyze_bridge_local`, `ha apps rebuild local_docker_wyze_bridge_local` still launched a stale `3.12.3-local` image with old MediaMTX listeners on `:8554`, `:8888`, and `:8889`. When the manifest on disk and the live runtime disagree, trust the runtime logs and open listeners, not the synced source tree or `ha apps info` metadata alone.
- In the Home Assistant `whep_proxy`, do not treat `currentUpstream() != nil` as enough to reuse a stream. On the April 14, 2026 HomeKit repair, `deck-sub` and `garage-sub` could stay wedged forever with `upstream_state="new"`, `video_ready=false`, `audio_ready=false`, and `whep_clients=0`, which left Scrypted seeing RTSP `400 Bad Request` while bridge logs showed `503`. Make `/kvs-config/<camera>` `404 camera [x] not found` terminal, and only reuse no-media sessions during a short startup grace window; older no-media sessions must be replaced.
- On the April 4, 2026 `Back Yard` wiring pass, Scrypted accepted a malformed MQTT helper script saved through an inline `node -e` string and left the helper silently nonfunctional even though the helper device, URL, and replacement-motion binding all looked correct. If a newly created MQTT helper does not react after restart, inspect the saved `mqtt.ts` body directly and make sure the string fallback and `'1'` comparison survived escaping exactly.
- The same April 4, 2026 live pass confirmed the safe add-a-new-Wyze-camera pattern on this box: create the MQTT helper first, set its URL to `mqtt://<user>:<pass>@<broker>/wyzebridge/<slug>/`, save the known-good `motion` handler script, add mixin `255` (`Custom Motion Sensor`) to the camera, point `replaceMotionSensor:replaceMotionSensor` at the helper, set `HomeKit -> RTP Sender = Scrypted`, then restart Scrypted before replaying a fake `motion 1 -> 2` publish.
- On the April 12, 2026 Home Assistant connectivity pass, the HA server itself was healthy even though the companion app showed `Unable to connect to Home Assistant`. The decisive split was: LAN `http://192.168.1.244:8123` still served the frontend, Cloudflared was connected, but the public hostname returned a Cloudflare Access login redirect instead of Home Assistant and `/config/.storage/core.config` still advertised the stale DuckDNS URL for both `internal_url` and `external_url`. When this happens, treat the app failure as an access-path mismatch first, not as a dead HA core. The safe live fix on this box was to back up `/config/.storage/core.config`, set `internal_url` to the plain LAN URL and `external_url` to the Cloudflare hostname, then restart Home Assistant Core and verify both the stored config and the served frontend.
- On the April 12, 2026 Frigate recovery, a live `OSError: [Errno 30] Read-only file system: '/media/frigate/recordings'` did not mean the CIFS share itself was read-only. The decisive clue was `ha mounts reload frigate`, where Supervisor logged `Mount frigate not correctly mounted after a reload. Trying a restart`. The host shell could still write to `/media/frigate`, but Frigate only recovered after the Supervisor media mount was reloaded and the add-on was started again. On this box, treat that error as a stale/bad Supervisor mount binding first, not as proof that the Frigate YAML or the share ACLs are wrong.
- On the April 12, 2026 `south_driveway` audit, the proven Frigate pattern on this box remained: high-resolution `record` input, lower-resolution `detect` input, `preset-rtsp-restream`, and camera-scoped `preset-vaapi`. Live stats confirmed the host was keeping up cleanly (`camera_fps=10.1`, `process_fps=10.1`, `skipped_fps=0.0`). The two meaningful tuning levers were not topology but policy: Frigate docs still recommend `detect.fps=5` by default, with `10` as the practical ceiling for faster motion, and the camera’s object thresholds were notably more sensitive than Frigate defaults, which is good for avoiding misses but can increase false positives.
- On the April 12, 2026 driveway follow-up, “Frigate looks low-def” and “the recordings are low-def” were two different questions. The decisive check was to inspect real recording segments, not Frigate’s dashboard or preview media: both `south_driveway` and `north_driveway` recording files under `/media/frigate/recordings/...` were still `2560x1440`. The low-quality look came from the Frigate UI using `latest.webp?height=360` and `/live/jsmpeg/<camera>`, i.e. the detect-stream / low-bandwidth path. On this box, do not treat Frigate’s preview quality as proof that the recording stream has dropped to SD unless you have checked an actual recording file.
- On the April 12, 2026 HD-live experiment for the driveway cameras, two textbook-looking Frigate `go2rtc` approaches both failed live validation on this host. Adding `go2rtc` HD/SD stream definitions plus `live.streams` did expose HD `mse/fmp4` live consumers, but it also caused repeated `No new valid recording segments were created` watchdog resets, starting with `north_driveway`, which points to the upstream restream setup not tolerating the extra live-view connection path. Repointing ffmpeg record/detect inputs to local `rtsp://127.0.0.1:8554/<stream>` go2rtc restreams was worse: all cameras dropped to `process_fps=0` with near-full `skipped_fps`. On this box, keep the stable direct ffmpeg record/detect URLs and treat HD live inside Frigate as not safely supported until the upstream Scrypted/Reolink stream topology is improved.
- On the April 13, 2026 driveway and doorbell reliability pass, simultaneous Frigate crashes on Scrypted-backed RTSP inputs were an upstream rebroadcast problem, not a Frigate config regression. The tell was Frigate `/api/ffprobe` returning `Connection refused` or `400 Bad Request` for the live RTSP URLs that matched the camera config. Restarting the Scrypted add-on cleared the dead listeners, and the safe proof of recovery was: `ffprobe` succeeds on the exact RTSP paths, then `/api/stats` returns about `camera_fps=10` and `process_fps=10` with `skipped_fps=0`. Do not edit Frigate topology first when those upstream probes are failing.
- On the April 3, 2026 Frigate startup-hang recovery, `enabled: false` was not enough to prove camera config was out of play. The add-on still hung in `startup` with the old full config even after every real camera was disabled, but it booted immediately with Frigate's documented minimal shape: `mqtt.enabled: false` and one disabled dummy camera. The safe recovery path was to rebuild the live config from that minimal-good baseline and then add back only the proven front cameras (`south_driveway`, `north_driveway`, `doorbell`) instead of trying to salvage the larger config blob in place.
- On May 3, 2026, the Mac Mini lacked LAN access (no route to 192.168.1.x) when an SSH investigation of HomeKit camera failures was requested, so the diagnosis was done entirely from code. The identified root cause is a `_native_alias_is_ready` race in 4.2.8: `native_selected` requires the go2rtc alias to be populated within a 10-second TTL window, so if the go2rtc sidecar is slow to start or the alias seeding delay exceeds 10 seconds, the bridge API returns a missing/incorrect RTSP URL for native-only cameras (`north-yard`, `hamster-sd`). Scrypted then caches the wrong URL and the camera vanishes from HomeKit. This is a 4.2.9 candidate: remove `_native_alias_is_ready` from the `native_selected` gate (keep only `api_reachable`), and ensure `_camera_catalog_entry` always returns the native RTSP URL for native-path feeds regardless of alias readiness. When SSH access is restored, the first checks should be: (1) `ha apps logs 0eb0428f_docker_wyze_bridge_v4 | grep -E "go2rtc|native|alias"` for sidecar startup state, (2) `nc -z 192.168.1.244 19554` to confirm the go2rtc RTSP port is open, (3) inspect Scrypted's source URL for north-yard and hamster, and (4) check `/addon_configs/0eb0428f_docker_wyze_bridge_v4/go2rtc_wyze.yaml` is not empty.
- On May 6, 2026, the managed Home Assistant `4.2.9` update failed for a very plain reason: `home_assistant/Dockerfile` had been switched to `. app/build.env`, but `home_assistant/app/build.env` was still ignored locally instead of being tracked in git, so the GitHub-backed Supervisor build context did not contain that file and died with `/bin/sh: 1: .: cannot open app/build.env`. The fast live recovery path on this box was to stop the competing bridge lanes, refresh the local prod-twin add-on source under `/addons/local/wyze_bridge/` from the current `home_assistant/` tree, install/start `local_0eb0428f_docker_wyze_bridge_v4`, and copy the production options into that local slot.
- The same May 6, 2026 recovery exposed a second packaging/runtime trap: the HA add-on can answer `/health` only if Flask can import the in-container `wyzebridge` package from `/app`. On this box, the add-on crashed with `ModuleNotFoundError: No module named 'wyzebridge'` until the runtime `run` wrapper exported `PYTHONPATH=/app` before `flask run`. Keep that export in all mirrored runtime entrypoints and cover it with a packaging test.
- The same May 6, 2026 port-recovery follow-up exposed a third packaging trap: the managed GitHub build also needs the production `home_assistant/app/.env` file tracked, not just present locally. Without that hidden file in git, the HA runtime still boots but falls back to default MediaMTX listeners on `:8554`, `:8888`, and `:8889`, leaving the declared host ports `58554`, `58888`, and `58889` closed. Keep `!home_assistant/app/.env` in `.gitignore` and cover it with a packaging test.
- On May 6, 2026, go2rtc returned HTTP 500 "Invalid credentials" from `/api/wyze?id=<email>` after a Wyze API key rotation. The `go2rtc_sidecar.sh` alias prep script treats an empty `/api/wyze` response (due to auth failure) as a signal to exit early without rewriting `/config/go2rtc_wyze.yaml`, so stale credentials in the on-disk config persisted silently across every bridge restart. Meanwhile Scrypted kept requesting `rtsp://...:19554/north-yard-sd` (which was never registered) and crashed in a restart loop. The fix was to manually write the current `api_id`/`api_key` from Supervisor options directly into `/addon_configs/0eb0428f_docker_wyze_bridge_v4/go2rtc_wyze.yaml` and restart the add-on. On restart, the sidecar successfully authenticated, fetched the camera list, and auto-registered all native aliases (including `north-yard-sd`). Prevention: when rotating Wyze API keys, always restart the Wyze Bridge add-on immediately afterward so the sidecar can refresh `go2rtc_wyze.yaml` with the new credentials; do not rely on the on-disk file being updated automatically.
- On May 8, 2026, HA SSH worked as `root@192.168.1.244` with `~/.ssh/id_homeassistant` and `IdentitiesOnly=yes`; plain `root`/default-agent auth failed because the key was not loaded. The same Frigate check showed `north_driveway` at `camera_fps=0` while `south_driveway` stayed healthy, with Frigate refusing `rtsp://192.168.1.244:44095/{a3ea1612e8a0a713,ac3d9badc30004af}` and Scrypted logging `North Driveway E1 Pro` `Error starting RTSP Rebroadcast Server` / `EADDRINUSE` on port `44095`. A Home Assistant/Core restart alone did not fix it, but `ha addons restart 09e60fb6_scrypted` restored `north_driveway`, `south_driveway`, and `doorbell` to about 10 FPS with `skipped_fps=0`. To reduce recurrence risk without Scrypted console credentials, `/config/configuration.yaml` now defines REST sensors from Frigate `http://ccab4aaf-frigate:5000/api/stats` and Wyze Bridge `http://0eb0428f-docker-wyze-bridge-v4:5000/api`, and `/config/automations.yaml` has grouped `camera_bridge_group_watchdog`, which restarts Scrypted if any Frigate camera stays below `0.1` FPS for 3 minutes while at least one Frigate sibling is above `5` FPS, and restarts Docker Wyze Bridge if any enabled bridge camera is disconnected for 10 minutes while at least one enabled sibling is connected. The Wyze branch must use `/api` rather than `/api/status` so enabled catalog cameras like `north-yard` are covered even when they are missing from the status stream list. Treat this as a Scrypted rebroadcast port/listener conflict first, not a Wyze `North Yard` TUTK issue.

[2026-05-17] - Dev lane WHEP and MediaMTX port isolation
Oops: The dev add-on looked healthy at `/health`, but WHEP first collided on its listener port, then kept refreshing camera config from the production-shaped `127.0.0.1:5000` path.
Why: The swap helper had separate dev ports, but the Go WHEP proxy did not read the dev bridge web port for `/kvs-config`, and the old dev MediaMTX range still collided on this HA host.
Fix: Made `WHEP_PROXY_PORT` and `KVS_CONFIG_PORT` real dev-lane settings, taught the WHEP proxy to fall back to `WB_APP_PORT`, and moved dev MediaMTX to the isolated `28xxx/29xxx` validation range.
Next time: After a dev swap, check `/health/details` plus the WHEP/MediaMTX logs for bind errors and wrong-port refreshes before starting any soak.

[2026-05-17] - WHEP status can be alive while media is dead
Oops: A WHEP stream could stay listed with `upstream_state="new"`, `video_ready=false`, and `audio_ready=false`, making `/health` look broadly okay while the camera had no usable media.
Why: The proxy only replaced never-media streams when reuse was checked, and reconnect loops had no no-video attempt threshold. Audio readiness also meant "track allocated", which could leak an audio-only path.
Fix: Added no-video reconnect attempt tracking, a periodic stale stream reaper, `/kvs-config` recreation, fragmented STAP-A SPS/PPS buffering, IDR replay failure reconnects, and audio gating that waits for real audio packets plus video readiness.
Next time: Check `/health/details` for `video_ready`, `audio_packets_seen`, and `has_ever_had_media`; a plain listener or `/status` 200 is not proof that WHEP is useful.

[2026-05-17] - South Yard WHEP wake still does not prove media
Oops: South Yard could be moved from the stale `south-yard-sub` WHEP path to the main `south-yard` path and still never produce WHEP media.
Why: `HL_BC` was not being woken before WebRTC setup, but even after adding that wake step the live camera kept failing ICE/no-media and MediaMTX continued to see `503` until the WHEP proxy replaced the stale stream.
Fix: Keep `HL_BC` on main-path SD quality, wake it before requesting WebRTC config, and rely on the WHEP no-media reaper to tear down the bad session instead of wedging forever.
Next time: Treat South Yard WHEP as red unless `/health/details?stream=south-yard` shows `video_ready=true` and `audio_packets_seen>0`; logs showing `Waking KVS camera South Yard` only prove the request path, not usable video.

[2026-05-17] - Snapshot freshness needs content hashes
Oops: Snapshot freshness checks were split across native go2rtc, RTSP, async RTSP, cloud thumbnails, and the HA watchdog, with some paths still able to treat touched or identical files as fresh enough.
Why: The bridge had payload validation, but freshness was not represented as a durable per-camera content fact. mtime and HTTP success could still move independently from actual image content.
Fix: Added a SHA-256 snapshot registry, recorded hashes only after validated replacements, exposed `/api/snapshot-hashes`, and made the HA watchdog watch hash movement.
Next time: When proving snapshots fresh, compare content hashes and visible timestamps; do not trust mtime, file size, or HTTP 200 alone.

[2026-05-17] - go2rtc 503 should not stall snapshot fallback
Oops: A go2rtc native frame `503` could keep the snapshot path waiting for the native timeout even though `503` only means no frame is warmed yet.
Why: Alias registration and frame warmth were being treated like the same state. The sidecar wrote aliases, but it did not proactively warm every alias or maintain that warmth.
Fix: Preload every native alias after sidecar alias refresh, poll producer readiness every 2 seconds for 10 seconds, keep a 60-second preload refresh loop, and make native snapshot `503` return immediately so RTSP can take over.
Next time: Treat `go2rtc /api/streams` producers as the warmth proof; an alias in YAML is only wiring, not a ready frame.

[2026-05-17] - SD_ONLY needs a hard HD stop
Oops: SD-only mode could not be treated as just another camera preference because HD controls, API changes, and stream creation could drift apart.
Why: A global SD-only goal needs every layer to agree: bridge capability, saved feed config, runtime stream setup, add-on options, and the Web UI.
Fix: Added a global `SD_ONLY` option that reports HD unsupported, rejects HD enable requests, creates only one SD stream per camera, and hides HD controls in the UI.
Next time: Prove SD-only twice: first with local tests, then with live HA config and the generated go2rtc aliases showing no HD path.

[2026-05-17] - Three runtime trees need a build check
Oops: Fixes kept landing in `app/`, `home_assistant/app/`, and `.ha_live_addon/app/` as hand-mirrored copies, so useful dev-only camera fixes and production packaging fixes drifted apart.
Why: There was no repeatable source-of-truth check. The dev tree could carry camera code that the canonical app did not, while production and dev still looked similar enough to fool quick reviews.
Fix: Made `app/` the canonical Python/web source, moved runtime-only differences into `runtime_overlays/`, added `scripts/build.sh --check`, and taught the dev deploy helper to build the dev tree before syncing.
Next time: Run `./scripts/build.sh --check` before any HA deploy; if it fails, fix the canonical source or overlay instead of editing a runtime tree directly.

[2026-05-17] - Live API proof should use header auth
Oops: A live dev readiness check used `?api=<key>` because that is how the go2rtc sidecar calls the bridge, and the add-on logs recorded the full URL.
Why: Query-string API auth is accepted by the bridge, but Flask access logs include the query string. Header auth is also supported and keeps the key out of normal request logs.
Fix: Use `api: <key>` headers for manual `/api`, `/api/ready`, and `/api/snapshot-hashes` probes, and redact any older query-string log lines before saving proof.
Next time: Never save or paste raw bridge API probe URLs from live logs; summarize camera counts and readiness status instead.

[2026-05-17] - Startup readiness can fail after the catalog looks good
Oops: The overlay-built dev add-on returned a stable six-camera `/api` catalog, but the 30-sample startup soak still had 14 `/api/ready` errors.
Why: Catalog population and readiness endpoint responsiveness are separate proof points. A later `ready` response does not erase readiness errors during the soak window.
Fix: Recorded the failed soak in `tmp/phase2_startup_soak_proof_20260517.md` and kept Phase 2 red.
Next time: For Phase 2, poll `/api` and `/api/ready` together and treat timeouts/errors as failures even when the catalog itself is non-empty.

[2026-05-18] - Sidecar readiness was waiting on itself
Oops: The go2rtc sidecar waited for `/api/ready` before refreshing native aliases, while `/api/ready` required those aliases to already be seeded.
Why: Readiness and alias seeding were coupled in a circle. After that was fixed, `/api` still timed out because normal catalog responses were doing slow go2rtc diagnostics on demand.
Fix: Made the sidecar wait for a populated authenticated `/api` catalog, moved sidecar bridge calls to header auth, made `/api/ready` fail soft, cached positive go2rtc API reachability, and reduced alias-ready checks to fast diagnostics.
Next time: For startup bugs, check for circular readiness dependencies first, then separately measure whether the public catalog endpoint is fast enough during the soak window.

[2026-05-18] - SD_ONLY live proof needs the bridge key, not the Wyze key
Oops: The first live SD_ONLY API probe used the add-on option named `API_KEY`, and the bridge correctly returned `401`.
Why: That option is the Wyze cloud API key. The bridge Web UI/API auth key is `WB_API`, or a generated 40-character key derived from the configured Wyze email when no stored `wb_api` file exists.
Fix: Read the dev add-on options privately through Supervisor, derive/use the bridge API key only in memory, send it as the `api` header, and keep proof artifacts to sanitized counts and booleans.
Next time: For live bridge `/api` checks, verify which key family is being used before blaming auth or readiness.

[2026-05-18] - Frigate recording can fail while preview FPS is green
Oops: Frigate looked partly healthy because the three active cameras still showed about 10 FPS and latest snapshots returned real images, but the logs were repeating `No new recording segments were created` and restarting record ffmpeg.
Why: Frigate detect/preview and record are separate paths. The live config uses Scrypted RTSP rebroadcast URLs for both roles; record workers can fail or loop while the latest image endpoint still looks fine. Restarting Scrypted briefly makes those RTSP ports return `Connection refused` or `400 Bad Request` until rebroadcast listeners rebuild.
Fix: Restored production Wyze Bridge, restarted Scrypted, verified all six Frigate RTSP inputs through `/api/ffprobe?paths=<rtsp-url>`, then waited for a fresh clean log window with no watchdog/record/input errors and FPS still near 10 with `skipped_fps=0`.
Next time: For Frigate recovery, prove three things together: configured camera FPS, RTSP input `ffprobe`, and a fresh post-restart log window without recording watchdog errors.

[2026-05-18] - Production MediaMTX can be blocked by a hidden host port holder
Oops: After dev-lane validation, the real production add-on came back with `wyze_authed=true` but `mtx_alive=false`, and repeated restarts kept logging `listen tcp :58888: bind: address already in use`.
Why: There was more than one Wyze Bridge slot on the HA host. A duplicate local production-style slot (`local_docker_wyze_bridge_v4`) could show `error`, while the real production add-on still could not bind the shared host-network HLS port. Syncing source into `/addons/wyze_bridge_v4lab` also did not prove the installed repository-backed add-on rebuilt from that source.
Fix: Stopped every visible Wyze bridge add-on and restarted only `0eb0428f_docker_wyze_bridge_v4`; Frigate stayed healthy, but MediaMTX still hit the `:58888` conflict, so the honest next step is host-level cleanup or a Home Assistant host reboot with user approval.
Next time: List all Wyze add-ons before prod/dev handoff, and if `:58888` stays busy after every visible slot is stopped, stop editing bridge code and escalate to host-level port cleanup evidence.

[2026-05-18] - WHEP tests need a canonical repo-root module
Oops: The master gate `go test ./whep_proxy/... -v -count=1` failed from the repo root even though the same tests passed inside `home_assistant/whep_proxy`.
Why: WHEP lived only inside generated/runtime trees and overlays, so Go could not resolve the requested root package path. The duplicated overlay copies also weakened the three-tree source-of-truth rule.
Fix: Promoted `whep_proxy/` to the canonical Go module, added `go.work`, taught `scripts/build.sh` to package it into both HA runtimes, and removed WHEP files from runtime overlays.
Next time: If a goal names a command, run that exact command early; passing an equivalent subdirectory command is useful but not enough proof.

[2026-05-18] - Status must show duplicate bridge slots
Oops: `ha_dev_build.sh status` showed only the configured production/dev pair, so a duplicate local production-style Wyze Bridge slot stayed easy to miss during live handoff.
Why: The helper optimized for the happy prod/dev swap and did not expose other bridge-like add-ons that can still join host networking or hold shared ports.
Fix: Extended `status` to list all bridge-like add-ons with slug, name, state, repository, and version.
Next time: Run the helper status before any live cutover and look for extra local bridge slots, even when they are in `error`.

[2026-05-18] - Blocker evidence should be one safe command
Oops: Refreshing the production MediaMTX `:58888` blocker required several hand-copied HA SSH probes, which made it easy to miss one or accidentally run a broader command than needed.
Why: The project had stream diagnostics and swap helpers, but no single read-only production blocker probe that collected health, add-on metadata, redacted logs, host port visibility, host logs, and Frigate FPS together.
Fix: Added `scripts/ha_bridge_doctor.sh`, a read-only doctor command that gathers the blocker evidence without stopping, starting, rebuilding, rebooting, or printing raw secret option values.
Next time: Use the doctor output as the first live evidence artifact before deciding whether a recovery path needs explicit approval.

[2026-05-18] - Passing tests should not return booleans
Oops: The full suite was green but pytest printed three warnings because `tests/test_fixes.py` test functions returned `True` or `False`.
Why: Those tests were written like a standalone demo script, but pytest treats returned values from tests as a warning-worthy smell.
Fix: Replaced return-value success checks with normal assertions and removed unreachable dead code in `test_pid_check`.
Next time: Keep demo-style logging if it helps, but make pytest success/failure come from assertions.

[2026-05-18] - Read-only live doctors still need guardrails
Oops: The new HA bridge doctor was read-only by intent, but it accepted slug and log-line environment values that were later interpolated into remote shell commands.
Why: Even diagnostic helpers can become risky when they combine environment overrides with SSH command strings.
Fix: Added slug and positive-integer validation before SSH, plus tests that reject unsafe env values, forbid stop/start/rebuild/reboot command patterns, and require redacted/log-key-only output.
Next time: Treat every HA SSH helper as live-system code, even if it only reads state.

[2026-05-18] - Local master gates should be one command
Oops: Local proof kept being rerun as a pile of separate commands, which made the audit easy to drift whenever a new safety test or gate was added.
Why: The master goal mixes phase-specific tests, overlay checks, Go tests, and full-suite proof. Without a wrapper, the current green surface lived mostly in terminal history.
Fix: Added `scripts/run_master_local_gates.sh` and tests that keep it local-only/non-disruptive while checking the required non-live gates.
Next time: Run the wrapper for local proof, then run the HA doctor separately for read-only live state.

[2026-05-18] - Audit files need a verifier too
Oops: The audit claimed a Phase 1 browser screenshot existed, but the expected file was missing from the proof folder.
Why: The audit was written from prior run memory instead of a script checking the artifact paths.
Fix: Added `scripts/master_goal_status.py` to read proof artifacts directly, then regenerated the missing Phase 1 proof screenshot with `agent-browser`.
Next time: Let the verifier check proof paths before calling a phase green, especially for visual artifacts.

[2026-05-18] - Recovery needs a pass/fail gate, not a vibes check
Oops: The next production recovery step had a written checklist, but the proof after that step would still depend on remembering several separate HA SSH probes.
Why: The doctor explains the current blocker, but it intentionally does not decide whether production has recovered enough to resume the remaining live WHEP and rebuild gates.
Fix: Added `scripts/ha_prod_recovery_verify.sh`, a read-only post-recovery verifier that fails on unhealthy bridge health, recent `:58888` bind errors, catalog/alias startup errors, obvious WHEP no-video/audio-only symptoms, or unhealthy Frigate FPS.
Next time: After any approved live recovery action, run the pass/fail verifier first, then start the longer Phase 4/5 live proof only if it passes.

[2026-05-18] - Phase 4 soak needs named streams
Oops: The master goal required a one-hour WHEP live soak, but the repo did not have a repeatable way to run it without hand-picking curl probes from memory.
Why: A broad bridge health check is not enough WHEP proof. The soak has to prove each intended stream has WHEP proxy reachability, video readiness, no stuck `upstream_state="new"`, sane audio packet state, and healthy Frigate FPS over time.
Fix: Added `scripts/ha_phase4_whep_soak.sh`, a read-only one-hour soak runner that requires explicit `HA_WHEP_SOAK_STREAMS` and fails loudly on the current production blocker before any real soak can be claimed.
Next time: Do not start Phase 4 live proof until production recovery passes, then run the soak with the exact WHEP stream list being certified.

[2026-05-18] - Production startup proof needs its own read-only soak
Oops: Phase 2 had a green dev-lane startup soak, but production still had no post-recovery command ready to prove `/api` and `/api/ready` stayed healthy after the disruptive recovery step.
Why: The production bridge API is authenticated, and using query-string auth for quick probes can leak the bridge key into logs. Waiting until after recovery to improvise that proof would invite mistakes.
Fix: Added `scripts/ha_phase2_prod_startup_soak.sh`, which derives the bridge API token internally, sends it only as the `api` header, polls `/api` and `/api/ready`, checks catalog/native URL stability, and scans startup logs without changing the host.
Next time: For any approved live restart/reboot gate, build the read-only post-action verifier first and keep secret handling covered by static tests.

[2026-05-18] - Phase 5 needs a production identity/API verifier
Oops: The overlay build was locally green, but there was no single proof command for the required post-rebuild production identity and API checks.
Why: A local `build.sh --check` proves the generated trees match the canonical source, but it does not prove the running Home Assistant add-on is actually the rebuilt production image or that its API, health, and logs are clean.
Fix: Added `scripts/ha_phase5_prod_overlay_api_verify.sh`, wired it into the local gate/status tests, and saved a pre-recovery failed proof while production still had `mtx_alive=false` and the `:58888` bind conflict.
Next time: After production recovery and approved rebuild, run the Phase 5 verifier before claiming the overlay-built production gate is done.

[2026-05-19] - Doctor files are not blockers by themselves
Oops: The master status helper could report `overall: blocked` just because a doctor artifact existed, even if a future doctor run showed healthy production state.
Why: The blocker detector added the latest doctor path to the same hit list as real failure symptoms, then treated any hit as a block.
Fix: Require both concrete failure symptoms for the production blocker: `/health` must show `mtx_alive=false`, and logs must show `listen tcp :58888: bind: address already in use`. Keep the doctor path as context only.
Next time: When a verifier records artifact paths alongside failure signals, test the healthy-artifact case too so stale context cannot pin the goal red forever.

[2026-05-19] - Completion needs explicit production PASS artifacts
Oops: The master status helper could stay honest while blocked, but it had no real future complete model for the full e2e goal.
Why: Dev-lane proof, pre-recovery failures, and local gates were tracked, but the script did not require named production/live PASS artifacts for every phase before `--strict` could ever become green.
Fix: Added explicit complete conditions for Phase 1 production snapshot soak, Phase 2 production startup soak, Phase 3 production SD_ONLY plus dead-branch audit, Phase 4 live WHEP soak plus wedge injection, and Phase 5 recovery plus overlay/API verification.
Next time: Any goal status helper should test both sides: dev-only proof stays incomplete, and a synthetic full production proof bundle makes strict mode pass.

[2026-05-19] - `/api/ready` needs body proof, not just HTTP 200
Oops: Production returned HTTP 200 for `/api/ready`, but the body was `{"error":"Could not find camera [ready]"}` instead of a real readiness payload.
Why: The running production add-on did not expose the overlay-built `/api/ready` route, so Flask handled it like a camera-specific API path while still returning 200.
Fix: Tightened `scripts/ha_phase2_prod_startup_soak.sh` to fail unless every `/api/ready` sample reports `status=ready`, and saved a fresh failed production soak showing `ready_not_ready_samples=14`.
Next time: For readiness endpoints, verify both status code and JSON semantics before calling a startup soak green.

[2026-05-19] - Guard `/api/ready` against route fallback regressions
Oops: The live production failure shape was easy to miss locally because the old verifier only counted the HTTP status and not the exact JSON contract.
Why: A missing `/api/ready` route can fall through to `/api/<camera>` and return a camera lookup error for camera name `ready`.
Fix: Added a frontend regression that `/api/ready` never returns `Could not find camera [ready]` and must return a readiness status shape.
Next time: When adding special API subpaths near catch-all routes, test the special path against the exact catch-all error text.

[2026-05-19] - Readiness blockers need a named failure marker
Oops: The production Phase 2 and Phase 5 artifacts said `/api/ready` was not semantically ready, but they did not clearly name the catch-all route fallback in their summaries.
Why: The endpoint was returning HTTP 200, and without a safe marker the artifact made the failure look like a generic readiness delay instead of old route code still running in production.
Fix: Added `ready_marker=camera_lookup_fallback` and `ready_camera_lookup_error_samples` to the read-only Phase 2/5 verifiers, then captured fresh failed artifacts showing 14/14 Phase 2 samples hit the fallback.
Next time: For live proof artifacts, name the smallest safe failure class in the summary so a later agent does not need raw response bodies or secrets to understand the blocker.

[2026-05-19] - Master status should repeat the sharpest blocker clue
Oops: The fresh Phase 2 and Phase 5 proof artifacts named the `/api/ready` route fallback, but `scripts/master_goal_status.py` still summarized them as generic production failures.
Why: The status helper only checked PASS/FAIL markers, so the most actionable non-secret clue stayed buried in the artifact.
Fix: Added a tiny parser that lifts `ready_camera_lookup_error_samples`, `ready_marker=camera_lookup_fallback`, and the camera-lookup fallback line into the phase evidence, then refreshed the live failed artifacts.
Next time: When a live verifier learns a precise failure label, bubble that label into the top-level goal status too.

[2026-05-19] - Duplicate add-on backup folders can poison Supervisor indexing
Oops: Backup copies of `/addons/wyze_bridge_v4lab` were left under `/addons`, each with the same `slug: docker_wyze_bridge_v4`, while trying to promote the local production source.
Why: Supervisor scans add-on manifests, so duplicate stale manifests can make version and update behavior point at an older source or fail with misleading rebuild errors.
Fix: Moved the backup folders out of the scanned add-on tree and restored the active source metadata so Home Assistant no longer advertises a failing update.
Next time: Put source backups under `/tmp` or another non-scanned path, and verify `ha apps info` version/update fields after every source move.

[2026-05-19] - North Yard freshness was a camera/LAN-path blocker, not just a bridge cache bug
Oops: The production Phase 1 wrapper initially appended a PASS line even though the aggregate showed North Yard had one unchanged hash across all 61 samples.
Why: The wrapper did not stop on the soak script's failing aggregate, and the follow-up LAN override attempt only proved the saved option path, not a working camera frame.
Fix: Wrote a corrected FAIL artifact, tested go2rtc frame endpoints for both `north-yard` and `north-yard-sd`, restored the guessed config change, and kept production healthy.
Next time: Treat `200` with `0B`, unchanged hashes, and absent camera MAC in the LAN table as a hard red gate until a changing JPEG or visible timestamp proves freshness.

[2026-05-19] - Proof scripts must follow the active production slot
Oops: The bridge health endpoint was green through the promoted local production add-on, but several proof scripts still defaulted their Supervisor/log checks to the old `0eb0428f_docker_wyze_bridge_v4` slot, which was in `error`.
Why: Overlay promotion changed the live serving slot to `local_docker_wyze_bridge_v4`, while the verifier defaults still reflected the older repository-backed production slug.
Fix: Defaulted the doctor, recovery verifier, Phase 2 soak, Phase 4 soak, and Phase 5 verifier to `local_docker_wyze_bridge_v4`, while keeping `HA_PROD_ADDON_SLUG` override support.
Next time: After any production-slot promotion, re-run `ha_dev_build.sh status` and make the proof tooling default to the slot that is actually serving `/health`.

[2026-05-19] - Phase 4 can fail on Frigate even when WHEP is green
Oops: A fresh Phase 4 preflight showed all five WHEP streams connected with video and audio packets, but the full soak immediately failed on a tiny Frigate skipped-FPS blip.
Why: The Phase 4 gate is stricter than WHEP-only health. Frigate must also report positive camera/process FPS and `skipped_fps=0`; a five-minute monitor later caught `north_driveway skipped_fps=0.1` for three samples.
Fix: Saved the Frigate monitor artifact and taught `master_goal_status.py` to surface that strict-FPS monitor as Phase 4 blocker evidence.
Next time: When Phase 4 fails, split the diagnosis into WHEP stream health and Frigate strict-FPS health before changing bridge code.

[2026-05-19] - North Yard needs LAN recovery, not another cache refresh
Oops: The bridge catalog still reported North Yard as connected, which made it tempting to keep poking the snapshot route as if the cache were the only stale piece.
Why: Authenticated probes showed `/snapshot/north-yard.jpg` timed out, `/img/north-yard.jpg` returned one unchanged Wyze API cache hash, and HA could not reach either the configured camera IP `192.168.1.183` or the override IP `192.168.1.185`.
Fix: Saved authenticated snapshot and LAN sweep artifacts, and taught `master_goal_status.py` to surface the North Yard timeout/cache/LAN clues directly in Phase 1 evidence.
Next time: If North Yard is red, prove a non-empty changing LAN/go2rtc frame or visible timestamp before rerunning the 30-minute production soak.

[2026-05-19] - Frigate blips point toward Scrypted rebroadcast stability
Oops: The Phase 4 WHEP stream checks looked green, but Frigate still tripped the strict skipped-FPS gate.
Why: Current Frigate stats can recover quickly, while recent Frigate logs still showed Scrypted RTSP rebroadcast URLs for `north_driveway` returning `400 Bad Request` and forcing ffmpeg restarts.
Fix: Saved a focused Frigate north-driveway diagnostic artifact with the current config, current stats, and recent watchdog log lines.
Next time: For Phase 4 skipped-FPS failures, inspect Frigate logs and Scrypted rebroadcast URLs first; a green WHEP status does not prove Frigate's RTSP inputs stayed clean.

[2026-05-19] - Current proof should replace older blocker artifacts
Oops: The top-level goal status still pointed at older North Yard blocker artifacts even after a newer reprobe proved the same failure shape more directly.
Why: `scripts/master_goal_status.py` only recognized the earlier `north_yard_authed_snapshot_reprobe_*` wording and did not parse the newer route/hash/IP format.
Fix: Taught the status helper to read `north_yard_current_reprobe_*` artifacts, including timed-out `/snapshot`, unchanged `/img` hash, `wyze-api` registry source, and unreachable configured/override IPs.
Next time: When a new live proof artifact uses a different format, update the status parser and a small regression test immediately so the dashboard follows the freshest evidence.

[2026-05-19] - WHEP soak can fail before the final result line
Oops: The Phase 4 soak script continues collecting samples after the first strict Frigate failure, so a running artifact can be decisively red before it contains the final `FAIL: Phase 4 WHEP soak failed.` line.
Why: A single `skipped_fps != 0` sample already invalidates the one-hour zero-blip gate, but the status helper only looked for the wrapper's final result marker.
Fix: Taught `scripts/master_goal_status.py` to treat the in-soak strict Frigate failure marker as a live WHEP soak failure too, then covered that path with `tests/test_master_goal_status.py`.
Next time: Long-running proof scripts should either fail fast on irreversible gate failures or status readers should recognize the earliest decisive failure marker.

[2026-05-19] - Frigate skipped-FPS follow-up needs exact input proof
Oops: After the Phase 4 soak caught `south_driveway skipped_fps=0.1`, the next diagnosis would have required hand-copying Frigate config, ffprobe URLs, and Scrypted log commands again.
Why: The existing doctor and soak scripts show FPS health, but they do not prove whether the exact Frigate RTSP record/detect inputs are currently accepted by Scrypted.
Fix: Added `scripts/ha_frigate_input_diag.sh`, a read-only helper that requires explicit camera names, prints current FPS, configured input paths, ffprobe results, Frigate log clues, Scrypted RTSP log clues, and sanitized add-on state. The fresh South Driveway artifact showed both RTSP inputs ffprobe cleanly after the intermittent blip.
Next time: When the strict Frigate gate blips, run the input diagnostic immediately for the named camera before considering any Scrypted restart or topology change.

[2026-05-19] - North Yard live proof needs a repeatable probe
Oops: North Yard blocker refreshes were still being assembled from several manual HA SSH snippets.
Why: The failure crosses bridge API state, snapshot cache hashes, go2rtc frame routes, and camera LAN reachability, so checking just one piece can make a dead live path look like a cache problem.
Fix: Added `scripts/ha_north_yard_live_probe.sh`, a read-only helper that derives bridge auth privately, uses the `api` header, samples authenticated snapshot routes, checks the snapshot-hash registry, probes go2rtc frames, and tests the configured/override LAN IPs. The fresh artifact showed connected catalog state but timed-out live snapshots, one unchanged Wyze API cache hash, empty go2rtc frames, and unreachable LAN IPs.
Next time: Before rerunning the long Phase 1 production soak, run the North Yard live probe and require a non-empty changing go2rtc frame or visible fresh timestamp.

[2026-05-19] - Remaining-work text must not erase passed proof
Oops: The Phase 4 status evidence listed the WHEP wedge injection proof as passed, but the remaining-work sentence still said the injected live wedge proof was missing.
Why: `scripts/master_goal_status.py` used one generic remaining sentence whenever Phase 4 was not complete, instead of splitting the live soak, wedge proof, and Go gate conditions.
Fix: Built the Phase 4 remaining sentence from the exact missing or failing proof pieces, and added a regression that a passed wedge proof is not reported as missing when only the live soak is red.
Next time: When a status dashboard has separate evidence items, generate the remaining-work text from the same booleans so it cannot contradict itself.

[2026-05-19] - Phase 4 repeated as intermittent Frigate, not WHEP media
Oops: A fresh required Phase 4 soak was started after current South Driveway input proof was clean, but it still failed within five minutes on a different camera: `north_driveway skipped_fps=0.2`.
Why: The WHEP streams stayed connected with `video_ready=true` and increasing audio packets through the failure. The strict gate failed on Frigate's skipped-FPS sample, and the immediate North Driveway input diagnostic recovered cleanly with both Scrypted RTSP paths ffprobeable.
Fix: Stopped the already-failed long soak, saved `tmp/phase4_whep_soak_20260519_152933.txt`, and captured `tmp/frigate_input_diag_north_driveway_20260519_153516.txt` to prove the current recovered state and exact RTSP inputs.
Next time: When the one-hour Phase 4 soak fails on skipped FPS, do not change WHEP code unless WHEP health also goes red; gather per-camera Scrypted/Frigate input proof and treat repeated skipped-FPS blips as a Frigate/Scrypted input stability blocker.

[2026-05-19] - Phase 4 status must preserve the failing sample row
Oops: The first parser for the newest Phase 4 artifact named `north_driveway`, but it reported the later recovered row with `skipped_fps=0.0` instead of the failing row with `skipped_fps=0.2`.
Why: The status helper built a global camera stats map across the whole artifact, so later samples overwrote the stats from the failing sample block.
Fix: Parse the stats rows immediately above each `Unhealthy Frigate cameras:` block and attach those values to the status evidence.
Next time: For long soak artifacts, parse evidence within the sample block that triggered the failure; never let later recovery samples overwrite the failure's proof.

[2026-05-19] - Phase 4 status needs both failure and recovery clues
Oops: The dashboard showed the exact skipped-FPS failure, but it did not show the immediate focused input diagnostic that proved Frigate/Scrypted recovered cleanly afterward.
Why: The status helper only looked at the WHEP soak and strict FPS monitor artifacts, not the newer `frigate_input_diag_*` files.
Fix: Surface clean current Frigate stats, ffprobe return_code=0 counts, and accepted Scrypted RTSP playback from the latest input diagnostic when the WHEP soak is red.
Next time: When a live gate fails on an intermittent subsystem, include the sharp failure clue and the follow-up recovery clue so the next action targets the right component.

[2026-05-19] - WHEP stream blips can invalidate a stopped soak
Oops: The formal Phase 4 soak was stopped after the decisive red sample, so it did not contain the final `FAIL: Phase 4 WHEP soak failed.` line.
Why: The zero-wedge gate is already failed when any sample shows a named stream with `upstream_state="new"` and `video_ready=false`, even if the stream recovers on the next sample.
Fix: Teach `scripts/master_goal_status.py` to recognize in-soak WHEP stream failure markers and surface the exact stream/message, such as `deck-sub WHEP upstream_state must not stay new`.
Next time: Long-running proof readers should understand decisive mid-run failures, not only final summary lines.

[2026-05-19] - Newer North Yard probes supersede older LAN sweeps
Oops: The status dashboard combined a fresh North Yard probe where `192.168.1.183` was reachable with an older LAN sweep saying the same IP was unreachable.
Why: The Phase 1 status helper always appended the latest separate LAN sweep, even when the newer `north_yard_current_reprobe_*` artifact already included a LAN Reachability section.
Fix: When the current reprobe has its own LAN section, use it as the authoritative LAN evidence and ignore older LAN-only sweeps. Also surface empty go2rtc main/SD frame routes as the current blocker.
Next time: Prefer one newest multi-signal live artifact over older narrow artifacts when they cover the same evidence category.

[2026-05-19] - WHEP reconnects need bounded recovery semantics
Oops: A formal Phase 4 soak failed when `deck-sub` briefly appeared as `upstream_state="new"` with `video_ready=false` even though the stream had already carried media and recovered on the next sample.
Why: The proxy cleared media readiness immediately when a previously healthy upstream session ended, so the health endpoint exposed the short reconnect startup window as a brand-new no-video wedge.
Fix: Keep last-known media readiness during a bounded recovery window, report the upstream state as `recovering`, and mark the stream non-reusable if recovery exceeds the timeout so the existing recreate path still replaces real failures.
Next time: For long WHEP soaks, distinguish first-start no-media wedges from transient recovery after proven media; do not let either one masquerade as the other.

[2026-05-19] - Phase 3 dead-branch cleanup needs a proof artifact
Oops: Phase 3 status could only say the dead-branch audit was missing, which left the remaining cleanup as a vague future task.
Why: There was no repeatable local audit that tied the cleanup to the production SD_ONLY prerequisite and listed the exact remaining HD/feed knobs.
Fix: Added `scripts/ha_phase3_dead_branch_audit.sh`, a read-only helper that writes a failing artifact until production SD_ONLY proof exists and legacy quality/HD/feed-selection branches are removed.
Next time: For deferred cleanup gates, add a proof generator that fails honestly now and can pass later without changing the goal definition.

[2026-05-19] - Production SD_ONLY needs its own read-only proof
Oops: Phase 3 had a strong dev-lane SD_ONLY proof, but production could only be described as "missing proof."
Why: The dev proof used a temporary dev swap, while production is its own running slot and may have different Supervisor options, stream configs, and go2rtc aliases.
Fix: Added `scripts/ha_phase3_prod_sd_only_probe.sh`, a read-only production probe that writes `phase3_prod_sd_only_*` artifacts. The fresh artifact showed production `SD_ONLY` is unset, stream configs do not report `sd_only=true`, Hamster and North Yard still have HD enabled, and non-SD go2rtc aliases remain.
Next time: Treat dev-lane proof and production proof as separate gates; use the production probe before claiming Phase 3 is ready for dead-branch cleanup.

[2026-05-19] - Phase 3 status needs exact production blockers
Oops: The dashboard only said production SD_ONLY proof failed, which hid the specific live blockers.
Why: `scripts/master_goal_status.py` looked only for the final fail marker and ignored the structured camera and alias rows inside the proof artifact.
Fix: Parse the latest production SD_ONLY artifact for the exact red facts: `SD_ONLY` unset, `sd_only` not true, wrong enabled feeds, HD still supported/enabled, and non-SD go2rtc aliases.
Next time: When a proof artifact is structured, surface its decisive fields in the status dashboard so the next action is obvious.

[2026-05-19] - Remaining work must be as sharp as evidence
Oops: Phase 1 and Phase 3 had detailed blocker evidence, but their `remaining` text still sounded generic.
Why: The status helper generated the final sentence from phase status alone instead of the parsed failure clues.
Fix: Build Phase 1 remaining text from the current North Yard snapshot/go2rtc/LAN clues, and Phase 3 remaining text from the production SD_ONLY proof fields plus the dead-branch audit state.
Next time: When adding detailed evidence parsers, update both the evidence list and the remaining-work sentence together.

[2026-05-19] - One-hour soaks need duration proof
Oops: A short Phase 4 WHEP preflight with a PASS line could have looked identical to the required one-hour soak in the master status helper.
Why: The status reader trusted the PASS marker but did not verify the artifact's recorded duration.
Fix: Require a WHEP soak PASS artifact to record at least 3600 seconds before Phase 4 can become complete; short passes stay useful but incomplete.
Next time: For time-boxed gates, parse and test the duration requirement, not just the final PASS/FAIL line.

[2026-05-19] - Stale LAN overrides can beat fresh helper IPs
Oops: North Yard's current helper path had a reachable private LAN IP, but the sidecar would still replace it with the configured override.
Why: `GO2RTC_LAN_IP_OVERRIDES` was applied by MAC match without checking whether the helper URL already had a private LAN host.
Fix: Keep helper-provided private LAN hosts by default and only force replacement when `GO2RTC_FORCE_LAN_IP_OVERRIDES=true` is explicitly set.
Next time: Treat manual IP overrides as emergency pins, not stronger truth than a current reachable helper URL.

[2026-05-20] - Current reachable IP must be visible in blockers
Oops: The Phase 1 status named the dead North Yard override, but it did not explicitly say the likely current/helper IP was reachable.
Why: The parser only recorded down/unreachable LAN facts, so a split state looked like generic reachability trouble instead of a stale-override/deploy gap.
Fix: Added a status clue for `192.168.1.183` reachable alongside `192.168.1.185` unreachable, and made the remaining-work text call out the stale override replacement.
Next time: For split-brain live probes, report both the working path and the broken forced path; otherwise the next agent may chase the wrong failure.

[2026-05-20] - Short WHEP passes are only context
Oops: A fresh two-second WHEP preflight passed cleanly, which could make Phase 4 sound healthier than the strict goal allows.
Why: The real Phase 4 gate is one continuous hour; a short preflight only proves the streams are healthy right now.
Fix: Taught the master status report to list the latest `phase4_whep_preflight_*` pass as context while keeping the failed one-hour soak as the blocker.
Next time: Keep quick preflights named separately from formal soaks, and never let a short pass replace the 3600-second proof.

[2026-05-20] - Whole-endpoint outage fails the WHEP soak
Oops: A formal Phase 4 soak stayed clean for 51 minutes, then production `/health`, every WHEP `/health/details` route, and Frigate stats all stopped responding together.
Why: This failure shape is wider than a single WHEP session; the follow-up doctor showed production `mtx_alive=false`, only two active streams, `Address already in use`, and MediaMTX starting again.
Fix: Taught `scripts/master_goal_status.py` to mark this endpoint-outage pattern as a failed live WHEP soak, saved `tmp/phase4_whep_soak_20260520_020822.txt`, and captured `tmp/ha_bridge_doctor_20260520_030413.txt`.
Next time: When all health endpoints vanish during a soak, stop treating it as inconclusive and immediately gather read-only doctor evidence before any recovery action.

[2026-05-20] - Local add-on reinstall can repair false Dockerfile-missing rebuilds
Oops: Rebuilding `local_docker_wyze_bridge_v4` removed the local image and then failed with `dockerfile is missing`, even though the Dockerfile existed in `/addons/local/wyze_bridge_v4`.
Why: Supervisor's install metadata for the local add-on was stale; adding a missing `config.yaml` and running repair rebuilt other slots but kept the local slot confused.
Fix: Restored service through the rebuilt old repository slot, saved the local slot options privately, uninstalled/reinstalled the local slot so Supervisor re-indexed the source folder, restored the options through the Supervisor API, then rebuilt and started the local slot successfully.
Next time: If a local HA add-on claims a present Dockerfile is missing, suspect the installed registration before rewriting source files; keep a running safety slot while repairing the local registration.

[2026-05-20] - `/api/ready` should wait only on aliases that serve the active feed
Oops: Production `/api/ready` stayed `loading` for 60 seconds even though `/api` had six cameras and six native RTSP URLs.
Why: The readiness check waited on every `native_selected` alias, including diagnostic native fields for cameras whose SD_ONLY feed had fallen back to the bridge path and was not actually being served from go2rtc.
Fix: Require go2rtc alias presence only when the catalog source is `go2rtc`, added a regression, rebuilt the local production slot, and captured a fresh Phase 2 soak with every `/api/ready` sample returning ready.
Next time: Treat `native_selected` as a diagnostic capability flag unless the active catalog source/path says the feed is really being served by go2rtc.

[2026-05-20] - North Yard proof was using the wrong go2rtc address
Oops: The North Yard live probe said go2rtc frames returned no bytes, but it was checking `127.0.0.1:11984` from the Home Assistant host.
Why: The go2rtc API listens inside the add-on container and is reachable from the HA host at the add-on IP, not host loopback. The bad probe made a measurement problem look like a camera problem.
Fix: Pointed the probe at `http://172.30.32.1:11984`, tested North Yard native variants without printing secrets, and found `192.168.1.175` returned changing SD JPEGs while `192.168.1.183` returned empty frames.
Next time: Before trusting a live probe that targets an internal service, confirm whether the port is host-mapped or add-on-IP-only.

[2026-06-02] - North Yard SD-only native cameras must not keep a fake HD alias
Oops: North Yard could look half-recovered after a bridge restart, but the live add-on still seeded both `north-yard` and `north-yard-sd` even though Home Assistant feed config explicitly had `HD=false` and `SD=true`.
Why: The go2rtc sidecar alias builder mixed coarse bridge catalog entries with per-feed `stream-config` and let the base catalog entry imply `hd=true` for a native SD camera. That made the fake `north-yard` alias compete with the real `north-yard-sd` path and produced the split-brain symptom where one alias returned frames while the other went dead and Scrypted flapped between `200` and `500`.
Fix: In `app/go2rtc_sidecar.sh`, force disabled feed flags from `/api/<cam>/stream-config` to win over the coarse catalog entry before building native aliases. In `app/wyzebridge/stream_manager.py`, add one forced re-preload retry before giving up on a stale selected native alias snapshot. After rebuilding the active HA slot, `/addon_configs/local_docker_wyze_bridge_v4/go2rtc_wyze.yaml` kept only `north-yard-sd`, `frame.jpeg?src=north-yard` returned `404`, `frame.jpeg?src=north-yard-sd` returned changing JPEGs again, bridge `/snapshot/north-yard.jpg` recovered, and Scrypted device `211` returned HTTP `200` snapshots again.
Next time: If a native SD camera is configured with `HD=false`, inspect the seeded `go2rtc_wyze.yaml` first. If both the base alias and `-sd` alias are present, treat that as a real alias-generation bug before chasing Scrypted settings again.

[2026-06-02] - Release work is not done until the homepage notes are updated
Oops: Multiple agents, including the final release pass, treated changelogs and version files as the release surface but forgot that the repo root `README.md` is the public GitHub homepage most users actually read first.
Why: The release checklist was implicit, so it was too easy to update tags, manifests, and changelogs while leaving the top-of-page release highlights stale on the main project page.
Fix: Updated the release highlights in `README.md` for `4.3.5` and added an explicit release-surface rule to `AGENTS.md` that any release, version bump, tag, or GitHub push must also refresh the top-of-file homepage highlights before the work is called done.
Next time: For every public release, verify the first visible release-notes section on the repo homepage shows the newest version before tagging, pushing, or closing the task.

[2026-05-20] - Phase 3 audit should separate old knobs from deliberate opt-in code
Oops: The dead-branch audit failed on every `hd` string across source and generated add-on trees, including the future HD opt-in controls that are still intentional.
Why: The grep was too broad and mixed obsolete Home Assistant quality fields with legitimate code that production `SD_ONLY` already proves inert.
Fix: Removed the old HA quality/bitrate fields from canonical/overlay config and updated the audit to fail on those legacy knobs while recording the deliberate HD opt-in branch review separately.
Next time: For cleanup gates, make the audit encode the architectural decision, not just raw keyword presence.

[2026-05-20] - Frigate UI load can break the strict Phase 4 gate without WHEP failing
Oops: A one-hour Phase 4 retry reached the end with every WHEP stream media-ready, but the script still failed because Doorbell had brief `skipped_fps` blips earlier in the soak.
Why: The follow-up Doorbell diagnostic showed current Frigate FPS recovered and both Scrypted RTSP inputs ffprobe cleanly. Frigate logs around the blip window showed Doorbell live/latest image requests through the Frigate UI, so the strict skipped-FPS gate can be tripped by external Frigate/Scrypted load even when Wyze Bridge WHEP is healthy.
Fix: Captured the failed soak and Doorbell input diagnostic instead of mislabeling it as a bridge/WHEP failure.
Next time: If Phase 4 fails with WHEP healthy and only Frigate skipped-FPS red, check recent Frigate UI/live traffic and Scrypted RTSP playback before changing bridge code.

[2026-06-19] - Extracted Python helpers from shell heredocs need PYTHONPATH set in the shell script
Oops: After extracting embedded Python from go2rtc_sidecar.sh into go2rtc_sidecar_helpers.py, the shell script's `python3 -c "from go2rtc_sidecar_helpers import ..."` calls failed with ModuleNotFoundError because the helpers module lives in app/wyzebridge/ but the shell script runs from /app/.
Why: The shell script's working directory doesn't include app/wyzebridge/ on the Python path. The heredocs previously had all code inline, so there was no import path issue.
Fix: Set `HELPERS_PYTHONPATH="${SCRIPT_DIR}/wyzebridge"` at the top of the shell script and prefix every `python3 -c` call with `PYTHONPATH="${HELPERS_PYTHONPATH}"`.
Next time: When extracting inline Python from shell scripts into a separate module, add the PYTHONPATH prefix before the first `python3 -c` call, not after. Test the shell script syntax with `sh -n` and a Python import smoke test immediately.

[2026-06-19] - Tests checking string patterns in shell+Python helper pairs need to read both files
Oops: After extracting embedded Python from go2rtc_sidecar.sh, tests that asserted patterns like `GO2RTC_WYZE_VERBOSE_ALIASES` or `def bridge_camera_state(...)` in the shell script text failed because those patterns now live in go2rtc_sidecar_helpers.py.
Why: The tests read `helper_path.read_text()` on the shell script only. The patterns split across two files after extraction.
Fix: Added `_sidecar_helper_texts(base)` helper that reads both the shell script and the Python helpers file, concatenates them, and returns a single string. Updated all `helper_text = helper_path.read_text()` calls to use `helper_text = _sidecar_helper_texts(helper_path.parent.parent)`.
Next time: When extracting code from a file that tests inspect via string pattern matching, update the test harness to read all files that now contain the patterns. Don't update individual assertions — fix the read path once.

[2026-06-19] - Module-level helpers extracted to a new module need a logger wrapper for test patching
Oops: After extracting `_log_tutk_trace` from iotc.py to iotc_helpers.py, tests that patch `iotc_module.logger.info` failed because the function now used `iotc_helpers.logger` instead of `iotc.logger`.
Why: `log_tutk_trace` in iotc_helpers.py called `logger.info(trace)` on the helpers module's logger. Tests patched `wyzecam.iotc.logger.info` expecting the trace to flow through the iotc module's logger.
Fix: Kept a `_log_tutk_trace` wrapper function in iotc.py that calls `tutk_trace_enabled` from helpers but uses `logger.info(trace)` on iotc.py's own logger. The wrapper preserves the test contract while the helper logic lives in the helpers module.
Next time: When extracting a function that uses `logger`, check if tests patch the original module's logger. If so, keep a thin wrapper in the original module that calls the helper but logs through the original module's logger. Don't just alias the function — the logger reference is module-local.
