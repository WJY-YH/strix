# Strix 私有 Runner 部署手册

这套部署只允许先扫描私有测试靶场。完成本页所有验收前，不得扫描真实业务或第三方系统。

## 1. 核对服务器

在腾讯 Ashburn 专用服务器执行：

```bash
. /etc/os-release
test "$ID" = ubuntu
test "${VERSION_ID%%.*}" -ge 22
docker version
kubectl get nodes -o wide
kubectl get pods -A -o wide
```

不满足 Ubuntu 22+、Docker 正常、能查看 k3s 节点和 Pod 时立即停止。记录该节点的私网 IP。根据 `environment-6a91f5e63bf3ef23ef4d4e1a` 实际 Pod IP，确认并记录真实 Pod CIDR；不要猜地址段。

## 2. 建立持久目录和配置

```bash
sudo install -d -m 0700 -o 10001 -g 10001 /srv/strix/data
sudo install -d -m 0700 /etc/strix
sudo install -m 0600 deploy/runner.env.example /etc/strix/runner.env
sudoedit /etc/strix/runner.env
```

手工填写：

- `STRIX_RUNNER_BIND`：上一步确认的节点私网 IP。
- `STRIX_RUNNER_TOKEN`：新生成的长随机值。
- `LLM_API_KEY`：模型服务密钥。
- `STRIX_LLM`：实际使用的模型名。
- `STRIX_REPORT_RETENTION_DAYS=7`：报告保留 7 天，过期的已结束扫描会自动清理；运行中的扫描不会清理。

文件中不能保留 `change-before-start`。不要把密钥贴到工单、日志或命令历史。

## 3. 只向 Pod 网段开放 Runner

下面的 `NODE_PRIVATE_IP` 和 `POD_CIDR` 必须替换为刚刚核对的真实值：

```bash
export NODE_PRIVATE_IP="核对后的节点私网IP"
export POD_CIDR="核对后的环境Pod网段"
sudo ufw deny 8787/tcp
sudo ufw allow from "$POD_CIDR" to "$NODE_PRIVATE_IP" port 8787 proto tcp
sudo ufw status numbered
```

从外部网络访问节点公网 IP 的 8787 端口必须失败。从指定 Pod 网段访问私网 IP 的 8787 才能成功。

## 4. 启动固定版本镜像

只使用 CI 已通过的完整提交 SHA：

```bash
export STRIX_IMAGE_TAG="完整提交SHA"
export DOCKER_GID="$(stat -c '%g' /var/run/docker.sock)"
docker pull "ghcr.io/wjy-yh/strix-runner:${STRIX_IMAGE_TAG}"
docker pull "ghcr.io/wjy-yh/strix-test-target:${STRIX_IMAGE_TAG}"
docker pull ghcr.io/usestrix/strix-sandbox:1.3.0
docker compose -f deploy/compose.runner.yml config
docker compose -f deploy/compose.runner.yml up -d
```

测试靶场只能映射到 `127.0.0.1:3001`。Compose 输出中如出现其他绑定地址，立即停止。

## 5. 核对 Runner

在服务器本机临时载入受保护的配置，再检查健康和就绪状态：

```bash
set -a
. /etc/strix/runner.env
set +a
curl --fail --silent "http://${STRIX_RUNNER_BIND}:8787/health" | jq
curl --fail --silent \
  -H "Authorization: Bearer ${STRIX_RUNNER_TOKEN}" \
  "http://${STRIX_RUNNER_BIND}:8787/ready" | jq
```

`ready` 必须为 `true`，CLI、Docker、隔离镜像、模型和磁盘必须全部显示完成。

## 6. 配置 Zeabur 网页

这一步会把 Runner 地址和令牌传到 Zeabur。执行前再次取得操作确认。在 Zeabur 私有变量中设置：

- `STRIX_RUNNER_URL=http://节点私网IP:8787`
- `STRIX_RUNNER_TOKEN=与服务器相同的随机值`
- `STRIX_UI_ACCESS_TOKEN=另一个新生成的长随机值`

部署 `ghcr.io/wjy-yh/strix-ui:完整提交SHA`。三个值只能存在服务端变量中，不能放进网页源码。

## 7. 私有靶场验收和重启证明

```bash
export STRIX_RUNNER_URL="http://${STRIX_RUNNER_BIND}:8787"
scripts/acceptance/check-runner.sh
docker compose -f deploy/compose.runner.yml restart runner
curl --fail --silent \
  -H "Authorization: Bearer ${STRIX_RUNNER_TOKEN}" \
  "${STRIX_RUNNER_URL}/v1/scans/刚才的scan_id" | jq
```

重启后必须返回相同终态和报告摘要。另需确认：外网无法连接 8787；未登录网页无法创建扫描或读取报告；报告中没有密钥和原始执行日志。

最终证据需记录完整提交 SHA、三个镜像摘要、CI 链接、Zeabur 部署 ID、私有靶场扫描 ID 与终态、重启结果和外网 8787 拒绝结果，并明确写明“没有扫描真实业务或第三方目标”。

## 8. 历史报告和临时开放网页

Runner 会把扫描状态、阶段和报告写入持久数据目录。网页通过 `GET /api/scans` 显示最近记录；已完成记录可通过 `GET /api/scans/{id}/report/download` 下载 Markdown。Runner 重启后记录仍可读取，报告不依赖浏览器缓存。

不需要查看时，在 Zeabur 的 UI 服务中移除 `strix-security-wjy.zeabur.app` 公网域名即可；不要删除 UI、Runner、测试靶场或持久数据。后台扫描继续运行。需要查看时，再把同一个域名临时绑定回 UI 服务，确认登录口令仍由服务端变量提供。
