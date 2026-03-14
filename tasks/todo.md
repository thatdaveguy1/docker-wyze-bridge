# Todo

- [completed] Remove public maintainer-only operational docs from the repository.
- [completed] Keep `lessons.md` and `tasks/todo.md` as required repo files, but sanitize them so they only contain generic project-safe notes.
- [completed] Add `.github/SECURITY.md` with a private security reporting path.
- [completed] Narrow GitHub Actions permissions so only the version bump job has `contents: write`.
- [completed] Move root Python tests into a dedicated `tests/` directory and verify discovery still works.
- [completed] Apply minimal branch protection on `main` to block force-pushes and deletions without breaking direct CI-driven version bumps.
- [in_progress] Fix the GHCR hwaccel Docker build regression and rerun the Docker workflow for the final `v4.0.0` tip.
