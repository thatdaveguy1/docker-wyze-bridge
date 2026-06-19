# Architecture Refactor: Candidate #7 — HA Bridge Probe Library

Last updated: 2026-06-19
Status: in-progress

## Objective

Extract shared HA bridge probe logic (token derivation, redaction, validation,
section/mark_fail helpers) from ~18 shell scripts and the go2rtc sidecar into
one `scripts/ha_bridge_probe.sh` library. A security fix or redaction rule
change propagates to all scripts automatically. The AGENTS.md `api`-header
rule becomes enforced code, not an advisory note.

## Success Criteria

1. `scripts/ha_bridge_probe.sh` exists and defines: `derive_bridge_token`,
   `redact_api_keys`, `validate_slug`, `validate_base_url`, `section`,
   `mark_fail`, `bool_true`.
2. Each migrated script sources/cats the library instead of duplicating the
   function bodies.
3. All existing tests pass (updated to check the library for moved patterns).
4. New test `tests/test_ha_bridge_probe.py` covers the library directly.
5. `./scripts/run_master_local_gates.sh` is green.
6. No script gains new forbidden patterns (ha apps stop, rm -rf, etc).

## Architectural Constraint

The scripts run remotely via `ha_ssh.sh "sh -s" <<'REMOTE'`. The shared
functions live INSIDE the heredoc (on the remote HA host). The library is
injected by piping `{ cat library; cat <<'REMOTE' ... REMOTE; }` to
`ha_ssh.sh`. Validation functions are sourced locally before the SSH call.

## Functions Extracted (identical across scripts)

- `section()` — printf section header
- `mark_fail()` — echo FAIL + set FAIL=1
- `redact_api_keys()` — sed redaction (was `redact()`)
- `derive_bridge_token(info_json)` — SHA256 email → base64 token
- `validate_slug(name, value)` — validate slug env var
- `validate_base_url(name, value)` — validate URL env var
- `bool_true(value)` — check if string is true/1/yes/on

## Functions NOT Extracted (vary per script)

- `curl_bridge_*()` — max-time, output handling, and route patterns differ
- `fetch_bridge_json()` — wraps curl with script-specific args
- Probe logic, assertions, output format — all script-specific

## Files Touched

### New
- `scripts/ha_bridge_probe.sh` — the shared library
- `tests/test_ha_bridge_probe.py` — library tests

### Migrated (scripts)
- `scripts/ha_phase2_prod_startup_soak.sh`
- `scripts/ha_phase3_prod_sd_only_probe.sh` (tracer bullet)
- `scripts/ha_phase4_whep_soak.sh`
- `scripts/ha_phase5_prod_overlay_api_verify.sh`
- `scripts/ha_prod_recovery_verify.sh`
- `scripts/ha_bridge_doctor.sh`
- `scripts/ha_north_yard_live_probe.sh`
- `scripts/ha_wyze_camera_matrix_probe.sh`
- `scripts/ha_wyze_scrypted_snapshot_probe.sh`
- `scripts/ha_frigate_input_diag.sh`
- `scripts/ha_bridge_diag.sh`

### Updated (tests)
- `tests/test_ha_phase2_prod_startup_soak.py`
- `tests/test_ha_phase3_prod_sd_only_probe.py`
- `tests/test_ha_phase4_whep_soak.py`
- `tests/test_ha_phase5_prod_overlay_api_verify.py`
- `tests/test_ha_prod_recovery_verify.py`
- `tests/test_ha_bridge_doctor.py`
- `tests/test_ha_north_yard_live_probe.py`
- `tests/test_ha_wyze_camera_matrix_probe.py`
- `tests/test_ha_wyze_scrypted_snapshot_probe.py`
- `tests/test_ha_frigate_input_diag.py`

### Updated (tracking)
- `tasks/todo.md`
- `lessons.md`
- `AGENTS.md`

## Test Update Pattern

For tests that checked for moved patterns in script text:
```python
# Before:
self.assertIn('s/api=[^" ]+/api=<redacted>/g', script)
self.assertIn("xxd -r -p", script)

# After:
library = (ROOT / "scripts" / "ha_bridge_probe.sh").read_text()
self.assertIn("ha_bridge_probe.sh", script)  # script references library
self.assertIn('s/api=[^" ]+/api=<redacted>/g', library)
self.assertIn("xxd -r -p", library)
```

Patterns that stay in scripts (curl with `-H "api: $API_TOKEN"`) are still
checked in script text.

## Tracer Bullet

`ha_phase3_prod_sd_only_probe.sh` is the tracer — it has all duplicated
patterns and a straightforward test. Migrate it first, verify tests pass,
then migrate the rest.
