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


if (typeof document !== "undefined") {
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
