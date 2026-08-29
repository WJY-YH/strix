# Mixed Batch Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent mixed-target batch queue that accepts websites, public GitHub repositories, and ZIP uploads, executes one item at a time, and keeps per-item Markdown reports.

**Architecture:** A `BatchManager` persists batch metadata and item state beside the existing Runner state. It delegates execution to the existing single-process `ScanManager`, so the current Docker isolation, progress phases, upload cleanup, and report retention stay in one place. The UI adds a batch composer and polls batch state; the existing single-scan API remains unchanged.

**Tech Stack:** Python 3.12 standard-library HTTP server and JSON state files; Node.js browser enhancement script and existing UI proxy; pytest and Node test runner.

**Spec:** `docs/superpowers/specs/2026-08-29-mixed-batch-scan-design.md`

## Global Constraints

- Execute at most one Strix process at a time.
- Accept only `website`, `repository`, and `local_code` items.
- Keep existing ZIP safety limits and seven-day report retention.
- Do not expose tokens, host paths, or full logs in API/UI responses.
- Preserve the existing `/v1/scans` contract and single-scan behavior.

### Task 1: Add persistent batch domain model and scheduler

**Files:**
- Create: `deploy/runner/batches.py`
- Modify: `deploy/runner/jobs.py`
- Test: `tests/deploy/runner/test_batches.py`

**Interfaces:**
- `BatchManager.create(items: list[AuthorizedTarget], quick_scan: bool) -> BatchJob`
- `BatchManager.list(limit: int = 50) -> list[BatchJob]`
- `BatchManager.get(batch_id: str) -> BatchJob`
- `BatchManager.cancel(batch_id: str) -> BatchJob`
- `BatchManager.tick() -> None`
- `BatchJob` exposes `id`, `status`, `created_at`, `updated_at`, `total`, `completed`, `items`.
- `BatchItem` exposes `id`, `position`, `target`, `status`, `scan_id`, `message`, and timestamps.

- [ ] **Step 1: Write failing scheduler tests**

```python
def test_batch_runs_items_in_order_and_continues_after_failure(tmp_path):
    manager, factory = make_batch_manager(tmp_path, exit_codes=[1, 0])
    batch = manager.create([website_target(), repository_target()], quick_scan=True)

    manager.tick()
    finish_active_scan(manager.scan_manager, factory)
    manager.tick()

    assert [call[0] for call in factory.calls] == ["http://one.test", "https://github.com/acme/repo"]
    assert [item.status for item in manager.get(batch.id).items] == ["failed", "running"]
```

- [ ] **Step 2: Run the focused test and verify it fails because `BatchManager` is absent.**

Run: `./.venv/bin/pytest tests/deploy/runner/test_batches.py::test_batch_runs_items_in_order_and_continues_after_failure -q`

- [ ] **Step 3: Implement the minimal persisted model.** Store one JSON file per batch under `runner-state/batches/`, derive the active item from persisted state, call `ScanManager.start`, and use a watcher thread or `tick()` to promote terminal single scans to the next item. Mark a running item failed during restart recovery, then continue waiting items.

- [ ] **Step 4: Add tests for cancel, restart recovery, empty input, and per-item scan/report association.**

- [ ] **Step 5: Run all batch tests and commit.**

Run: `./.venv/bin/pytest tests/deploy/runner/test_batches.py -q`

Commit: `git commit -m "feat: add persistent mixed batch scheduler"`

### Task 2: Expose authenticated batch API

**Files:**
- Modify: `deploy/runner/app.py`
- Modify: `tests/deploy/runner/test_api.py`

**Interfaces:**
- `POST /v1/batches` accepts `{items: [{type, target}], quickScan, authorized}` and returns `202` with a batch payload.
- `GET /v1/batches` returns `{batches: [...]}`.
- `GET /v1/batches/{id}` returns a batch payload with item progress.
- `POST /v1/batches/{id}/cancel` returns the cancelled batch payload.

- [ ] **Step 1: Add API tests for authentication, mixed target creation, status, and cancellation.**
- [ ] **Step 2: Run the focused API tests and verify the new routes return `404` before implementation.**
- [ ] **Step 3: Add strict payload validation, target authorization checks, and safe error mapping.**
- [ ] **Step 4: Run all Runner API tests and Ruff.**

Run: `./.venv/bin/pytest tests/deploy/runner/test_api.py -q && ./.venv/bin/ruff check deploy/runner tests/deploy/runner`

- [ ] **Step 5: Commit.**

Commit: `git commit -m "feat: expose batch scan API"`

### Task 3: Add UI proxy and batch client methods

**Files:**
- Modify: `deploy/ui/server.mjs`
- Modify: `deploy/ui/runner-client.mjs`
- Test: `deploy/ui/tests/server.test.mjs`
- Test: `deploy/ui/tests/runner-client.test.mjs`

- [ ] **Step 1: Write failing proxy/client tests for the four batch operations.**
- [ ] **Step 2: Run focused Node tests and verify missing route/method failures.**
- [ ] **Step 3: Proxy batch routes with the same Runner token handling and add `createBatch`, `listBatches`, `batchStatus`, and `cancelBatch` methods.**
- [ ] **Step 4: Run all UI Node tests.**

Run: `npm --prefix deploy/ui test`

- [ ] **Step 5: Commit.**

Commit: `git commit -m "feat: proxy batch scan operations"`

### Task 4: Build mixed batch composer and progress UI

**Files:**
- Modify: `deploy/ui/dist/client/enhancements.js`
- Test: `deploy/ui/tests/enhancements.test.mjs`
- Modify: `deploy/ui/dist/index.html` only if the existing enhancement mount point is insufficient.

- [ ] **Step 1: Add failing tests for mixed item normalization, ZIP multi-file expansion, and batch progress summaries.**
- [ ] **Step 2: Run focused enhancement tests and verify the new helpers are absent.**
- [ ] **Step 3: Add a “批量检查” panel with add/remove rows, website/GitHub inputs, multi-file ZIP selection, authorization checkbox, and a single start button.**
- [ ] **Step 4: Upload ZIP files before batch creation, submit normalized item descriptors, poll `/api/batches/{id}`, render `第 N / 总数`, and add per-item Markdown links.**
- [ ] **Step 5: Add cancel behavior and refresh recovery without changing the existing single-scan ZIP flow.**
- [ ] **Step 6: Run all UI tests and inspect the live DOM locally.**

Run: `npm --prefix deploy/ui test && node --test deploy/test-target/server.test.mjs`

- [ ] **Step 7: Commit.**

Commit: `git commit -m "feat: add mixed batch scan UI"`

### Task 5: Integrate, verify, and deploy

**Files:**
- Modify: `docs/usage/private-ui-zip-upload.md` if batch usage needs documentation.

- [ ] **Step 1: Run the full Python and Node test suites plus diff checks.**
- [ ] **Step 2: Push the feature branch and wait for image/test CI.**
- [ ] **Step 3: Publish and deploy the Runner and UI images without deleting `strix-data` or changing private networking.**
- [ ] **Step 4: Verify `/health`, authenticated `/ready`, batch API validation, and a small non-production batch if it does not spend real model budget.**
- [ ] **Step 5: Merge the feature branch into `main` only after CI and live health checks pass.**
