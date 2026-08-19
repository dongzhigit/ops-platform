# Spug 私有化部署基线

该目录的 Compose 配置用于第一阶段内部试运行，默认只监听本机回环地址，并要求显式设置所有密钥和数据库口令。

## 启动

```bash
cd docs/docker
cp .env.example .env
openssl rand -base64 48
openssl rand -base64 32
openssl rand -base64 48
openssl rand -hex 16
mkdir -p secrets
openssl rand -hex 32 > secrets/prometheus_discovery_token
openssl rand -hex 32 > secrets/alertmanager_webhook_token
chmod 700 secrets
chmod 444 secrets/prometheus_discovery_token secrets/alertmanager_webhook_token
```

将前四个独立生成的值依次写入 `.env` 的 `SPUG_SECRET_KEY`、`SPUG_CREDENTIAL_MASTER_KEY`、`SPUG_AUDIT_SIGNING_KEY` 和 `SPUG_GUACAMOLE_JSON_SECRET_KEY`。Prometheus 和 Alertmanager 的两个 token 保留在权限为 `0600` 的独立文件中。随后填写数据库口令、正式域名和 HTTPS 来源，然后执行：

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

镜像会直接编译当前仓库的 `spug_api` 和 `spug_web`，不会在启动时下载上游版本。更新代码后必须重新执行 `docker compose up -d --build`。运行日志、临时文件和构建仓库分别持久化，应用源码保留在不可变镜像层中。

首次启动后创建管理员：

```bash
docker exec spug init_spug admin '请替换为高强度密码'
```

资产凭据、对象授权、旧私钥迁移和主密钥轮换说明见 [M1 资产、凭据与对象授权](../M1_ASSET_ACCESS.md)，高风险操作流程见 [M1 高风险操作审批与审计](../M1_APPROVAL_AUDIT.md)，RDP/VNC 网关配置和当前边界见 [M3 RDP/VNC 安全接入](../M3_REMOTE_DESKTOP.md)，Prometheus/Alertmanager 监控闭环见 [M4 主机监控与告警闭环](../M4_OBSERVABILITY.md)，知识库与只读 AI 调查见 [M5 知识库与只读 AI 运维](../M5_KNOWLEDGE_AIOPS.md)。

## 启用只读 AI 调查

AI 默认关闭。初始化管理员后，推荐进入“知识与 AI → AI 运维 → 模型配置”，通过受 `aiops.config.manage` 保护的页面保存模型地址、模型名称、限制参数和 API Key。API Key 使用凭据主密钥加密后落库，页面和 API 均不会回显明文；配置保存后即时生效，无需重启容器。

环境变量仍可作为首次启动或灾难恢复的回退配置。选择可信的 OpenAI-compatible Chat Completions 服务后，将 `.env` 中的 `SPUG_AIOPS_ENABLED` 改为 `true`，填写 `SPUG_AIOPS_BASE_URL` 和 `SPUG_AIOPS_MODEL`，再创建独立密钥文件：

```bash
printf '%s' '替换为独立的模型服务密钥' > secrets/aiops_api_key
chmod 400 secrets/aiops_api_key
```

同时设置 `SPUG_AIOPS_API_KEY_FILE=/run/spug-secrets/aiops_api_key`。生产模式下，无论页面还是环境配置都要求模型地址使用 HTTPS。若兼容服务不支持 `response_format=json_object`，可关闭页面中的 JSON Object 模式或设置 `SPUG_AIOPS_JSON_MODE=false`；后端仍会严格解析和校验 JSON，并按响应字节上限流式限制响应体。不要把模型密钥写入镜像、Git 或浏览器持久存储。

调查时会把当前用户有权访问的主机基础信息、指标、告警摘要和已发布知识片段发送给所配置的模型服务。使用外部供应商前必须确认数据分类、跨境、保留与审计要求；敏感环境优先使用受控内网模型服务。当前模型没有执行工具，不能运行 Shell、SSH、发布或配置变更。

## 网络与 TLS

- 默认 `SPUG_BIND_HOST=127.0.0.1`，应通过 Caddy、Nginx 或负载均衡器提供 HTTPS。
- 只有在可信内网且有防火墙保护时，才将绑定地址改为 `0.0.0.0`。
- 反向代理必须设置 `X-Forwarded-Proto`，使用 HTTPS 时保持 `SPUG_SECURE_COOKIES=true`。
- 外层反向代理必须关闭或脱敏 `/api/v1/gateway/sessions/*/launch/` 与 `/guacamole/` 的查询字符串日志。
- Prometheus、Alertmanager 和 exporter 不应发布公网端口；限制监控网络只能访问批准的采集目标。
- 限制 Spug 到模型服务的出站地址；模型接口不得回连资产网络，也不得复用应用、数据库或监控密钥。
- 若由外部代理完成 HTTPS 跳转，`SPUG_SSL_REDIRECT` 可保持 `false`，避免配置错误造成循环重定向。

## 权限说明

批量文件分发依赖 SSHFS，因此容器需要 `/dev/fuse` 和 `SYS_ADMIN` capability。这里没有使用完整的 `privileged` 模式。若不需要 SSHFS 文件分发，可删除 `cap_add`、`devices` 和 `apparmor:unconfined`，进一步收紧权限。

当前容器基线面向 Linux 主机；Docker Desktop 通常不提供可直接映射的 `/dev/fuse`。在 macOS/Windows 上仅做界面开发时，可删除上述 FUSE 配置并暂不使用 SSHFS 文件分发。

M0 镜像仍依赖 CentOS 7/Python 3.6，并临时锁定为 `linux/amd64`；ARM 主机会通过模拟运行，性能不适合作为正式生产方案。Dockerfile 使用归档软件源保证过渡期可构建，后续必须迁移到仍受安全支持的操作系统、Python 和 Django 版本。

## 上线前检查

- `.env` 不得提交到 Git。
- `secrets/` 不得提交到 Git，目录权限为 `0700`，两个只读监控 token 与可选 AI 模型密钥彼此独立。
- 域名和 TLS 证书已配置，公网不能直接访问数据库和 Redis。
- 已执行数据库备份和恢复演练。
- `SPUG_CREDENTIAL_MASTER_KEY` 已独立备份并完成凭据恢复演练。
- 管理员启用独立强密码，不与主机或数据库复用。
- 已用防火墙限制 guacd 只能访问批准的 RDP/VNC 目标网段和端口。
- `docker compose ps` 中数据库、Guacamole 和 Spug 服务状态正常。
- `docker compose ps` 中 Prometheus 和 Alertmanager 健康，服务发现接口与 Webhook 对错误 token 返回 401。
