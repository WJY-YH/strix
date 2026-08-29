#!/usr/bin/env bash
set -euo pipefail

if (($# != 0)); then
  echo "此检查不接受命令行参数。" >&2
  exit 2
fi

: "${STRIX_RUNNER_URL:?必须设置 STRIX_RUNNER_URL}"
: "${STRIX_RUNNER_TOKEN:?必须设置 STRIX_RUNNER_TOKEN}"

acceptance_target="${STRIX_ACCEPTANCE_TARGET:-http://host.docker.internal:3001}"
if [[ "$acceptance_target" != "http://host.docker.internal:3001" ]]; then
  echo "拒绝扫描：验收目标不是固定的私有测试靶场。" >&2
  exit 2
fi

runner_root="${STRIX_RUNNER_URL%/}"
auth_header="Authorization: Bearer ${STRIX_RUNNER_TOKEN}"

health_json="$(curl --fail --silent --show-error "${runner_root}/health")"
jq -e '.status == "ok"' >/dev/null <<<"$health_json"

ready_json="$(curl --fail --silent --show-error -H "$auth_header" "${runner_root}/ready")"
jq -e '.ready == true' >/dev/null <<<"$ready_json"

create_json="$(curl --fail --silent --show-error \
  -H "$auth_header" \
  -H "Content-Type: application/json" \
  -X POST \
  --data '{"type":"website","target":"http://host.docker.internal:3001","quickScan":true,"authorized":true}' \
  "${runner_root}/v1/scans")"
scan_id="$(jq -er '.id' <<<"$create_json")"

terminal_json=""
for _attempt in {1..120}; do
  status_json="$(curl --fail --silent --show-error \
    -H "$auth_header" \
    "${runner_root}/v1/scans/${scan_id}")"
  status="$(jq -er '.status' <<<"$status_json")"
  case "$status" in
    complete|findings)
      terminal_json="$status_json"
      break
      ;;
    failed|stopped)
      echo "验收扫描未成功完成：${status}" >&2
      exit 1
      ;;
    running)
      sleep 10
      ;;
    *)
      echo "执行器返回了未知状态。" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$terminal_json" ]]; then
  echo "验收扫描在 20 分钟内没有完成。" >&2
  exit 1
fi

report_json="$(curl --fail --silent --show-error \
  -H "$auth_header" \
  "${runner_root}/v1/scans/${scan_id}/report")"
status="$(jq -er '.status' <<<"$terminal_json")"
exit_code="$(jq -r '.exitCode // "无"' <<<"$terminal_json")"
summary="$(jq -er '.summary' <<<"$report_json")"
printf '验收完成：scan_id=%s status=%s exit_code=%s summary=%s\n' \
  "$scan_id" "$status" "$exit_code" "$summary"
