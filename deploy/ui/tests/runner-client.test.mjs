import assert from "node:assert/strict";
import test from "node:test";

import { RunnerClientError, createRunnerClient } from "../runner-client.mjs";


test("runner token stays in the server-side request", async () => {
  const seen = [];
  const client = createRunnerClient({
    baseUrl: "https://runner.internal:8787",
    token: "runner-secret",
    fetchImpl: async (url, options) => {
      seen.push({ url, options });
      return Response.json({ ready: true });
    },
  });

  const result = await client.ready();

  assert.equal(seen[0].options.headers.Authorization, "Bearer runner-secret");
  assert.equal(JSON.stringify(result).includes("runner-secret"), false);
});


test("runner client uses the fixed API paths", async () => {
  const seen = [];
  const client = createRunnerClient({
    baseUrl: "https://runner.internal:8787/",
    token: "secret",
    fetchImpl: async (url, options) => {
      seen.push([url, options.method]);
      return Response.json({ ok: true });
    },
  });

  await client.start({ type: "website" });
  await client.list();
  await client.status("scan/id");
  await client.stop("scan/id");
  await client.report("scan/id");
  await client.downloadReport("scan/id");
  await client.createBatch({ items: [], quickScan: true, authorized: true });
  await client.listBatches();
  await client.batchStatus("batch/id");
  await client.cancelBatch("batch/id");

  assert.deepEqual(seen, [
    ["https://runner.internal:8787/v1/scans", "POST"],
    ["https://runner.internal:8787/v1/scans", "GET"],
    ["https://runner.internal:8787/v1/scans/scan%2Fid", "GET"],
    ["https://runner.internal:8787/v1/scans/scan%2Fid/cancel", "POST"],
    ["https://runner.internal:8787/v1/scans/scan%2Fid/report", "GET"],
    ["https://runner.internal:8787/v1/scans/scan%2Fid/report/download", "GET"],
    ["https://runner.internal:8787/v1/batches", "POST"],
    ["https://runner.internal:8787/v1/batches", "GET"],
    ["https://runner.internal:8787/v1/batches/batch%2Fid", "GET"],
    ["https://runner.internal:8787/v1/batches/batch%2Fid/cancel", "POST"],
  ]);
});


test("runner client downloads markdown without parsing it as JSON", async () => {
  const client = createRunnerClient({
    baseUrl: "https://runner.internal:8787",
    token: "secret",
    fetchImpl: async (_url, options) => {
      assert.equal(options.headers.Authorization, "Bearer secret");
      return new Response("# Report\n", {
        headers: {
          "Content-Type": "text/markdown; charset=utf-8",
          "Content-Disposition": 'attachment; filename="strix-report-scan-id.md"',
        },
      });
    },
  });

  const report = await client.downloadReport("scan-id");
  assert.equal(report.contentType, "text/markdown; charset=utf-8");
  assert.equal(report.contentDisposition, 'attachment; filename="strix-report-scan-id.md"');
  assert.equal(await report.body.text(), "# Report\n");
});


test("runner client streams ZIP uploads with a filename and size", async () => {
  const seen = [];
  const client = createRunnerClient({
    baseUrl: "https://runner.internal:8787",
    token: "secret",
    fetchImpl: async (url, options) => {
      seen.push({ url, options });
      return Response.json({ uploadId: "upload-id", filename: "project.zip", size: 4 });
    },
  });

  const result = await client.uploadZip(new Uint8Array([1, 2, 3, 4]), {
    filename: "project.zip",
    contentLength: 4,
  });

  assert.deepEqual(result, { uploadId: "upload-id", filename: "project.zip", size: 4 });
  assert.equal(seen[0].url, "https://runner.internal:8787/v1/uploads");
  assert.equal(seen[0].options.headers["X-Filename"], "project.zip");
  assert.equal(seen[0].options.headers["Content-Length"], "4");
});


test("runner client maps connection and non-json failures safely", async () => {
  const unavailable = createRunnerClient({
    baseUrl: "https://runner.internal:8787",
    token: "secret",
    fetchImpl: async () => {
      throw new Error("connect ECONNREFUSED with secret");
    },
  });
  await assert.rejects(
    unavailable.ready(),
    (error) => error instanceof RunnerClientError && error.status === 503,
  );

  const broken = createRunnerClient({
    baseUrl: "https://runner.internal:8787",
    token: "secret",
    fetchImpl: async () => new Response("upstream secret", { status: 502 }),
  });
  await assert.rejects(
    broken.ready(),
    (error) => error instanceof RunnerClientError && !error.message.includes("secret"),
  );
});
