import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { createUiServer } from "../server.mjs";


async function withServer(callback) {
  const clientDir = await mkdtemp(join(tmpdir(), "strix-ui-test-"));
  await writeFile(join(clientDir, "index.html"), "<h1>Strix UI</h1>");
  const calls = [];
  const runnerClient = {
    ready: async () => ({ ready: true }),
    uploadZip: async (stream, metadata) => {
      calls.push(["uploadZip", metadata, stream]);
      return { uploadId: "upload-id", filename: metadata.filename, size: metadata.contentLength };
    },
    list: async () => (calls.push(["list"]), { scans: [] }),
    start: async (body) => (calls.push(["start", body]), { id: "scan-id" }),
    status: async (id) => (calls.push(["status", id]), { id, status: "running" }),
    stop: async (id) => (calls.push(["stop", id]), { id, status: "stopped" }),
    report: async (id) => (
      calls.push(["report", id]),
      { summary: "完成", markdown: "# 报告", findings: 0 }
    ),
    downloadReport: async (id) => (
      calls.push(["downloadReport", id]),
      {
        body: new Response("# 报告\n"),
        contentType: "text/markdown; charset=utf-8",
        contentDisposition: 'attachment; filename="strix-report-scan-id.md"',
      }
    ),
    createBatch: async (body) => (calls.push(["createBatch", body]), { id: "batch-id" }),
    listBatches: async () => (calls.push(["listBatches"]), { batches: [] }),
    batchStatus: async (id) => (calls.push(["batchStatus", id]), { id, status: "running" }),
    cancelBatch: async (id) => (calls.push(["cancelBatch", id]), { id, status: "cancelled" }),
  };
  const server = createUiServer({
    accessToken: "ui-access-token",
    runnerClient,
    clientDir,
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  try {
    await callback(`http://127.0.0.1:${address.port}`, calls);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}


test("UI login creates an opaque secure session", async () => {
  await withServer(async (baseUrl) => {
    assert.equal((await fetch(`${baseUrl}/api/preflight`)).status, 401);
    const login = await fetch(`${baseUrl}/api/session`, {
      method: "POST",
      body: JSON.stringify({ token: "ui-access-token" }),
    });
    assert.equal(login.status, 200);
    assert.match(login.headers.get("set-cookie"), /HttpOnly/);
    assert.match(login.headers.get("set-cookie"), /Secure/);
    assert.doesNotMatch(login.headers.get("set-cookie"), /ui-access-token/);
  });
});


test("UI proxies the existing scan contract", async () => {
  await withServer(async (baseUrl, calls) => {
    const login = await fetch(`${baseUrl}/api/session`, {
      method: "POST",
      body: JSON.stringify({ token: "ui-access-token" }),
    });
    const cookie = login.headers.get("set-cookie").split(";", 1)[0];
    const request = (path, options = {}) => fetch(`${baseUrl}${path}`, {
      ...options,
      headers: { ...options.headers, Cookie: cookie },
    });

    assert.equal((await request("/api/preflight")).status, 200);
    assert.equal((await request("/api/scans")).status, 200);
    assert.equal((await request("/api/scans", {
      method: "POST",
      body: JSON.stringify({ type: "website", target: "fixture" }),
    })).status, 202);
    assert.equal((await request("/api/scans/scan-id")).status, 200);
    assert.equal((await request("/api/scans/scan-id/stop", { method: "POST" })).status, 200);
    assert.equal((await request("/api/scans/scan-id/report")).status, 200);
    const download = await request("/api/scans/scan-id/report/download");
    assert.equal(download.status, 200);
    assert.equal(await download.text(), "# 报告\n");
    assert.equal(download.headers.get("content-type"), "text/markdown; charset=utf-8");
    assert.match(download.headers.get("content-disposition"), /strix-report-scan-id\.md/);
    assert.equal((await request("/api/batches")).status, 200);
    assert.equal((await request("/api/batches", {
      method: "POST",
      body: JSON.stringify({ items: [], quickScan: true, authorized: true }),
    })).status, 202);
    assert.equal((await request("/api/batches/batch-id")).status, 200);
    assert.equal((await request("/api/batches/batch-id/cancel", { method: "POST" })).status, 200);
    assert.deepEqual(calls.map(([name]) => name), [
      "list",
      "start",
      "status",
      "stop",
      "report",
      "downloadReport",
      "listBatches",
      "createBatch",
      "batchStatus",
      "cancelBatch",
    ]);
  });
});


test("UI protects markdown downloads with the same session", async () => {
  await withServer(async (baseUrl) => {
    const response = await fetch(`${baseUrl}/api/scans/scan-id/report/download`);
    assert.equal(response.status, 401);
  });
});


test("UI proxies authenticated ZIP uploads", async () => {
  await withServer(async (baseUrl, calls) => {
    const login = await fetch(`${baseUrl}/api/session`, {
      method: "POST",
      body: JSON.stringify({ token: "ui-access-token" }),
    });
    const cookie = login.headers.get("set-cookie").split(";", 1)[0];
    const response = await fetch(`${baseUrl}/api/uploads`, {
      method: "POST",
      headers: { Cookie: cookie, "Content-Type": "application/zip", "X-Filename": "project.zip" },
      body: new Uint8Array([1, 2, 3]),
    });
    assert.equal(response.status, 201);
    assert.deepEqual(await response.json(), { uploadId: "upload-id", filename: "project.zip", size: 3 });
    assert.equal(calls[0][0], "uploadZip");
  });
});


test("UI serves the bundle without exposing arbitrary files", async () => {
  await withServer(async (baseUrl) => {
    assert.equal((await fetch(`${baseUrl}/`)).status, 200);
    assert.equal((await fetch(`${baseUrl}/api/unknown`)).status, 401);
    const traversal = await fetch(`${baseUrl}/assets/%2e%2e/%2e%2e/etc/passwd`);
    assert.notEqual(await traversal.text(), "root:x:0:0");
  });
});
