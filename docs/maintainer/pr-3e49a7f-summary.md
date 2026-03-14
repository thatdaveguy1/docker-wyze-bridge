# PR Draft for `3e49a7f`

## Title

Finalize local patched release state and attribution

## Summary

- add the committed Home Assistant local patched add-on tree under `.ha_live_addon/`, including config, runtime, assets, translations, docs, and deployment helpers
- document provenance clearly by crediting `idisposable/docker-wyze-bridge` as the base, `akeslo/docker-wyze-bridge` as a likely KVS/WebRTC influence, and this repo for the HA integration, reliability fixes, tests, and release work
- sync the root app with the staged V4/KVS expectations by adding KVS camera routing helpers, proxy config support, stream-availability improvements, and focused regression tests

## Test Plan

- [x] Run Python regression coverage for the changed root-app paths:
  - `python3 -m unittest test_web_ui_preview_streams.py test_frontend_kvs_config_auth.py test_all_rtc_proxy_config.py test_audio_session_lifetime.py test_connect_watchdog_timeout.py test_fixes.py test_live_v4_kvs_routing.py test_v4_auth_protocol.py test_v4_kvs_trial_routing.py test_v4_timeout_fix.py`
- [x] Confirm the regression suite result is `Ran 23 tests ... OK`
- [x] Scan committed sample payloads and test helpers to confirm live Wyze credentials are redacted
- [ ] Run `go test ./...` in `.ha_live_addon/whep_proxy` once Go is available or the Docker daemon is running

## Notes

- The provenance appendix is in `docs/maintainer/provenance-2026-03-11-local-patched-attribution.md`.
- The release-level attribution summary is in `.ha_live_addon/CHANGELOG.md`.
- The remaining unchecked item is environmental, not a known code failure: this shell currently has no `go` binary and Docker cannot reach its daemon.
