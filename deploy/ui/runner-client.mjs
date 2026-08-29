export class RunnerClientError extends Error {
  constructor(status, code, message, payload = null) {
    super(message);
    this.name = "RunnerClientError";
    this.status = status;
    this.code = code;
    this.payload = payload;
  }
}


export function createRunnerClient({ baseUrl, token, fetchImpl = fetch }) {
  const root = baseUrl.replace(/\/+$/, "");

  function requestOptions(method, body) {
    const options = {
      method,
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(5000),
    };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return options;
  }

  async function request(path, { method = "GET", body } = {}) {
    const options = requestOptions(method, body);

    let response;
    try {
      response = await fetchImpl(`${root}${path}`, options);
    } catch {
      throw new RunnerClientError(
        503,
        "runner_unavailable",
        "执行器暂不可用，请稍后重试。",
      );
    }

    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new RunnerClientError(
        response.ok ? 503 : response.status,
        "runner_error",
        "执行器返回了无效响应。",
      );
    }
    if (!response.ok) {
      const safePayload = payload && typeof payload === "object" ? payload : null;
      throw new RunnerClientError(
        response.status,
        String(safePayload?.error || "runner_error"),
        String(safePayload?.message || "执行器请求失败。"),
        safePayload,
      );
    }
    return payload;
  }

  async function requestBinary(path) {
    let response;
    try {
      response = await fetchImpl(`${root}${path}`, requestOptions("GET"));
    } catch {
      throw new RunnerClientError(503, "runner_unavailable", "执行器暂不可用，请稍后重试。");
    }
    if (!response.ok) {
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        // Keep a safe generic error for non-JSON upstream failures.
      }
      const safePayload = payload && typeof payload === "object" ? payload : null;
      throw new RunnerClientError(
        response.status,
        String(safePayload?.error || "runner_error"),
        String(safePayload?.message || "执行器请求失败。"),
        safePayload,
      );
    }
    return {
      body: new Response(await response.arrayBuffer(), {
        headers: { "Content-Type": response.headers.get("content-type") || "application/octet-stream" },
      }),
      contentType: response.headers.get("content-type") || "application/octet-stream",
      contentDisposition: response.headers.get("content-disposition") || "",
      contentLength: response.headers.get("content-length") || "",
    };
  }

  async function uploadZip(body, { filename, contentLength }) {
    let response;
    try {
      response = await fetchImpl(`${root}/v1/uploads`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/zip",
          "Content-Length": String(contentLength),
          "X-Filename": filename,
        },
        body,
        duplex: "half",
        signal: AbortSignal.timeout(120000),
      });
    } catch {
      throw new RunnerClientError(503, "runner_unavailable", "执行器暂不可用，请稍后重试。");
    }
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new RunnerClientError(response.ok ? 503 : response.status, "runner_error", "执行器返回了无效响应。");
    }
    if (!response.ok) {
      const safePayload = payload && typeof payload === "object" ? payload : null;
      throw new RunnerClientError(
        response.status,
        String(safePayload?.error || "runner_error"),
        String(safePayload?.message || "上传失败。"),
        safePayload,
      );
    }
    return payload;
  }

  const scanPath = (id) => `/v1/scans/${encodeURIComponent(id)}`;
  return {
    ready: () => request("/ready"),
    uploadZip,
    start: (body) => request("/v1/scans", { method: "POST", body }),
    list: () => request("/v1/scans"),
    status: (id) => request(scanPath(id)),
    stop: (id) => request(`${scanPath(id)}/cancel`, { method: "POST" }),
    report: (id) => request(`${scanPath(id)}/report`),
    downloadReport: (id) => requestBinary(`${scanPath(id)}/report/download`),
  };
}
