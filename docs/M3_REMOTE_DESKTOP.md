# M3 RDP/VNC 安全接入（alpha.9）

本阶段在现有资产身份、对象授权和审计链上增加 Apache Guacamole 1.6.0 网关，提供浏览器 RDP/VNC 的安全启动链路。它是分阶段交付，不代表远程桌面治理已经全部完成。

## 安全边界

- RDP/VNC 身份只能引用加密保存的 `password` 凭据，端点必须引用已绑定到目标主机的同协议身份。
- 用户同时需要页面权限 `host.console.remote` 和对象动作 `rdp.connect` 或 `vnc.connect`。
- 签发启动票据和真正启动时都会检查用户、页面权限、对象授权、端点、身份、凭据、绑定关系及协议。
- 原始启动票据使用 32 字节随机值，默认 60 秒过期且只能消费一次；数据库只保存 SHA-256，不保存原始票据。
- 长期密码只在服务端生成 Guacamole JSON 的瞬间解密，不返回 ops-platform 前端，也不写入会话表或审计事件。
- 端点参数使用协议白名单，禁止用 JSON 覆盖目标主机、端口、用户名、密码、磁盘映射或录像路径。
- 签发、启动、拒绝、失败、过期和关闭元数据都会进入 HMAC 链式审计。
- Scheduler 每 5 分钟把未使用且已过期的启动票据转为 `expired` 并追加审计事件。

Guacamole JSON 本身是短时认证数据，不是 Guacamole 侧强制一次性票据。ops-platform 票据消费后生成的加密 `data` 在 `SPUG_GUACAMOLE_AUTH_TTL`（默认 60 秒）内理论上仍可重放。因此必须使用 HTTPS，并关闭或脱敏所有代理层对以下查询字符串的记录：

```text
/api/v1/gateway/sessions/*/launch/?ticket=...
/guacamole/?data=...
```

仓库内置 Nginx 已对这两个路径关闭访问日志，并对 Guacamole 响应设置 `no-store` 与 `no-referrer`；外层 TLS 代理仍需单独配置。
外层代理还应对启动 URL 配置按来源 IP 的请求速率限制，降低会话 UUID 探测和无效票据造成的审计噪声。

## 启用网关

在 `docs/docker/.env` 中配置：

```bash
SPUG_REMOTE_GATEWAY_ENABLED=true
SPUG_GUACAMOLE_JSON_SECRET_KEY=<openssl rand -hex 16 的输出>
SPUG_GUACAMOLE_PUBLIC_URL=/guacamole
SPUG_REMOTE_TICKET_TTL=60
SPUG_GUACAMOLE_AUTH_TTL=60
```

`SPUG_GUACAMOLE_JSON_SECRET_KEY` 必须是独立的 16 字节密钥，以 32 个十六进制字符表示。Compose 会把同一值分别注入 ops-platform 和 Guacamole JSON 认证扩展。Guacamole 与 guacd 没有发布宿主机端口，只允许 ops-platform 内置 Nginx 代理 `/guacamole/`。

guacd 必须能够访问受管主机，因此 `remote_gateway` 网络保留出站能力。生产环境应在宿主机或上游防火墙将其限制到批准的目标网段以及 TCP 3389/5900 等实际使用端口，不能把 guacd 的 4822 端口暴露到公网或普通办公网。

Guacamole 1.6.0 默认启用暴力破解封禁扩展。Compose 同时启用 Tomcat `RemoteIpValve`，让封禁按 Nginx 传入的真实客户端 IP 生效，避免把代理后的全部用户识别为一个地址。外层代理必须追加可信的 `X-Forwarded-For`，不能直接透传客户端伪造的头部。

## 配置与授权顺序

1. 在资产接口创建 `password` 凭据；接口响应永远不返回密码。
2. 创建协议为 `rdp` 或 `vnc` 的身份，并把它绑定到目标主机。
3. 系统管理员在主机列表的“远程桌面”窗口配置端点。
4. 在角色中授予“RDP/VNC 远程桌面”页面权限。
5. 使用对象授权接口授予对应主机的 `rdp.connect` 或 `vnc.connect`；需要限制到特定身份时同时提交 `identity_id`。

端点管理 API：

```text
GET    /api/v1/gateway/endpoints/?host_id=<id>
POST   /api/v1/gateway/endpoints/
DELETE /api/v1/gateway/endpoints/?id=<id>
```

会话 API：

```text
POST   /api/v1/gateway/sessions/
GET    /api/v1/gateway/sessions/<uuid>/
DELETE /api/v1/gateway/sessions/<uuid>/
GET    /api/v1/gateway/sessions/<uuid>/launch/?ticket=<one-time-ticket>
```

## 当前限制

- `launched` 表示安全启动数据已签发，不等于已确认 RDP/VNC 握手成功。
- 关闭会话 API 目前只关闭 ops-platform 的会话元数据，不能强制断开已建立的 Guacamole 隧道。
- 尚未接入 Guacamole 连接/断开事件回调、并发会话控制、录像存储、录像访问审批或留存策略。
- 完成上述能力前，不应把本阶段描述为完整堡垒机，也不建议直接面向公网开放。
- 基础镜像仍依赖 CentOS 7、Python 3.6 和 Django 2.2；已知高危依赖必须通过后续技术栈升级解决，当前版本只适合隔离内网试运行。
- 官方 Guacamole Web 镜像操作系统层仍有可修复高危项，guacd 基于已停止维护的 Alpine 3.18，且 Java 依赖扫描尚未完成；详见 [安全验证基线](SECURITY_BASELINE.md)。
