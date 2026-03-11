# Lessons

- For this repo's Home Assistant local add-on flow, copying files into `/addons/local/wyze_bridge_local/...` plus `ha apps restart` is not enough to prove the running add-on picked up code changes.
- The reliable deployment boundary is `ha apps rebuild local_docker_wyze_bridge_local --force`, then verify the running HTTP service directly.
- Do not claim repo and HA are in sync based only on source-file copies or on-disk remote files. Verify the runtime response from the live add-on (`/static/site.js`, `/api`, rendered `/`) before making sync claims.
- When debugging HA ingress mismatches, distinguish three layers explicitly: local repo files, HA add-on source directory, and the running add-on HTTP responses. Check all three in that order.
- Persist HA SSH access locally in a git-ignored file (`scripts/.ha_ssh.env`) and use a helper (`scripts/ha_ssh.sh`) so connection details are not lost between steps.
- Use `scripts/deploy_ha_local_addon.sh` for local HA add-on deployment so copy, rebuild, and runtime verification happen as one repeatable flow.
- When a user reports "I still don't see it," stop assuming browser cache first. Re-verify the live runtime response and only then narrow to ingress/browser caching or CSS/layout issues.
- Capture repeatable workflows (MQTT/Scrypted pairings) in runbooks the moment you verify them—then future SSH-only handoffs don't require rediscovery.
- Before inventing new MQTT credentials, inspect live consumers like Frigate for an already-working broker/user pair; reusing verified LAN credentials removes unnecessary setup and speeds validation.
- Stream menu availability should not be driven only by `WB_RTSP_URL` or `WB_RTMP_URL`; if the bridge can construct a localhost/LAN URL for an active camera, keep that protocol enabled and preserve `disabled`/`offline` as the first failure reasons.
- When browser-testing the authenticated HA add-on directly with credentials embedded in the URL, `fetch()` calls for relative image refreshes can fail with `Request cannot be constructed from a URL that includes credentials`; use the live API output plus visible menu state to verify stream reasons, and avoid treating those Playwright-auth URL errors as deployment regressions.
- The WHEP/KVS proxy refresh path reaches Flask through loopback without Web UI credentials. Keep `/kvs-config/<camera>` accessible from `127.0.0.1`/`::1` even when normal Web UI auth is enabled, or on-demand RTC-backed RTSP can fail despite public UI auth still working.
