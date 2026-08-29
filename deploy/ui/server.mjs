import { createHmac, timingSafeEqual } from "node:crypto";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import http from "node:http";
import { extname, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { RunnerClientError, createRunnerClient } from "./runner-client.mjs";


const COOKIE_NAME = "strix_ui_session";
const MAX_BODY_BYTES = 32 * 1024;
const MIME_TYPES = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".png", "image/png"],
  [".svg", "image/svg+xml"],
]);


function safeEqual(left, right) {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
}


function sessionValue(accessToken) {
  return createHmac("sha256", accessToken)
    .update("strix-ui-session-v1")
    .digest("base64url");
}


function readCookie(request, name) {
  for (const part of String(request.headers.cookie || "").split(";")) {
    const [key, ...value] = part.trim().split("=");
    if (key === name) return value.join("=");
  }
  return "";
}


function sendJson(response, status, payload, extraHeaders = {}) {
  const body = Buffer.from(JSON.stringify(payload));
  response.writeHead(status, {
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": body.length,
    "X-Content-Type-Options": "nosniff",
    ...extraHeaders,
  });
  response.end(body);
}


async function sendDownload(response, status, download) {
  const body = Buffer.from(await download.body.arrayBuffer());
  response.writeHead(status, {
    "Cache-Control": "no-store",
    "Content-Type": download.contentType || "text/markdown; charset=utf-8",
    "Content-Disposition": download.contentDisposition || "attachment; filename=report.md",
    "Content-Length": body.length,
    "X-Content-Type-Options": "nosniff",
  });
  response.end(body);
}


async function readJson(request) {
  const declared = Number(request.headers["content-length"] || 0);
  if (!Number.isSafeInteger(declared) || declared <= 0 || declared > MAX_BODY_BYTES) {
    throw new Error("invalid_body");
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw new Error("invalid_body");
    chunks.push(chunk);
  }
  const payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("invalid_body");
  }
  return payload;
}


function runnerFailure(response, error) {
  if (error instanceof RunnerClientError) {
    sendJson(response, error.status, {
      error: error.code,
      message: error.message,
    });
    return;
  }
  sendJson(response, 503, {
    error: "runner_unavailable",
    message: "执行器暂不可用，请稍后重试。",
  });
}


async function serveStatic(response, clientDir, pathname) {
  const root = resolve(clientDir);
  let relativePath;
  try {
    relativePath = decodeURIComponent(pathname === "/" ? "/index.html" : pathname);
  } catch {
    response.writeHead(400).end();
    return;
  }
  let candidate = resolve(root, `.${relativePath}`);
  if (candidate !== root && !candidate.startsWith(`${root}${sep}`)) {
    response.writeHead(404).end();
    return;
  }
  try {
    if (!(await stat(candidate)).isFile()) throw new Error("not_file");
  } catch {
    if (pathname.startsWith("/assets/")) {
      response.writeHead(404).end();
      return;
    }
    candidate = resolve(root, "index.html");
  }
  response.writeHead(200, {
    "Cache-Control": candidate.endsWith("index.html") ? "no-cache" : "public, max-age=31536000, immutable",
    "Content-Type": MIME_TYPES.get(extname(candidate)) || "application/octet-stream",
    "X-Content-Type-Options": "nosniff",
  });
  createReadStream(candidate).pipe(response);
}


export function createUiServer({ accessToken, runnerClient, clientDir }) {
  const expectedSession = sessionValue(accessToken);
  return http.createServer(async (request, response) => {
    const url = new URL(request.url, "http://localhost");
    const pathname = url.pathname;

    if (pathname === "/api/session" && request.method === "POST") {
      try {
        const body = await readJson(request);
        if (!safeEqual(String(body.token || ""), accessToken)) {
          sendJson(response, 401, { error: "unauthorized", message: "访问密码不正确。" });
          return;
        }
      } catch {
        sendJson(response, 400, { error: "invalid_request", message: "请求内容无效。" });
        return;
      }
      sendJson(response, 200, { ok: true }, {
        "Set-Cookie": `${COOKIE_NAME}=${expectedSession}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=43200`,
      });
      return;
    }

    if (pathname.startsWith("/api/")) {
      const suppliedSession = readCookie(request, COOKIE_NAME);
      if (!safeEqual(suppliedSession, expectedSession)) {
        sendJson(response, 401, { error: "unauthorized", message: "请先登录。" });
        return;
      }
      try {
        if (pathname === "/api/preflight" && request.method === "GET") {
          sendJson(response, 200, await runnerClient.ready());
          return;
        }
        if (pathname === "/api/scans" && request.method === "GET") {
          sendJson(response, 200, await runnerClient.list());
          return;
        }
        if (pathname === "/api/scans" && request.method === "POST") {
          sendJson(response, 202, await runnerClient.start(await readJson(request)));
          return;
        }
        const report = pathname.match(/^\/api\/scans\/([^/]+)\/report$/);
        if (report && request.method === "GET") {
          sendJson(response, 200, await runnerClient.report(decodeURIComponent(report[1])));
          return;
        }
        const reportDownload = pathname.match(/^\/api\/scans\/([^/]+)\/report\/download$/);
        if (reportDownload && request.method === "GET") {
          await sendDownload(
            response,
            200,
            await runnerClient.downloadReport(decodeURIComponent(reportDownload[1])),
          );
          return;
        }
        const stop = pathname.match(/^\/api\/scans\/([^/]+)\/stop$/);
        if (stop && request.method === "POST") {
          sendJson(response, 200, await runnerClient.stop(decodeURIComponent(stop[1])));
          return;
        }
        const status = pathname.match(/^\/api\/scans\/([^/]+)$/);
        if (status && request.method === "GET") {
          sendJson(response, 200, await runnerClient.status(decodeURIComponent(status[1])));
          return;
        }
        sendJson(response, 404, { error: "not_found", message: "接口不存在。" });
      } catch (error) {
        runnerFailure(response, error);
      }
      return;
    }

    if (request.method !== "GET" && request.method !== "HEAD") {
      response.writeHead(405).end();
      return;
    }
    await serveStatic(response, clientDir, pathname);
  });
}


async function main() {
  const accessToken = process.env.STRIX_UI_ACCESS_TOKEN;
  const runnerUrl = process.env.STRIX_RUNNER_URL;
  const runnerToken = process.env.STRIX_RUNNER_TOKEN;
  if (!accessToken || !runnerUrl || !runnerToken) {
    throw new Error("STRIX_UI_ACCESS_TOKEN, STRIX_RUNNER_URL and STRIX_RUNNER_TOKEN are required");
  }
  const clientDir = fileURLToPath(new URL("./dist/client", import.meta.url));
  const runnerClient = createRunnerClient({ baseUrl: runnerUrl, token: runnerToken });
  const server = createUiServer({ accessToken, runnerClient, clientDir });
  const port = Number(process.env.PORT || 8080);
  server.listen(port, "0.0.0.0");
}


if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
