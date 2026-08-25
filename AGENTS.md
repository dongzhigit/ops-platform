# Codex project guidance

## Start here

- This repository is a security-focused secondary development fork of Spug,
  not a greenfield rewrite. Preserve upstream behavior unless a requirement or
  documented security boundary explicitly changes it.
- Read `docs/PROJECT_STATUS.md` before starting a new task. Then read only the
  milestone document relevant to the area being changed.
- Never put passwords, API keys, credential master keys, deployment `.env`
  files, database dumps, or runtime data in Git.

## Architecture and routing

- `spug_api/`: Django 2.2 / Python 3.6 legacy backend. Keep compatibility with
  the pinned runtime until the platform-upgrade milestone is explicitly taken.
- `spug_web/`: React frontend built with `react-app-rewired` and Yarn.
- `docs/docker/`: immutable application image and Compose deployment baseline.
- `docs/M1_ASSET_ACCESS.md`: assets, credentials, identities, authorization.
- `docs/M1_APPROVAL_AUDIT.md`: approvals and tamper-evident audit behavior.
- `docs/M2_RESUMABLE_SFTP.md`: SFTP upload/download/edit and resumability.
- `docs/M3_REMOTE_DESKTOP.md`: RDP/VNC through Guacamole.
- `docs/M4_OBSERVABILITY.md`: Prometheus, Alertmanager, metrics and alerts.
- `docs/M5_KNOWLEDGE_AIOPS.md`: knowledge base and read-only AI investigation.
- `docs/SECURITY_BASELINE.md`: security constraints that must remain true.

## Working rules

- Preserve unrelated working-tree changes and existing deployment data.
- Make the smallest coherent change, add or update tests, and update the
  relevant milestone documentation when behavior changes.
- Keep AI investigations read-only. Model output must never directly execute
  commands or bypass approval, authorization, audit, or evidence validation.
- Store asset credentials and model API keys encrypted. APIs and pages must
  never return secret plaintext after saving.
- Production public model endpoints require HTTPS. Trusted loopback, Docker
  internal names, and RFC1918 private IPs may use HTTP only in isolated LANs.
- Docker network subnets must not overlap managed hosts, VPNs, LANs, or model
  service networks. Keep `SPUG_OBSERVABILITY_SUBNET` explicit and configurable.
- Do not claim production readiness while the documented legacy runtime and
  pre-release limitations remain.

## Verification

- Backend: `python3 spug_api/manage.py test`
- Frontend: `cd spug_web && yarn install --frozen-lockfile && yarn build`
- Compose baseline: from `docs/docker`, run
  `docker compose -f docker-compose.yml -f docker-compose.windows.yml config --quiet`
  for Windows Docker Desktop, or `docker compose config --quiet` on Linux.
- For focused changes, run the narrow test first, then the full applicable
  suite before release. Report skipped tests and environmental limitations.

## Deployment safety

- Windows Docker Desktop must use Linux containers and
  `docker-compose.windows.yml`.
- Do not delete or recreate database, Prometheus, or Alertmanager volumes to
  fix startup problems. Diagnose mounts, permissions, health checks, DNS, and
  routing first.
- Never clean unrelated images, build cache, containers, networks, or volumes.
- A page returning HTTP 200 is insufficient: confirm Supervisor application
  processes and all Compose service health states.
