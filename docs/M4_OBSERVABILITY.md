# M4 主机监控与告警闭环

版本：`0.1.0-alpha.10`

## 能力范围

本阶段在原有站点、端口、Ping、进程和自定义脚本探测之外，新增独立的可观测性域：

- 主机与 node_exporter 采集目标一对一绑定，Prometheus 通过受 Bearer Token 保护的 HTTP 服务发现接口动态加载目标。
- CPU、内存、磁盘、网络吞吐和在线状态使用后端预定义 PromQL。浏览器不能提交任意 PromQL。
- 指标目标、实时摘要、趋势和告警事件均通过现有主机对象授权过滤；显式授权动作是 `metrics.view`。
- Prometheus 基线规则覆盖 Exporter 离线、CPU、内存和磁盘阈值，Alertmanager 通过独立 Bearer Token 回调 ops-platform。
- 相同 fingerprint 在一次故障周期内聚合；恢复后再次触发会创建新的 episode，保留历史故障周期。
- 告警支持认领、取消认领、静默、取消静默和时间线。静默操作会调用 Alertmanager Silence API，失败时不会把本地状态伪装成成功。
- 发生、恢复、采集目标变更、认领和静默均写入现有的 HMAC 哈希链审计。
- 采集目标可以关联原有报警联系组和微信、短信、钉钉、邮件、企业微信、电话、飞书通知方式；未配置时仍生成站内事件。

## 部署

先为 Prometheus 服务发现和 Alertmanager Webhook 分别生成独立 token：

```bash
cd docs/docker
mkdir -p secrets
openssl rand -hex 32 > secrets/prometheus_discovery_token
openssl rand -hex 32 > secrets/alertmanager_webhook_token
chmod 700 secrets
chmod 444 secrets/prometheus_discovery_token secrets/alertmanager_webhook_token
```

`secrets/` 已被 Git 忽略。目录仅允许属主访问；目录中的文件设为只读，使 Prometheus/Alertmanager 的非 root 用户能够读取挂载后的 token，同时其他宿主机用户无法穿过私有目录。Compose 将两个文件只读挂载到 ops-platform 和对应的监控组件，不把 token 放入命令行、URL 或版本库。

执行配置检查并启动：

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Prometheus、Alertmanager 和 node_exporter 没有发布宿主机端口。`observability` 网络需要访问被监控主机；生产环境应通过防火墙限制 Prometheus 只能访问批准的目标地址和端口。

Compose 中的 node_exporter 采集当前 Docker 宿主机。要监控其他资产，需要在每台目标 Linux 主机部署 node_exporter，并在“监控中心 / 主机指标 / 新增目标”中填写 Prometheus 实际可达的地址。容器宿主机若作为 ops-platform 资产登记，可将 Exporter 地址填写为 Compose 网络中的 `node-exporter`。

## 权限

页面权限分为：

- `monitor.metrics.view`：查看已授权主机的目标和指标；
- `monitor.metrics.manage`：创建、修改和删除采集目标；
- `alarm.event.view`：查看已授权主机的指标告警；
- `alarm.event.claim`：认领或取消认领告警；
- `alarm.event.silence`：创建或取消 Alertmanager 静默。

页面权限只控制功能入口，对象范围仍由资产分组和 AccessGrant 决定。`metrics.view` 的显式 deny 会在指标查询、告警列表和操作时生效。

服务到服务接口不使用用户会话：

- `GET /api/v1/observability/discovery/targets/`
- `POST /api/v1/observability/alertmanager/webhook/`

两者只接受各自独立的 `Authorization: Bearer ...`，响应禁止缓存。普通指标接口只返回预定义查询结果。

## 验收建议

1. 新增一个已运行 node_exporter 的主机目标，等待最多 30 秒完成服务发现。
2. 在指标总览中确认在线状态以及 CPU、内存、磁盘和网络数据，切换 1/6/24 小时趋势。
3. 暂停目标 node_exporter，等待 `OpsPlatformHostExporterDown` 规则持续 2 分钟后确认告警进入工作台。
4. 检查同一告警重复回调只增加聚合次数；恢复后状态变为“已恢复”；再次故障产生新的 episode。
5. 创建静默并在 Alertmanager API 中确认 Silence 存在，再从工作台取消静默。
6. 使用只授权部分主机的普通用户确认无法查看或操作其他主机的指标和告警。
7. 在安全中心按事件关联 ID 检查发生、恢复、认领和静默审计链。

## 当前边界

- 指标依赖 node_exporter，ops-platform 不会自动在远程主机安装代理。
- 原有站点、端口和 Ping 探测仍由兼容调度器执行，尚未自动迁移成 Prometheus blackbox 任务。曾评估的 blackbox_exporter `0.28.0` 在本次漏洞库中仍有严重且已有修复版本的 Go 依赖漏洞，因此未把未使用的组件放入默认部署栈。
- 默认保留 30 天本地 TSDB 数据；已恢复告警事件默认保留 90 天，可通过 `SPUG_ALERT_RETENTION_DAYS` 调整。当前不是 Prometheus/Alertmanager 高可用集群，也没有远程长期存储。
- 告警阈值目前来自版本化规则文件，尚未提供逐主机在线规则编辑器。
- 服务 Bearer Token 应配合隔离网络使用；若把接口跨主机暴露，必须再使用 mTLS 或受控 HTTPS 反向代理。

## 拓扑视图设计

监控中心新增“拓扑视图”，用于把平台组件、Prometheus、Alertmanager、主机和 node_exporter 采集链路放在同一张图中展示。当前版本只使用已有可信数据源：

- `/api/v1/observability/topology/`：后端聚合后的只读拓扑快照；
- 指标采集目标：已授权主机的 node_exporter 配置；
- 指标摘要：主机在线状态、CPU、内存、磁盘和网络；
- 告警事件：当前 firing 告警；
- 配置中心：应用依赖和服务依赖；
- 发布配置：应用部署到哪些主机和环境。

拓扑图分为服务、应用和服务器三列。节点颜色规则为：停用目标显示灰色，Exporter 不在线显示红色，存在 firing 告警时按最高严重度显示橙色或红色，其余为绿色。应用状态从其可见部署主机状态汇总，服务状态从依赖它的应用状态汇总。

连接线分为三类：应用部署到服务器、应用依赖应用、应用依赖服务。应用到服务器的连接可以基于 node_exporter 指标和告警推导状态；应用/服务依赖线来自配置关系，当前没有独立 TCP/HTTP/gRPC 探测时在前端显示为虚线和“未探测”。这样可以快速判断问题在业务应用、被依赖服务还是具体服务器，但不会把未探测的配置依赖伪装成真实连通状态。

要进一步做到“各业务服务之间的联系和连接状态”，需要新增显式业务拓扑模型，而不是从脚本或配置文本中猜测：

1. `ServiceInstance`：服务实例、所属应用/环境、所在主机、端口、协议和健康检查地址。
2. `ServiceDependency`：上游服务、下游服务、连接方式、期望 SLA、负责人和影响级别。
3. `DependencyProbeResult`：定时探测 TCP/HTTP/gRPC/数据库等连接状态，并保留最近状态、延迟和错误摘要。
4. `TopologySnapshot`：把资产、部署、依赖和探测结果聚合成一次可审计快照，前端只渲染快照，不直接拼接敏感配置。

安全边界保持不变：拓扑和探测必须受主机对象授权过滤；探测结果不得暴露凭据、连接串密钥或模型 API Key；AI 调查只能引用拓扑和指标证据，不得通过拓扑直接执行修复命令。
