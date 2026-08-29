import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  formatBatchProgress,
  formatScanProgress,
  normalizeBatchItems,
  rewriteZipScanBody,
  safeReportFilename,
} from "../dist/client/enhancements.js";


test("progress formatter uses server phase and truthful elapsed data", () => {
  const progress = formatScanProgress({
    phase: "analyzing",
    phaseIndex: 3,
    phaseTotal: 4,
    message: "正在分析检测结果",
    startedAt: "2026-08-28T00:00:00Z",
    updatedAt: "2026-08-28T00:01:05Z",
  }, Date.parse("2026-08-28T00:01:10Z"));
  assert.deepEqual(progress, {
    phase: "analyzing",
    label: "正在分析检测结果",
    count: "3/4",
    elapsed: "已运行 1 分钟 10 秒",
    updated: "最近更新：00:01:05",
  });
});


test("report filenames cannot contain path separators", () => {
  assert.equal(safeReportFilename("scan/../../id"), "strix-report-scan-------id.md");
});


test("ZIP scan requests become local-code scans without changing authorization", () => {
  assert.deepEqual(JSON.parse(rewriteZipScanBody({ quickScan: false, authorized: true }, "upload-id")), {
    type: "local_code",
    target: "upload-id",
    quickScan: false,
    authorized: true,
  });
});


test("batch normalization keeps mixed targets in order and expands ZIP files", () => {
  assert.deepEqual(normalizeBatchItems([
    { type: "website", target: " https://example.test " },
    { type: "repository", target: "https://github.com/acme/repo" },
    { type: "local_code", files: [{ name: "one.zip" }, { name: "two.zip" }] },
  ], (file) => `upload-${file.name}`), [
    { type: "website", target: "https://example.test" },
    { type: "repository", target: "https://github.com/acme/repo" },
    { type: "local_code", target: "upload-one.zip" },
    { type: "local_code", target: "upload-two.zip" },
  ]);
});


test("batch progress reports completed items and current item", () => {
  assert.deepEqual(formatBatchProgress({
    total: 3,
    completed: 1,
    status: "running",
    items: [
      { status: "complete" },
      { status: "running", phaseIndex: 2, phaseTotal: 4, message: "正在分析检测结果" },
      { status: "queued" },
    ],
  }), {
    count: "1/3",
    current: "正在分析检测结果",
    phase: "2/4",
  });
});


test("static page loads the history enhancement after the app bundle", async () => {
  const html = await readFile(new URL("../dist/client/index.html", import.meta.url), "utf8");
  assert.match(html, /src="\/enhancements\.js\?v=batch-scan-1"/);
});


test("ZIP upload enhancement supports inputs that are not inside labels", async () => {
  const source = await readFile(new URL("../dist/client/enhancements.js", import.meta.url), "utf8");
  assert.match(source, /targetInput\.parentElement/);
});
