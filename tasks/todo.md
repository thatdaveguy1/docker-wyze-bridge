# Todo

- [completed] Remove public maintainer-only operational docs from the repository.
- [completed] Keep `lessons.md` and `tasks/todo.md` as required repo files, but sanitize them so they only contain generic project-safe notes.
- [completed] Add `.github/SECURITY.md` with a private security reporting path.
- [completed] Narrow GitHub Actions permissions so only the version bump job has `contents: write`.
- [completed] Move root Python tests into a dedicated `tests/` directory and verify discovery still works.
- [completed] Apply minimal branch protection on `main` to block force-pushes and deletions without breaking direct CI-driven version bumps.
- [in_progress] Fix the GHCR hwaccel Docker build regression and rerun the Docker workflow for the final `v4.0.0` tip.
- [completed] Convert `.ha_live_addon` into a distinct Home Assistant `Dev Build` add-on with its own slug for staging.
- [completed] Add SSH staging helpers for syncing the dev add-on, copying production settings, and swapping prod/dev safely.
- [completed] Add local-only maintainer docs for the Home Assistant dev-build lane, smoke checks, troubleshooting, and promotion.
- [completed] Add a fallback deployment path that reuses an already-indexed local HA add-on slot when Supervisor store reload is blocked.
- [completed] Make the Home Assistant add-on expose the standard visible login path (`Wyze email`, `Wyze password`, `Key ID`, `API key`) in both prod and dev manifests and lock it in with a packaging test.
- [completed] Clarify Home Assistant sub-stream wording so it follows the internal capability map and explicitly notes that Pan V2 is not currently included.
- [completed] Validate the current KVS startup/playback behavior in the HA dev add-on lane; no `400` warning reproduced, so no KVS runtime or logging change was applied in this pass.
- [completed] Prepare the `4.0.1` release surfaces with version bumps, changelog entries, and updated HA-facing release docs.
- [completed] Fix the HA local add-on validation lane so metadata refreshes use `ha store reload` first and dev syncs exclude macOS junk files that can break Supervisor parsing.
