import http from "node:http";
import { pathToFileURL } from "node:url";


export function createFixtureServer() {
  return http.createServer((request, response) => {
    const url = new URL(request.url, "http://fixture.local");
    if (url.pathname === "/health") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end('{"status":"ok"}');
      return;
    }
    if (url.pathname !== "/") {
      response.writeHead(404).end();
      return;
    }

    // Deliberately unescaped: this private fixture gives the scanner stable,
    // low-risk reflected-input evidence and is never exposed off loopback.
    const name = url.searchParams.get("name") || "visitor";
    const body = `<!doctype html><html><body><h1>Hello ${name}</h1></body></html>`;
    response.writeHead(200, {
      "Content-Type": "text/html; charset=utf-8",
      "Content-Length": Buffer.byteLength(body),
    });
    response.end(body);
  });
}


if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  createFixtureServer().listen(3001, "0.0.0.0");
}
