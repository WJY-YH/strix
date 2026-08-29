const PHASE_LABELS = {
  preparing: "正在准备扫描",
  scanning: "正在执行安全检查",
  analyzing: "正在分析检测结果",
  reporting: "正在生成修复建议",
  complete: "扫描完成",
  findings: "发现需要处理的问题",
  failed: "扫描未完成",
  stopped: "体检已停止",
};


function elapsedText(startedAt, now = Date.now()) {
  const started = Date.parse(startedAt || "");
  if (!Number.isFinite(started)) return "已运行时间未知";
  const seconds = Math.max(0, Math.floor((now - started) / 1000));
  if (seconds < 60) return `已运行 ${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  return `已运行 ${minutes} 分钟 ${seconds % 60} 秒`;
}


export function safeReportFilename(scanId) {
  const safeId = String(scanId || "report").replace(/[^A-Za-z0-9_-]/g, "-");
  return `strix-report-${safeId}.md`;
}


export function formatScanProgress(scan, now = Date.now()) {
  const phase = String(scan?.phase || scan?.status || "preparing");
  const total = Number(scan?.phaseTotal) > 0 ? Number(scan.phaseTotal) : 4;
  const index = Math.min(total, Math.max(1, Number(scan?.phaseIndex) || 1));
  return {
    phase,
    label: String(scan?.message || PHASE_LABELS[phase] || "正在处理"),
    count: `${index}/${total}`,
    elapsed: elapsedText(scan?.startedAt, now),
    updated: scan?.updatedAt ? `最近更新：${new Date(scan.updatedAt).toISOString().slice(11, 19)}` : "",
  };
}


export function rewriteZipScanBody(body, uploadId) {
  const payload = typeof body === "string" ? JSON.parse(body) : { ...body };
  return JSON.stringify({ ...payload, type: "local_code", target: uploadId });
}


export function normalizeBatchItems(rows, uploadIdForFile = (file) => file.uploadId) {
  const items = [];
  for (const row of Array.isArray(rows) ? rows : []) {
    const type = String(row?.type || "");
    if (type === "local_code") {
      for (const file of Array.isArray(row.files) ? row.files : []) {
        const uploadId = uploadIdForFile(file);
        if (uploadId) items.push({ type, target: String(uploadId) });
      }
      continue;
    }
    const target = String(row?.target || "").trim();
    if ((type === "website" || type === "repository") && target) {
      items.push({ type, target });
    }
  }
  return items;
}


export function formatBatchProgress(batch) {
  const total = Math.max(0, Number(batch?.total) || 0);
  const completed = Math.min(total, Math.max(0, Number(batch?.completed) || 0));
  const current = Array.isArray(batch?.items)
    ? batch.items.find((item) => item?.status === "running")
    : null;
  const totalPhases = Number(current?.phaseTotal) > 0 ? Number(current.phaseTotal) : 4;
  const phaseIndex = Math.min(totalPhases, Math.max(1, Number(current?.phaseIndex) || 1));
  return {
    count: `${completed}/${total}`,
    current: String(current?.message || (batch?.status === "queued" ? "等待开始" : "批次处理完成")),
    phase: current ? `${phaseIndex}/${totalPhases}` : "-/-",
  };
}


function statusLabel(scan) {
  if (scan.status === "findings") return "发现问题";
  if (scan.status === "complete") return "已完成";
  if (scan.status === "failed") return "未完成";
  if (scan.status === "stopped") return "已停止";
  return "进行中";
}


function renderHistory(scans) {
  const section = document.querySelector(".history-section");
  if (!section) return;
  const title = section.querySelector("#history-title");
  if (title) title.textContent = "体检历史";
  const headingButton = section.querySelector(".section-heading > button");
  if (headingButton) headingButton.textContent = scans.length ? `${scans.length} 条记录` : "暂无记录";
  section.querySelectorAll(".history-row").forEach((row) => row.remove());
  if (!scans.length) return;

  const fragment = document.createDocumentFragment();
  for (const scan of scans.slice(0, 7)) {
    const row = document.createElement("div");
    row.className = "history-row";
    const target = document.createElement("div");
    target.className = "history-target";
    const targetText = document.createElement("strong");
    targetText.textContent = scan.target || "未记录目标";
    const targetMeta = document.createElement("small");
    targetMeta.textContent = scan.finishedAt ? new Date(scan.finishedAt).toLocaleString("zh-CN") : "正在运行";
    target.append(targetText, targetMeta);
    const status = document.createElement("div");
    status.className = "status-pill";
    status.textContent = statusLabel(scan);
    const summary = document.createElement("div");
    summary.className = "issue-summary";
    summary.textContent = scan.message || PHASE_LABELS[scan.phase] || "";
    const action = document.createElement("a");
    action.className = "secondary-button";
    action.textContent = "下载 Markdown";
    action.href = `/api/scans/${encodeURIComponent(scan.id)}/report/download`;
    if (["complete", "findings"].includes(scan.status)) action.download = safeReportFilename(scan.id);
    else {
      action.removeAttribute("href");
      action.setAttribute("aria-disabled", "true");
    }
    row.append(target, status, summary, action);
    fragment.append(row);
  }
  section.append(fragment);
}


function updateProgress(scans) {
  const running = scans.find((scan) => scan.status === "running");
  const progress = document.querySelector(".scan-progress");
  if (!running || !progress) return;
  const formatted = formatScanProgress(running);
  const title = progress.querySelector(".progress-title strong");
  const count = progress.querySelector(".progress-title span");
  const detail = progress.querySelector(".progress-title")?.nextElementSibling?.nextElementSibling;
  if (title) title.textContent = formatted.label;
  if (count) count.textContent = formatted.count;
  if (detail) detail.textContent = `${formatted.elapsed}${formatted.updated ? ` · ${formatted.updated}` : ""}`;
  const bar = progress.querySelector(".progress-track span");
  if (bar) bar.style.width = `${Number(running.phaseIndex || 1) / Number(running.phaseTotal || 4) * 100}%`;
  window.__strixReportId = running.id;
}


async function refresh() {
  try {
    const response = await fetch("/api/scans", { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    const scans = Array.isArray(payload.scans) ? payload.scans : [];
    renderHistory(scans);
    updateProgress(scans);
    const latest = scans.find((scan) => ["complete", "findings"].includes(scan.status));
    if (latest) window.__strixReportId = latest.id;
  } catch {
    // Login state and Runner availability are handled by the main UI.
  }
}


function installZipUpload() {
  if (window.__strixZipUploadInstalled) return;
  const targetGroup = document.querySelector('[role="group"]');
  const targetInput = document.querySelector('input[placeholder="https://你的站点.com"], input[placeholder*="你的账号"]');
  if (!targetGroup || !targetInput) return;

  const zipButton = document.createElement("button");
  zipButton.type = "button";
  zipButton.className = "strix-zip-button";
  zipButton.textContent = "上传 ZIP 代码";
  zipButton.setAttribute("aria-pressed", "false");
  targetGroup.append(zipButton);

  const panel = document.createElement("div");
  panel.className = "strix-zip-panel";
  panel.hidden = true;
  panel.innerHTML = `
    <label class="strix-zip-picker">
      <span>选择本地 ZIP 文件</span>
      <input type="file" accept=".zip,application/zip" aria-label="选择 ZIP 文件">
    </label>
    <small class="strix-zip-status">只上传源码压缩包，最大 100 MB。扫描结束后自动删除源码。</small>
  `;
  const panelAnchor = targetInput.closest("label") || targetInput.parentElement;
  panelAnchor?.after(panel);
  const fileInput = panel.querySelector('input[type="file"]');
  const status = panel.querySelector(".strix-zip-status");
  const state = { file: null, uploadId: null };

  const setMode = (enabled) => {
    window.__strixZipMode = enabled;
    zipButton.setAttribute("aria-pressed", String(enabled));
    panel.hidden = !enabled;
    const targetLabel = targetInput.closest("label");
    if (targetLabel) targetLabel.hidden = enabled;
    if (enabled) {
      targetInput.value = "https://github.com/WJY-YH/strix";
      targetInput.dispatchEvent(new Event("input", { bubbles: true }));
      targetInput.dispatchEvent(new Event("change", { bubbles: true }));
    }
  };
  zipButton.addEventListener("click", () => setMode(!window.__strixZipMode));
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.('[role="group"] button');
    if (button && button !== zipButton) setMode(false);
  });
  fileInput.addEventListener("change", () => {
    state.file = fileInput.files?.[0] || null;
    state.uploadId = null;
    if (!state.file) {
      status.textContent = "只上传源码压缩包，最大 100 MB。扫描结束后自动删除源码。";
      return;
    }
    status.textContent = `${state.file.name}（${Math.ceil(state.file.size / 1024 / 1024)} MB），开始体检时上传。`;
  });

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input?.url || "";
    if (window.__strixZipMode && url.endsWith("/api/scans") && init.method === "POST" && !state.file) {
      status.textContent = "请先选择 ZIP 文件。";
      return new Response(JSON.stringify({ error: "upload_required", message: "请先选择 ZIP 文件。" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (window.__strixZipMode && state.file && url.endsWith("/api/scans") && init.method === "POST") {
      if (!state.uploadId) {
        status.textContent = "正在上传 ZIP…";
        const upload = await originalFetch("/api/uploads", {
          method: "POST",
          headers: { "Content-Type": "application/zip", "X-Filename": state.file.name },
          body: state.file,
        });
        if (!upload.ok) {
          status.textContent = "ZIP 上传失败，请检查文件大小和格式。";
          return upload;
        }
        const payload = await upload.json();
        state.uploadId = payload.uploadId;
        status.textContent = "ZIP 已上传，正在创建扫描…";
      }
      init = { ...init, body: rewriteZipScanBody(init.body, state.uploadId) };
    }
    return originalFetch(input, init);
  };
  window.__strixZipUploadInstalled = true;
}


function installBatchScan() {
  if (window.__strixBatchInstalled) return;
  const targetGroup = document.querySelector('[role="group"]');
  if (!targetGroup) return;

  const button = document.createElement("button");
  button.type = "button";
  button.className = "strix-batch-button";
  button.textContent = "批量检查";
  targetGroup.append(button);

  const panel = document.createElement("section");
  panel.className = "strix-batch-panel";
  panel.hidden = true;
  panel.innerHTML = `
    <div class="strix-batch-heading"><strong>批量检查</strong><small>网站、GitHub 和 ZIP 会按顺序扫描</small></div>
    <div class="strix-batch-rows"></div>
    <div class="strix-batch-actions">
      <button type="button" class="strix-batch-add">添加目标</button>
      <button type="button" class="strix-batch-start">开始批量检查</button>
      <button type="button" class="strix-batch-cancel" hidden>停止批次</button>
    </div>
    <div class="strix-batch-status" role="status"></div>
  `;
  targetGroup.parentElement?.append(panel);
  const rowsContainer = panel.querySelector(".strix-batch-rows");
  const status = panel.querySelector(".strix-batch-status");
  const addButton = panel.querySelector(".strix-batch-add");
  const startButton = panel.querySelector(".strix-batch-start");
  const cancelButton = panel.querySelector(".strix-batch-cancel");
  button.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
  });
  let batchId = null;
  let timer = null;

  const addRow = (type = "website") => {
    const row = document.createElement("div");
    row.className = "strix-batch-row";
    row.innerHTML = `
      <select aria-label="批量目标类型">
        <option value="website">网站</option>
        <option value="repository">GitHub 仓库</option>
        <option value="local_code">ZIP 文件</option>
      </select>
      <input aria-label="批量目标地址" placeholder="https://example.com">
      <input aria-label="批量 ZIP 文件" type="file" accept=".zip,application/zip" multiple hidden>
      <button type="button" class="strix-batch-remove">删除</button>
    `;
    const select = row.querySelector("select");
    const textInput = row.querySelector('input[type="text"], input:not([type])');
    const fileInput = row.querySelector('input[type="file"]');
    const updateType = () => {
      const zip = select.value === "local_code";
      textInput.hidden = zip;
      fileInput.hidden = !zip;
      textInput.required = !zip;
    };
    select.value = type;
    select.addEventListener("change", updateType);
    row.querySelector(".strix-batch-remove").addEventListener("click", () => row.remove());
    updateType();
    rowsContainer.append(row);
  };

  const readRows = () => Array.from(rowsContainer.querySelectorAll(".strix-batch-row")).map((row) => ({
    type: row.querySelector("select").value,
    target: row.querySelector('input[type="text"], input:not([type])').value,
    files: Array.from(row.querySelector('input[type="file"]').files || []),
  }));

  const upload = async (file) => {
    const response = await fetch("/api/uploads", {
      method: "POST",
      headers: { "Content-Type": "application/zip", "X-Filename": file.name },
      body: file,
    });
    if (!response.ok) throw new Error("ZIP 上传失败");
    return (await response.json()).uploadId;
  };

  const render = (batch) => {
    const progress = formatBatchProgress(batch);
    status.textContent = `总进度 ${progress.count} · 当前：${progress.current} · 阶段 ${progress.phase}`;
    const links = batch.items
      .filter((item) => item.scanId && ["complete", "findings"].includes(item.status))
      .map((item) => `<a href="/api/scans/${encodeURIComponent(item.scanId)}/report/download" download>下载第 ${item.position} 项 Markdown</a>`)
      .join(" · ");
    if (links) status.insertAdjacentHTML("beforeend", ` · ${links}`);
    if (["complete", "findings", "failed", "cancelled"].includes(batch.status)) {
      startButton.disabled = false;
      cancelButton.hidden = true;
      if (timer) window.clearInterval(timer);
      timer = null;
    }
  };

  const refresh = async () => {
    if (!batchId) return;
    const response = await fetch(`/api/batches/${encodeURIComponent(batchId)}`, { cache: "no-store" });
    if (response.ok) render(await response.json());
  };

  addButton.addEventListener("click", () => addRow());
  startButton.addEventListener("click", async () => {
    const authorized = document.querySelector('input[type="checkbox"]')?.checked === true;
    if (!authorized) {
      status.textContent = "请先确认已获得安全测试授权。";
      return;
    }
    try {
      const rows = readRows();
      for (const row of rows) {
        if (row.type === "local_code") {
          const uploaded = [];
          for (const file of row.files) uploaded.push({ ...file, uploadId: await upload(file) });
          row.files = uploaded;
        }
      }
      const items = normalizeBatchItems(rows);
      if (!items.length) {
        status.textContent = "请至少添加一个有效目标。";
        return;
      }
      const response = await fetch("/api/batches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items, quickScan: true, authorized: true }),
      });
      if (!response.ok) throw new Error("批次创建失败");
      const batch = await response.json();
      batchId = batch.id;
      startButton.disabled = true;
      cancelButton.hidden = false;
      render(batch);
      timer = window.setInterval(refresh, 1500);
    } catch {
      status.textContent = "批量检查创建失败，请检查目标和 ZIP 文件。";
    }
  });
  cancelButton.addEventListener("click", async () => {
    if (!batchId) return;
    await fetch(`/api/batches/${encodeURIComponent(batchId)}/cancel`, { method: "POST" });
    await refresh();
  });

  addRow();
  window.__strixBatchInstalled = true;
}


if (typeof document !== "undefined") {
  const zipStyle = document.createElement("style");
  zipStyle.textContent = `
    .strix-zip-button { border: 1px solid #cbd5e1; border-radius: 14px; padding: 12px 18px; background: #fff; color: #1d4ed8; cursor: pointer; font-weight: 600; }
    .strix-zip-button[aria-pressed="true"] { border-color: #2563eb; background: #eff6ff; }
    .strix-zip-panel { margin-top: 12px; border: 1px dashed #93c5fd; border-radius: 16px; padding: 14px; background: #eff6ff; }
    .strix-zip-picker { display: flex; align-items: center; justify-content: space-between; gap: 12px; color: #1e3a8a; font-weight: 600; cursor: pointer; }
    .strix-zip-picker input { max-width: 180px; }
    .strix-zip-status { display: block; margin-top: 8px; color: #64748b; }
    .strix-batch-button { margin-left: 10px; border: 1px solid #2563eb; border-radius: 14px; padding: 12px 18px; background: #eff6ff; color: #1d4ed8; cursor: pointer; font-weight: 600; }
    .strix-batch-panel { margin-top: 16px; border: 1px solid #bfdbfe; border-radius: 16px; padding: 16px; background: #f8fbff; }
    .strix-batch-heading { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; color: #1e3a8a; }
    .strix-batch-heading small { color: #64748b; font-weight: 400; }
    .strix-batch-row { display: grid; grid-template-columns: 140px 1fr auto; gap: 8px; margin-top: 10px; align-items: center; }
    .strix-batch-row select, .strix-batch-row input { min-width: 0; border: 1px solid #cbd5e1; border-radius: 10px; padding: 9px 10px; background: #fff; color: #172554; }
    .strix-batch-row input[type="file"] { padding: 7px; }
    .strix-batch-remove, .strix-batch-add, .strix-batch-cancel { border: 1px solid #cbd5e1; border-radius: 10px; padding: 9px 12px; background: #fff; color: #475569; cursor: pointer; }
    .strix-batch-actions { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
    .strix-batch-start { border: 0; border-radius: 10px; padding: 9px 14px; background: #2563eb; color: #fff; cursor: pointer; font-weight: 600; }
    .strix-batch-start:disabled { opacity: .55; cursor: wait; }
    .strix-batch-status { margin-top: 12px; color: #475569; line-height: 1.6; }
    .strix-batch-status a { color: #1d4ed8; }
    @media (max-width: 680px) { .strix-batch-row { grid-template-columns: 1fr; } .strix-batch-button { margin: 10px 0 0; } }
  `;
  document.head.append(zipStyle);
  window.setInterval(installZipUpload, 300);
  window.setInterval(installBatchScan, 300);
  document.addEventListener("click", async (event) => {
    const button = event.target.closest?.(".download-button");
    const reportId = window.__strixReportId;
    if (!button || !reportId) return;
    event.preventDefault();
    const response = await fetch(`/api/scans/${encodeURIComponent(reportId)}/report/download`);
    if (!response.ok) return;
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = safeReportFilename(reportId);
    link.click();
    URL.revokeObjectURL(link.href);
  });
  window.setInterval(refresh, 1500);
  window.setTimeout(refresh, 0);
}
