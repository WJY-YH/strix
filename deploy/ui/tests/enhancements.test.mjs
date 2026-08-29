import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { formatScanProgress, rewriteZipScanBody, safeReportFilename } from "../dist/client/enhancements.js";


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


test("static page loads the history enhancement after the app bundle", async () => {
  const html = await readFile(new URL("../dist/client/index.html", import.meta.url), "utf8");
  assert.match(html, /src="\/enhancements\.js"/);
});
