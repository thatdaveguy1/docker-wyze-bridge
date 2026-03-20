# Lessons

- Keep repo-tracked env files free of secrets. Use git-ignored local override files for any real keys or host-specific values.
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
