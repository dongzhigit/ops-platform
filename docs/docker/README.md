# Spug 私有化部署基线

该目录的 Compose 配置用于第一阶段内部试运行，默认只监听本机回环地址，并要求显式设置所有密钥和数据库口令。

## 启动

```bash
cd docs/docker
cp .env.example .env
openssl rand -base64 48
openssl rand -base64 32
openssl rand -base64 48
```

将三个独立生成的值依次写入 `.env` 的 `SPUG_SECRET_KEY`、`SPUG_CREDENTIAL_MASTER_KEY` 和 `SPUG_AUDIT_SIGNING_KEY`。随后填写数据库口令、正式域名和 HTTPS 来源，然后执行：

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

资产凭据、对象授权、旧私钥迁移和主密钥轮换说明见 [M1 资产、凭据与对象授权](../M1_ASSET_ACCESS.md)。

## 网络与 TLS

- 默认 `SPUG_BIND_HOST=127.0.0.1`，应通过 Caddy、Nginx 或负载均衡器提供 HTTPS。
- 只有在可信内网且有防火墙保护时，才将绑定地址改为 `0.0.0.0`。
- 反向代理必须设置 `X-Forwarded-Proto`，使用 HTTPS 时保持 `SPUG_SECURE_COOKIES=true`。
- 若由外部代理完成 HTTPS 跳转，`SPUG_SSL_REDIRECT` 可保持 `false`，避免配置错误造成循环重定向。

## 权限说明

批量文件分发依赖 SSHFS，因此容器需要 `/dev/fuse` 和 `SYS_ADMIN` capability。这里没有使用完整的 `privileged` 模式。若不需要 SSHFS 文件分发，可删除 `cap_add`、`devices` 和 `apparmor:unconfined`，进一步收紧权限。

当前容器基线面向 Linux 主机；Docker Desktop 通常不提供可直接映射的 `/dev/fuse`。在 macOS/Windows 上仅做界面开发时，可删除上述 FUSE 配置并暂不使用 SSHFS 文件分发。

M0 镜像仍依赖 CentOS 7/Python 3.6，并临时锁定为 `linux/amd64`；ARM 主机会通过模拟运行，性能不适合作为正式生产方案。Dockerfile 使用归档软件源保证过渡期可构建，后续必须迁移到仍受安全支持的操作系统、Python 和 Django 版本。

## 上线前检查

- `.env` 不得提交到 Git。
- 域名和 TLS 证书已配置，公网不能直接访问数据库和 Redis。
- 已执行数据库备份和恢复演练。
- `SPUG_CREDENTIAL_MASTER_KEY` 已独立备份并完成凭据恢复演练。
- 管理员启用独立强密码，不与主机或数据库复用。
- `docker compose ps` 中数据库和 Spug 服务均为 healthy。
