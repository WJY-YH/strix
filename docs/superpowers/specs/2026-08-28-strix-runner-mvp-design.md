# Strix Runner MVP Design

## Goal

Make `https://strix-security-wjy.zeabur.app/` capable of launching and displaying one real, authorized Strix quick scan without exposing Docker or LLM credentials to the public web service.

The MVP is ready for arranged testing when a private fixture scan can start from the Chinese UI, finish, display its result, and remain available after the runner restarts.

## Current State

- The Zeabur `strix-ui` service is healthy and serves the Chinese beginner UI on port 8080.
- Its management panel reports that the Strix CLI, Docker daemon, and sandbox image are unavailable.
- The service has LLM and UI-related environment variable names configured, but the secret values remain intentionally unread.
- The service is an uploaded OCI image. Zeabur shows no source branch or commit.
- The deployed Chinese UI source is not present in `WJY-YH/strix`.
- No persistent Zeabur volume is mounted, so container-local scan data would be lost on redeploy or restart.
- The current Codex host also has neither Docker nor the Strix CLI; it cannot act as the runner.

## Architecture

```text
Browser
  |
  | HTTPS + UI session
  v
Zeabur strix-ui (public, no Docker socket, no LLM key in browser)
  |
  | HTTPS + runner bearer token
  v
Private runner API on the Tencent Ashburn dedicated server
  |
  | fixed argv, target allowlist, concurrency=1
  v
Strix CLI -> local Docker daemon -> strix-sandbox image
  |
  v
/data/strix_runs (persistent) + private test fixture
```

The public service never receives a Docker socket. Docker control stays on the dedicated runner host. The UI server is the only caller allowed to hold the runner token; browser JavaScript never receives it.

## Repository Layout

The existing Strix engine remains under `strix/`. New deployment code is isolated under `deploy/` so upstream engine changes stay reviewable.

- `deploy/ui/`: recovered Chinese UI source, its Node server, tests, and Dockerfile.
- `deploy/runner/`: authenticated runner API, process supervisor, readiness checks, tests, Dockerfile, and service files.
- `deploy/test-target/`: deliberately vulnerable fixture bound only to the runner host for acceptance testing.
- `.github/workflows/strix-runner-images.yml`: builds immutable UI and runner images from Git commits.
- `docs/operations/strix-runner.md`: deployment, secret rotation, rollback, and acceptance instructions.

## Components

### Public UI service

The current `/app` sources are recovered from the running Zeabur container and committed under `deploy/ui/`. The UI keeps its existing Chinese flow.

Its Node server gains one runner client. The client reads `STRIX_RUNNER_URL` and `STRIX_RUNNER_TOKEN` only on the server. Existing browser endpoints remain same-origin and never reveal those variables.

The management panel reports separate facts:

- UI server reachable.
- Runner API reachable and authenticated.
- Strix CLI runnable on the runner.
- Docker daemon reachable on the runner.
- Sandbox image present on the runner.
- LLM configuration present on the runner.
- Persistent result directory writable.

### Runner API

The runner is a small Python service installed beside the Strix package. It provides only these operations:

- `GET /health`: process liveness; contains no secret or environment values.
- `GET /ready`: authenticated preflight status.
- `POST /v1/scans`: start one allowed scan.
- `GET /v1/scans/{run_id}`: read status and a bounded result summary.
- `POST /v1/scans/{run_id}/cancel`: stop an active scan.

The API never accepts arbitrary command-line arguments. It constructs a fixed command equivalent to:

```text
strix -n --target <validated-target> --scan-mode quick --max-budget <server-cap>
```

Only one scan runs at a time. A second request receives HTTP 409. Process output is size-limited and credentials are redacted before logging.

Runner state is derived from `run.json` and the process supervisor. After a restart, completed runs remain readable and interrupted runs are marked failed with an explicit reason.

### Target authorization

The UI still requires the user to affirm ownership or explicit authorization. The runner enforces a second, server-side boundary:

- `STRIX_ALLOWED_TARGETS` is an exact hostname/repository allowlist.
- URL userinfo, fragments, loopback targets, link-local targets, cloud metadata addresses, and private IP ranges are rejected unless the target is the named private acceptance fixture.
- Redirects do not expand authorization: the final hostname must remain allowed.
- The runner does not accept instruction text, credentials, uploaded files, or repository write access in the MVP.

The first acceptance target is `http://host.docker.internal:3001`, a fixture bound only to the runner host. No real business system is scanned during deployment acceptance.

### Secrets and network boundary

- `LLM_API_KEY` and model configuration live only on the runner host.
- `STRIX_RUNNER_TOKEN` is a random, dedicated token stored on the runner and as a private Zeabur variable.
- `STRIX_UI_ACCESS_TOKEN` protects the public UI session and all scan endpoints.
- The runner binds to the Tencent server's private node IP on port 8787. The host firewall permits that port only from the local k3s pod network used by the Zeabur UI service and denies it on every public interface.
- Secrets are never committed, built into images, shown by readiness endpoints, or copied into logs.

The deployment records the actual node-private IP and pod CIDR during installation and verifies the route from the UI container. If that private route cannot be verified, deployment stops before enabling scan creation. A public runner endpoint is not an acceptable fallback.

### Persistence

The runner stores all run artifacts under `/data/strix_runs`. `/data` is a host-mounted persistent directory. The UI stores no authoritative scan state; it requests summaries from the runner.

The runner refuses new scans if `/data` is not writable or available disk falls below 10 GB. A restart test must prove that the fixture run remains readable.

## Error Handling

- UI-to-runner timeouts become a clear Chinese “执行器暂不可用” state.
- Authentication failure is reported as a configuration error without echoing tokens.
- Missing CLI, Docker, image, LLM configuration, storage, or network access appears as a separate readiness item.
- A failed Strix process records its exit code and a redacted final log excerpt.
- Strix exit code `2` means findings were produced, not that the runner failed.
- Cancellation sends a graceful interrupt first, then terminates after a fixed timeout.

## Deployment

1. Recover and commit the currently deployed UI source.
2. Add runner and fixture code with tests.
3. Build commit-addressed UI and runner images in GitHub Actions.
4. Install the runner on the Tencent Ashburn dedicated server with Docker access and `/data` persistence.
5. Establish and verify the private UI-to-runner route.
6. Configure runner secrets on the host and non-secret URL/token reference in Zeabur private variables.
7. Deploy the UI image from the GitHub-built artifact.
8. Run the private fixture acceptance scan.

Rollback means restoring the previous Zeabur UI image and stopping the runner service. Existing `/data/strix_runs` artifacts remain untouched.

## Testing

### Automated

- Target allowlist and SSRF rejection tests.
- Bearer-token authentication tests.
- Fixed-command construction tests; arbitrary arguments must be impossible.
- Single-concurrency and cancellation tests.
- Readiness tests for missing CLI, Docker, image, LLM configuration, and storage.
- Result parsing tests for Strix exit codes 0, 1, and 2.
- UI runner-client contract tests and Chinese error-state tests.
- Image build and smoke tests in CI.

### Acceptance

The MVP passes only when all of the following are freshly verified:

1. GitHub Actions succeeds for the exact commit deployed.
2. The Zeabur service shows the corresponding commit-addressed UI image.
3. An unauthenticated browser cannot create or read scans.
4. The runner is unreachable from the public Internet.
5. The management panel reports every runner preflight item ready.
6. The private fixture quick scan starts from the UI and reaches a terminal state.
7. Exit code 0 or 2 is interpreted correctly and the result appears in the UI.
8. LLM usage stays within the configured budget cap.
9. Restarting the runner preserves and redisplays the completed run.
10. No real business target, credential, payment flow, or production write is exercised.

## Out of Scope

- Scanning a real business or third-party target.
- Deep scans, schedules, multiple concurrent users, or multiple runners.
- Uploading credentials, source archives, instruction files, or private repositories.
- Automatic vulnerability fixes or pull requests.
- Making the runner publicly reachable.
