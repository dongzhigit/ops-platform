# M1 资产、凭据与对象授权

高风险操作的审批、一次性票据和审计链见 [M1 高风险操作审批与审计](M1_APPROVAL_AUDIT.md)。

## 当前交付范围

本阶段完成了第一条可运行的 M1 纵向链路，不代表整套平台已经完成。当前支持：

- SSH 密钥、密码和 Token 凭据的 AES-GCM 信封加密；每条记录使用独立数据密钥。
- SSH/SFTP/RDP/VNC 身份模型，以及主机与身份绑定；当前实际连接链路先支持 SSH/SFTP。
- 用户或角色到单台主机或主机组的 allow/deny、动作、有效期授权。
- SSH、文件读写、文件分发、批量命令、计划任务、主机监控和发布部署在执行前校验对象权限。
- deny 覆盖旧主机组权限；旧分组权限继续作为兼容 allow。
- 批量命令队列只保存用户和主机引用，不再把解密私钥写入 Redis。
- 所有应用的固定 Django 迁移基线；镜像启动时只校验迁移漂移并执行已提交迁移。

后续仍需完善资产中心前端、标签、配置中心统一授权和远程会话录像。RDP/VNC、监控、知识库和只读 AI 调查已经落地；AI 仍不具备任何写操作或执行工具。

## API

以下接口仅超级管理员可管理，响应不会返回 `secret` 或 `secret_data`：

| 接口 | 方法 | 用途 |
| --- | --- | --- |
| `/api/v1/assets/credentials/` | GET/POST/DELETE | 查询、新建/更新、停用凭据 |
| `/api/v1/assets/identities/` | GET/POST/DELETE | 查询、新建/更新、停用身份 |
| `/api/v1/assets/bindings/` | GET/POST/DELETE | 管理主机身份绑定和默认身份 |
| `/api/v1/assets/grants/` | GET/POST/PATCH | 创建授权、查询授权、撤销授权 |

授权动作包括：

- `host.view`
- `ssh.connect`
- `file.read`
- `file.write`
- `file.distribute`
- `exec.run`
- `schedule.run`
- `monitor.run`
- `deploy.run`
- `*`

身份级授权只允许绑定单台主机。未显式选择身份的现有页面会使用主机默认身份，因此身份级授权也只在该身份为默认绑定时生效。

## 旧主机私钥迁移

先备份数据库和主密钥，再执行预检查：

```bash
docker exec ops-platform python3 /data/ops-platform/ops_api/manage.py migrate_host_credentials
```

执行迁移但保留 `hosts.pkey` 回滚窗口：

```bash
docker exec ops-platform python3 /data/ops-platform/ops_api/manage.py migrate_host_credentials --execute
```

确认所有主机连接正常后，回读验证并清除旧明文：

```bash
docker exec ops-platform python3 /data/ops-platform/ops_api/manage.py migrate_host_credentials --execute --clear-legacy
```

命令具有幂等检查，已存在默认托管身份的主机会跳过。`--clear-legacy` 只会在新密文回读内容与旧私钥一致后清除旧字段。

## 主密钥备份与轮换

`SPUG_CREDENTIAL_MASTER_KEY` 丢失后凭据不可恢复。必须把它与数据库备份分开保存在受控密码库或 KMS 中，并做恢复演练。不要直接替换该变量，否则现有凭据会立即无法解密。

轮换采用双密钥分阶段方式：

1. 进入维护窗口并备份数据库和当前 `.env`。
2. 生成新密钥：`openssl rand -base64 32`。
3. 保持旧值仍在 `SPUG_CREDENTIAL_MASTER_KEY`，将新值加入 JSON keyring，例如：

   ```dotenv
   SPUG_CREDENTIAL_KEYRING={"v2":"新密钥的Base64值"}
   SPUG_CREDENTIAL_PRIMARY_KEY_ID=v2
   ```

4. 重建/重启容器，使旧 `primary` 和新 `v2` 同时可读；新写入自动使用 `v2`。
5. 先预检查，再执行逐条重新加密和回读验证。该命令同时覆盖资产凭据和 AI 运维页面保存的模型 API Key：

   ```bash
   docker exec ops-platform python3 /data/ops-platform/ops_api/manage.py rotate_credential_master_key --target-key-id v2
   docker exec ops-platform python3 /data/ops-platform/ops_api/manage.py rotate_credential_master_key --target-key-id v2 --execute
   ```

6. 查询数据库确认没有旧 key ID 后，才可在下一维护窗口把 `SPUG_CREDENTIAL_MASTER_KEY` 替换为新密钥；保留 keyring 中的 `v2` 映射和 `SPUG_CREDENTIAL_PRIMARY_KEY_ID=v2`。若轮换失败，事务会整体回滚，旧密钥必须继续保留。

`.env` 对 JSON 的引号处理因 Compose 版本而异；执行 `docker compose config` 确认容器最终收到的是合法 JSON，且不要把该输出保存到公开日志。

## 数据库迁移策略

仓库现在为全部 Django 应用保存 `0001_initial.py` 基线。`updatedb` 不再在生产容器里生成迁移，只执行：

1. `makemigrations --check --dry-run`，发现模型漂移就失败关闭；
2. `migrate --noinput`，应用仓库中已经评审的迁移。

已有 ops-platform 数据库通常已记录各应用的 `0001_initial`，升级时新增的 `assets.0001_initial` 会单独应用。正式升级前仍必须在数据库副本上演练，并验证迁移记录与实际表结构一致。
