# Strix ZIP 上传扫描 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a secure local ZIP upload path that runs the existing Strix scanner and preserves the current progress/report workflow.

**Architecture:** The browser uploads a ZIP through the authenticated UI server to the authenticated Runner. The Runner validates and stores a one-time upload, extracts it into an isolated per-scan directory, starts the existing local-target scan, and removes source artifacts at terminal state. The current website/repository API contracts remain unchanged.

**Tech Stack:** Python standard library (`zipfile`, `pathlib`, `http.server`), Node.js native `fetch`/streams, static browser enhancement JavaScript, pytest, Node test runner.

**Spec:** `docs/superpowers/specs/2026-08-29-strix-zip-upload-design.md`

## Global Constraints

- Accept only `.zip` uploads up to 100 MB compressed.
- Reject absolute paths, `..` traversal, symbolic links, excessive entries, and excessive uncompressed size.
- Delete ZIP and extracted source at scan terminal state; retain reports for the existing 7-day period.
- Require the existing UI session and Runner bearer token for every upload and scan request.
- Do not accept passwords, cookies, or OTPs as upload metadata.

### Task 1: Runner ZIP validation and upload storage

**Files:**
- Create: `deploy/runner/uploads.py`
- Modify: `deploy/runner/config.py`
- Modify: `deploy/runner/app.py`
- Test: `tests/deploy/runner/test_uploads.py`
- Test: `tests/deploy/runner/test_api.py`

**Interfaces:**
- `UploadStore.save(stream, content_length, filename) -> UploadRecord` validates and stores an upload.
- `UploadStore.prepare(upload_id, destination) -> Path` safely extracts an upload and returns its source directory.
- `UploadStore.discard(upload_id) -> None` removes all upload artifacts.
- `POST /v1/uploads` accepts `application/zip` and returns `{ "uploadId": "...", "filename": "...", "size": N }`.

- [ ] **Step 1: Write failing validation tests** for invalid ZIP bytes, traversal, symlink entries, compressed-size limit, uncompressed-size limit, entry-count limit, and successful storage.
- [ ] **Step 2: Run `./.venv/bin/pytest tests/deploy/runner/test_uploads.py -q` and confirm the tests fail because `UploadStore` does not exist.
- [ ] **Step 3: Implement bounded streaming storage and safe ZIP validation/extraction.** Use generated UUIDs, explicit size counters, `ZipInfo.external_attr` symlink detection, POSIX path normalization, and destination containment checks.
- [ ] **Step 4: Add authenticated `/v1/uploads` handling** with `Content-Length`, MIME/filename checks, bounded reads, and JSON errors.
- [ ] **Step 5: Run the upload tests and API upload tests; confirm they pass.**
- [ ] **Step 6: Commit `feat: add secure runner zip uploads`.**

### Task 2: Local-code scan lifecycle

**Files:**
- Modify: `deploy/runner/targets.py`
- Modify: `deploy/runner/jobs.py`
- Modify: `deploy/runner/app.py`
- Test: `tests/deploy/runner/test_jobs.py`
- Test: `tests/deploy/runner/test_api.py`

**Interfaces:**
- `validate_target("local_code", upload_id, allowed) -> AuthorizedTarget` accepts only a UUID upload id.
- `ScanManager.start(target, *, quick_scan=True) -> ScanJob` prepares local code targets before starting Strix.

- [ ] **Step 1: Write failing tests** for local-code target validation, scan argv using an extracted directory, and cleanup after terminal state.
- [ ] **Step 2: Run focused tests and confirm the new tests fail.**
- [ ] **Step 3: Inject `UploadStore` into `ScanManager`, prepare the upload, and pass the extracted directory as `--target`.** Preserve quick/full mode behavior and existing report paths.
- [ ] **Step 4: Remove upload artifacts on process completion, failed start, stop, and startup cleanup.**
- [ ] **Step 5: Run `./.venv/bin/pytest tests/deploy/runner -q` and `./.venv/bin/ruff check deploy/runner tests/deploy/runner`.**
- [ ] **Step 6: Commit `feat: scan uploaded local code`.**

### Task 3: UI proxy and upload control

**Files:**
- Modify: `deploy/ui/server.mjs`
- Modify: `deploy/ui/runner-client.mjs`
- Modify: `deploy/ui/dist/client/enhancements.js`
- Modify: `deploy/ui/dist/client/index.html`
- Test: `deploy/ui/tests/runner-client.test.mjs`
- Test: `deploy/ui/tests/server.test.mjs`
- Test: `deploy/ui/tests/enhancements.test.mjs`

**Interfaces:**
- `runnerClient.uploadZip(stream, { filename, contentLength }) -> UploadRecord`.
- `POST /api/uploads` proxies an authenticated ZIP stream to Runner.
- Browser control exposes a labeled ZIP picker and rewrites the next scan request to use its `uploadId`.

- [ ] **Step 1: Write failing Node tests** for upload proxy auth, stream forwarding, filename/size display, and local-code request rewriting.
- [ ] **Step 2: Run the focused Node tests and confirm failure.**
- [ ] **Step 3: Implement streaming proxy with `duplex: "half"`, no in-memory base64 conversion, and safe error mapping.**
- [ ] **Step 4: Add the upload control to the existing static UI, keeping the authorization checkbox and existing CTA.**
- [ ] **Step 5: Run `npm --prefix deploy/ui test` and `node --test deploy/test-target/server.test.mjs`.**
- [ ] **Step 6: Commit `feat: add zip upload UI`.**

### Task 4: Integration verification and deployment

**Files:**
- Modify: `docs/usage` or the nearest existing deployment guide with ZIP usage and limits.

- [ ] **Step 1: Run the full Runner and UI test suites.**
- [ ] **Step 2: Build the UI and Runner images through the existing GitHub Actions workflow.**
- [ ] **Step 3: Replace only the Runner/UI containers on Zeabur, preserving the existing data volume, environment file, domains, and keys.**
- [ ] **Step 4: Verify `/health`, authenticated `/ready`, upload rejection cases, and one authorized ZIP scan without exposing source contents or secrets.**
- [ ] **Step 5: Confirm report download and source cleanup.**
- [ ] **Step 6: Merge the feature branch into `main` and push it.**
