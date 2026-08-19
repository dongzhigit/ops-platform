# M5 知识库与只读 AI 运维

版本：`0.1.0-alpha.12`

## 已实现范围

知识库提供知识空间、成员角色、文档、Runbook、故障复盘和资产说明。空间支持仅成员可见与组织内可见，访问同时受角色页面权限和空间成员角色约束。文档支持草稿、待审核、已发布和归档，任何有效修改都会生成新的不可覆盖版本；版本恢复同样创建新版本。删除采用软删除，写操作进入 HMAC 审计链。

知识检索只返回当前用户可访问的内容。AI 证据检索进一步限制为已发布版本，并携带形如 `knowledge://<space>/<document>?v=<version>` 的固定引用。当前 `retention_days` 记录空间保留策略，但不会自动清除历史版本；启用自动清理前必须先确定法规、备份和审计保留要求。

AI 运维第一阶段只支持同步的只读调查：

- 收集当前用户有权访问的主机基础信息、Prometheus 指标、Alertmanager 告警和已发布知识片段。
- 调用管理员配置的 OpenAI-compatible Chat Completions 服务。
- 输出区分事实、推断、未知、建议和人工行动方案。
- 事实必须引用本次授权证据，伪造或越权引用会导致整次调查失败。
- 结果、模型、提示版本、证据快照、token 用量和关联 ID 持久化并审计。
- 大模型不可访问 SSH、Shell、数据库、凭据、发布、配置或审批工具。

## 强制安全边界

资产描述、告警、指标和知识内容全部标记为不可信输入。系统提示明确要求忽略其中的指令，且模型请求不携带 `tools` 或 `functions`。后端在保存结果前再次递归拒绝 `command`、`shell`、`script`、`tool_call`、`arguments` 等可执行字段，并校验每个引用都属于本次证据集合。

模型生成的行动方案只有 `proposed` 状态和 `executable=false` 标记。它不能自动转为批量命令、计划任务、发布或配置变更。若后续增加写操作，必须另行实现白名单工具、结构化参数、主机对象授权、风险分级、双人审批、幂等键、灰度、验证、回滚和完整审计；禁止把模型文本直接拼接成 Shell。

模型服务故障、超时、非 JSON 输出、过大响应、伪造引用或禁止字段都会使调查进入 `failed`，但不影响人工运维功能。API 响应设置 `Cache-Control: no-store`，配置接口只返回密钥是否已配置及其来源，永不返回密钥内容或密文。

## 权限

- `knowledge.space.view`：查看有权访问的知识空间。
- `knowledge.space.manage`：创建空间；空间内修改和成员管理还要求空间管理员角色。
- `knowledge.document.view`：查看空间角色允许的文档。
- `knowledge.document.edit`：空间编辑者或管理员创建、编辑和恢复文档。
- `knowledge.document.publish`：发布文档以及修改已发布内容。
- `aiops.investigation.view`：查看可用调查范围和自己的调查记录。
- `aiops.investigation.run`：预览授权证据并发起只读模型调查。
- `aiops.config.manage`：在 AI 运维页面管理模型地址、模型名称、配额和加密 API Key。

超级管理员可以查看全部调查；普通用户只能查看自己创建的调查。页面权限不能绕过空间角色或主机对象授权。

## 接口

- `/api/v1/knowledge/spaces/`
- `/api/v1/knowledge/spaces/<space_id>/members/`
- `/api/v1/knowledge/documents/`
- `/api/v1/knowledge/documents/search/`
- `/api/v1/knowledge/documents/<document_id>/revisions/`
- `/api/v1/aiops/config/`
- `/api/v1/aiops/scope/`
- `/api/v1/aiops/evidence/preview/`
- `/api/v1/aiops/investigations/`

## 模型配置

AI 默认关闭。具有 `aiops.config.manage` 权限的管理员可以在“知识与 AI → AI 运维 → 模型配置”中保存并即时启用，无需重启容器。API Key 使用资产凭据主密钥执行 AES-GCM 信封加密，数据库只保存密文；密钥更新进入 HMAC 审计链，主密钥轮换命令也会同时轮换 AI 模型密钥。

页面配置运行时优先级高于环境配置。以下环境变量继续作为首次启动和灾难恢复的回退方式：

- `SPUG_AIOPS_ENABLED=true`
- `SPUG_AIOPS_BASE_URL`：包含 `/v1` 的 OpenAI-compatible 根地址；生产启用时必须使用 HTTPS。
- `SPUG_AIOPS_MODEL`：模型标识。
- `SPUG_AIOPS_API_KEY_FILE`：容器内只读密钥文件路径；页面未保存密钥时作为回退。

页面和环境变量均可调整 JSON mode、请求超时、单次主机上限、知识片段上限、输出 token 上限、响应字节上限和每分钟调用上限。同一用户同时只允许一个模型调查，避免重复请求放大费用。部署示例见 [Docker Compose 说明](docker/README.md)。使用外部模型会把经过授权筛选的运维证据发送到该服务，部署方必须自行满足数据分类、地域、保留和供应商合规要求。

## 当前边界

- 不执行命令，不自动修改系统，不消费批准票据。
- 不采集原始日志、进程列表或终端输出；只有已接入的资产、指标、告警和知识证据。
- 不提供向量数据库；当前知识召回是权限过滤后的数据库文本匹配。
- 调查同步执行，受模型超时限制；后续大规模使用应迁移到隔离队列并增加并发配额。
- 仍受 [安全验证基线](SECURITY_BASELINE.md) 中旧操作系统、Python、Django 和辅助镜像漏洞阻断，不得据此宣称可生产部署。
