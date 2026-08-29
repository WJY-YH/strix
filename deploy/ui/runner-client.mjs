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

  async function request(path, { method = "GET", body } = {}) {
    const options = {
      method,
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(5000),
    };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }

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

  const scanPath = (id) => `/v1/scans/${encodeURIComponent(id)}`;
  return {
    ready: () => request("/ready"),
    start: (body) => request("/v1/scans", { method: "POST", body }),
    status: (id) => request(scanPath(id)),
    stop: (id) => request(`${scanPath(id)}/cancel`, { method: "POST" }),
    report: (id) => request(`${scanPath(id)}/report`),
  };
}
