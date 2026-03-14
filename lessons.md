# Lessons

- Keep repo-tracked env files free of secrets. Use git-ignored local override files for any real keys or host-specific values.
- Never embed stream credentials into URLs rendered by the UI or returned by the API. Use request headers for authenticated playback instead.
- Keep internal bridge and proxy surfaces on loopback when they are only meant for local process-to-process traffic.
- Put `.dockerignore` at the build-context root so local override files like `*.env.local` and `build.env.local` cannot enter image builds.
- When a secret reaches git history, rewrite it from a clean clone, verify the literal value is absent from `git rev-list --all`, and only then update public refs.
