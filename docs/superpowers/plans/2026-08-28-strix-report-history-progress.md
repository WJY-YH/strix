# Strix 报告历史与精准进度 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不暴露密钥的前提下，为 Strix 增加 7 天报告保留、Markdown 下载、历史记录和真实阶段进度，并保留后台 Runner 运作。

**Architecture:** Runner 继续以 `/data` 持久卷保存状态和报告；新增状态阶段、历史列表和 Markdown 附件接口。UI 通过现有认证代理读取历史，并用一个独立增强脚本接入现有已构建页面，避免重写当前视觉界面。

**Tech Stack:** Python 3.12、标准库 `http.server`、pytest；Node.js ESM、Node test runner、现有静态 React bundle。

**Spec:** `docs/superpowers/specs/2026-08-28-strix-report-history-progress-design.md`

## Global Constraints

- 报告只保留 7 天；清理只处理已结束扫描，不删除运行中的扫描。
- 只提供 Markdown 下载；下载和历史接口必须经过 UI 会话与 Runner Bearer Token 双层认证。
- 精确白名单、授权确认、单任务并发和 2 MiB 报告上限保持不变。
- 浏览器端不得获得 `STRIX_RUNNER_TOKEN`、`STRIX_UI_ACCESS_TOKEN` 或 `LLM_API_KEY`。
- 不删除 `strix-ui-next`、Runner、测试靶场或 `strix-data` 持久卷。

### Task 1: Runner 状态阶段和历史查询

**Files:**
- Modify: `deploy/runner/jobs.py`
- Modify: `deploy/runner/app.py`
- Test: `tests/deploy/runner/test_jobs.py`
- Test: `tests/deploy/runner/test_api.py`

**Interfaces:**
- `ScanJob` adds `phase`, `phase_index`, `phase_total`, `message`, and `updated_at`.
- `ScanManager.list(limit: int = 100) -> list[ScanJob]` returns newest-first persisted jobs.
- `GET /v1/scans` returns `{scans: [...]}`.
- Existing `GET /v1/scans/{id}` keeps its fields and adds the phase fields.

- [ ] **Step 1: Write the failing tests**

Add tests that create a manager with a fake process, assert a new job starts at `preparing`, assert the job payload includes phase fields, and assert `GET /v1/scans` returns newest-first records after a manager reload.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/deploy/runner/test_jobs.py tests/deploy/runner/test_api.py -q`
Expected: FAIL because `ScanJob` has no phase fields and the list route is missing.

- [ ] **Step 3: Implement the minimal state and route changes**

Persist the new fields atomically with the existing JSON state file, update `updated_at` whenever state changes, mark `preparing` before process creation and `scanning` after process creation, and return list/status payloads without exposing logs or secrets.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/deploy/runner/test_jobs.py tests/deploy/runner/test_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add deploy/runner/jobs.py deploy/runner/app.py tests/deploy/runner/test_jobs.py tests/deploy/runner/test_api.py && git commit -m "feat: expose persistent scan history and phases"`

### Task 2: 真实阶段更新和 7 天清理

**Files:**
- Modify: `deploy/runner/jobs.py`
- Modify: `deploy/runner/config.py`
- Modify: `deploy/runner/preflight.py`
- Modify: `deploy/runner.env.example`
- Test: `tests/deploy/runner/test_jobs.py`
- Test: `tests/deploy/runner/test_config.py`

**Interfaces:**
- `RunnerConfig.retention_days` reads `STRIX_REPORT_RETENTION_DAYS` and defaults to `7`.
- `ScanManager.cleanup_expired(now: datetime | None = None) -> int` removes only terminal jobs older than the retention window.

- [ ] **Step 1: Write the failing tests**

Add tests for phase transitions to `scanning`, `analyzing`, `reporting`, and the terminal phase; add tests proving a 6-day terminal run remains, an 8-day terminal run is deleted, and a running run is never deleted; add config coverage for the default and invalid retention values.

- [ ] **Step 2: Run focused tests to verify they fail**

Run: `pytest tests/deploy/runner/test_jobs.py tests/deploy/runner/test_config.py -q`
Expected: FAIL because retention configuration and cleanup do not exist.

- [ ] **Step 3: Implement minimal cleanup and phase watcher**

Start a lightweight watcher for the active run directory. Set `analyzing` when `vulnerabilities.json` appears, `reporting` when `penetration_test_report.md` appears, and stop the watcher at terminal state. Run cleanup during manager initialization and never remove a running job.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `pytest tests/deploy/runner/test_jobs.py tests/deploy/runner/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add deploy/runner/jobs.py deploy/runner/config.py deploy/runner/preflight.py deploy/runner.env.example tests/deploy/runner/test_jobs.py tests/deploy/runner/test_config.py && git commit -m "feat: track scan phases and retain reports for seven days"`

### Task 3: Markdown 下载接口和 UI 代理

**Files:**
- Modify: `deploy/runner/app.py`
- Modify: `deploy/ui/runner-client.mjs`
- Modify: `deploy/ui/server.mjs`
- Test: `tests/deploy/runner/test_api.py`
- Test: `deploy/ui/tests/runner-client.test.mjs`
- Test: `deploy/ui/tests/server.test.mjs`

**Interfaces:**
- Runner `GET /v1/scans/{id}/report/download` returns `text/markdown; charset=utf-8` with `Content-Disposition: attachment; filename="strix-report-<safe-id>.md"`.
- UI `GET /api/scans/{id}/report/download` streams the authenticated Markdown attachment.
- Runner client adds `list()` and `downloadReport(id)` methods.

- [ ] **Step 1: Write the failing tests**

Add tests for a successful Markdown attachment, missing report returning 409/404, path-safe filenames, unauthenticated UI download returning 401, and proxying the download path with the Runner token kept server-side.

- [ ] **Step 2: Run focused tests to verify they fail**

Run: `pytest tests/deploy/runner/test_api.py -q && node --test deploy/ui/tests/*.test.mjs`
Expected: FAIL because the download route and client methods are missing.

- [ ] **Step 3: Implement the minimal download and proxy paths**

Read the bounded Markdown through `load_report`, reject empty/incomplete reports, set a fixed safe filename derived only from the scan ID, and proxy bytes without returning Runner credentials to the browser.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `pytest tests/deploy/runner/test_api.py -q && node --test deploy/ui/tests/*.test.mjs`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add deploy/runner/app.py deploy/ui/runner-client.mjs deploy/ui/server.mjs tests/deploy/runner/test_api.py deploy/ui/tests && git commit -m "feat: add authenticated markdown report downloads"`

### Task 4: 历史记录和精准进度页面

**Files:**
- Create: `deploy/ui/dist/client/enhancements.js`
- Modify: `deploy/ui/dist/client/index.html`
- Modify: `deploy/ui/server.mjs`
- Test: `deploy/ui/tests/enhancements.test.mjs`

**Interfaces:**
- Browser enhancement module exports pure `formatScanProgress(scan)` and `safeReportFilename(scanId)` helpers for tests.
- After login, it renders real history rows below `#history-title`, updates `.scan-progress` from the newest running scan, and provides a Markdown download button per completed row.

- [ ] **Step 1: Write the failing tests**

Add Node tests for phase labels, elapsed-time formatting, safe filenames, and the static bundle loading `/enhancements.js` after the main bundle.

- [ ] **Step 2: Run focused tests to verify they fail**

Run: `node --test deploy/ui/tests/enhancements.test.mjs`
Expected: FAIL because the enhancement module and script tag are missing.

- [ ] **Step 3: Implement the minimal browser enhancement**

Use `textContent` for all server-provided values, poll `/api/scans` on a short interval only after authentication succeeds, show phase name plus latest update time and elapsed time, and render an empty state without the old example row. Keep the existing report drawer and current-page download behavior intact.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `node --test deploy/ui/tests/enhancements.test.mjs deploy/ui/tests/*.test.mjs`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add deploy/ui/dist/client/enhancements.js deploy/ui/dist/client/index.html deploy/ui/server.mjs deploy/ui/tests/enhancements.test.mjs && git commit -m "feat: show scan history and precise progress"`

### Task 5: 全量验证、部署和公网解绑

**Files:**
- Modify: `docs/operations/strix-runner.md`
- Test: repository test suite and deployed endpoints

- [ ] **Step 1: Run all automated tests**

Run: `pytest -q && node --test deploy/ui/tests/*.test.mjs`
Expected: all tests pass.

- [ ] **Step 2: Build and publish fixed SHA images**

Push the branch `codex/strix-runner-mvp` to trigger `.github/workflows/strix-runner-images.yml`; wait for the `python-tests`, `node-tests`, `image-builds`, and `publish-images` jobs to pass, then record `${GITHUB_SHA}` and the three GHCR image digests. Do not change the model key.

- [ ] **Step 3: Deploy Runner and UI without deleting persistent data**

Recreate only the Runner/UI containers with the new SHA, keep `strix-data`, `strix-private`, and the test target, and set `STRIX_REPORT_RETENTION_DAYS=7`.

- [ ] **Step 4: Verify live behavior**

Check `/health`, authorized `/ready`, history after restart, Markdown download headers/content, precise progress during an authorized fixture scan, and that unauthenticated history/download requests return 401.

- [ ] **Step 5: Unbind the public UI domain**

Remove only `strix-security-wjy.zeabur.app` from `strix-ui-next`; leave the service, Runner, test target, and data volume running.

- [ ] **Step 6: Commit operations documentation**

Update the operations guide with the seven-day variable, history/download endpoints, and the private UI recovery procedure, then commit with `git add docs/operations/strix-runner.md && git commit -m "docs: document report retention and private UI recovery"`.
