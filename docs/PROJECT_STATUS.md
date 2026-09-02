# Project status and development handoff

Last updated: 2026-08-25

## Current baseline

- Branch: `3.0`
- Release: `ops-v0.1.0-alpha.14`
- Baseline commit: `b1ff1e748a877158a498cf72b9b816a4cf7eebba`
- Status: internal pre-release; do not describe it as production-ready.
- Origin: ops-platform with authorization, security, remote access,
  observability, knowledge, and AI operations modules.

## Implemented scope

- Grouped asset management for hosts, credentials, identities and object-level
  authorization.
- SSH online terminal plus Guacamole-backed RDP/VNC access.
- SFTP upload, download and online editing, including resumable large files.
- Batch commands and multi-host file distribution, subject to authorization
  and platform capability limits.
- Cron-based scheduled host commands and deployment/configuration workflows.
- Prometheus and Alertmanager integration for host metrics, targets and alerts.
- Knowledge spaces, documents, publication and access control.
- Read-only AI investigations with evidence citations, strict JSON validation,
  approval-aware action proposals, audit records and rate limits.
- Page-managed OpenAI Chat Completions and Anthropic Messages configurations;
  API keys are encrypted and never echoed back.
- Trusted private HTTP model endpoints are allowed; public HTTP endpoints are
  rejected in production mode.

## Latest operational fixes

- The observability Docker bridge now uses an explicit configurable subnet.
  This prevents Docker from allocating a subnet that shadows a managed host or
  an internal model-service address.
- On Windows, MariaDB remains on a Docker named volume because NTFS bind mounts
  do not provide the Linux filesystem semantics required by InnoDB.
- The model request timeout is configurable up to 120 seconds. Slow internal
  reasoning models may require a value greater than the 30-second default.

## Known boundaries

- The base image still depends on CentOS 7, Python 3.6 and Django 2.2. Runtime
  modernization is required before a production release.
- Windows Docker Desktop cannot provide the Linux host `/proc`, `/sys`, FUSE,
  or AppArmor behavior used by some Linux-only functions. SFTP remains usable;
  SSHFS-based batch distribution is unavailable in the Windows override.
- Guacamole, monitoring and notification integrations still require real
  managed-host and provider acceptance testing in each target environment.
- Model quality and latency depend on the configured provider. A successful
  TCP connection or `/models` response does not replace an end-to-end AI
  investigation test.

## Last verified baseline

- Backend suite: 110 tests passed, 2 skipped before alpha.14 release.
- React production build passed before alpha.14 release.
- Windows Compose services verified healthy: MariaDB, Guacamole/guacd,
  Prometheus, Alertmanager and ops-platform.
- Supervisor processes verified running: API, WebSocket, Worker, Scheduler,
  Monitor, Redis and Nginx.
- A real encrypted page configuration successfully called an OpenAI-compatible
  private model endpoint and passed the strict investigation output validator.

## Suggested next work

1. Modernize the supported OS, Python and Django runtime with migration tests.
2. Improve health checks so the ops-platform container fails health when core
   Supervisor processes are not running, rather than checking Nginx alone.
3. Add repeatable Windows and Linux acceptance scripts for RDP/VNC, monitoring,
   SFTP, notifications and AI provider compatibility.
4. Complete backup/restore, credential-key rotation and disaster-recovery
   exercises before any production designation.
