# Project status and development handoff

Last updated: 2026-09-08

## Current baseline

- Branch: `3.0`
- Release: `ops-v0.1.0-alpha.43`
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

- Application image/version naming now uses the `ops-platform:*` tag.
- The observability Docker bridge now uses an explicit configurable subnet.
  This prevents Docker from allocating a subnet that shadows a managed host or
  an internal model-service address.
- On Windows, MariaDB remains on a Docker named volume because NTFS bind mounts
  do not provide the Linux filesystem semantics required by InnoDB.
- The model request timeout is configurable up to 120 seconds. Slow internal
  reasoning models may require a value greater than the 30-second default.
- Host verification now lets an operator retry with a temporary login password
  when an existing SSH credential or global key cannot authenticate. The
  password is used only for that verification/public-key installation step and
  is not stored.
- Topology now defaults to a server-link graph and opens a focused service-link
  graph from host nodes. The first view shows only server-to-server business
  calls; service, port, database and middleware nodes appear only after
  drilling into a server or switching to the full view.
- The monitoring topology tab now links directly to the layered topology page
  to avoid confusing the legacy monitoring topology with runtime diagnosis.
- Topology graph nodes now use compact pill nodes with concise name, type,
  port or PID metadata and readable 14px source title text; topology columns
  are widened to give business links more horizontal routing space, empty
  columns are folded, sparse columns are vertically centered against the
  densest column, edge labels appear only for the selected node's related
  links, and full raw identifiers remain available in hover text and the
  right-side detail panel.
- Topology graph canvas uses 60% as the default standard view and supports
  60% to 200% zoom levels, with drag movement corrected for the active scale
  so operators can zoom into dense business links without losing manual layout
  control.
- Topology graph nodes can be dragged within the current canvas. Connection
  anchors are distributed within each compact node side while the curve control
  points fan out parallel calls, so shared MySQL/Redis-style dependencies no
  longer render as one overlapped line bundle without visually detaching from
  their nodes.
- Topology edge labels now wrap into multiple SVG text lines with a light
  background instead of being truncated to ten characters.
- Runtime topology now keeps business dependency calls such as MySQL, Redis,
  PostgreSQL, MongoDB, RabbitMQ and Kafka, while generic established TCP
  connections are filtered out of the default diagnosis graph.
- Runtime topology now recognizes frontend, backend, database and middleware
  links for common Nginx/Vue/Node/Python/Go/Java stacks, and the host creation
  form can trigger read-only business-topology discovery after verification.
- Host onboarding now previews discovered services and business links before
  topology generation, so operators can confirm the default selection instead
  of writing discovered topology immediately.
- Runtime topology now resolves connections to scanned remote listener services
  and includes the target service name in port nodes and edge labels.
- Runtime topology now parses `tcp6`/`udp6` socket rows, keeps inbound
  database/middleware connections whose service port is on the local side, and
  shows important MySQL/Redis-style listeners in the server-level graph.
- Runtime discovery now separates candidate discovery from fixed monitoring:
  only services and business dependencies confirmed by an operator or added
  manually become long-lived topology monitors; later scans refresh those
  fixed service/port statuses instead of adding every transient connection.
- Runtime discovery now adds sanitized framework hints for Django-style Python
  services. Remote hosts return only PID-to-framework labels, so Django
  listeners can be shown as Django services without storing full command-line
  arguments.
- Runtime business links now preserve the concrete source process where
  available, so a Django/Python process calling MySQL or Redis is not collapsed
  into a generic host-level dependency.
- Runtime scans now use non-interactive sudo when the managed host permits it,
  parse Linux netstat `PID/program` ownership, and identify database or
  middleware services by process name on non-standard ports such as MySQL
  exposed on 13306.
- Runtime discovery now supplements live sockets with sanitized Django settings
  hints, so a Django process can keep a MySQL/PostgreSQL dependency in the
  fixed topology even when no database socket is established at scan time; the
  saved dependency status is still decided by runtime/probe evidence.
- Runtime dependency discovery now prefers scanned listener process ownership
  over generic backend port ranges, so non-standard Redis/MySQL ports are not
  mislabeled as backend APIs; unknown external client traffic is no longer
  selected as a fixed business dependency by default.
- Runtime service-chain discovery now reads sanitized Nginx upstream/proxy
  targets and preserves application-to-application calls such as
  Nginx -> Django, while allowing private clients of shared MySQL/Redis
  services to converge on the same dependency nodes.

## Planned AI topology diagnosis

- AI topology diagnosis, remediation evidence and incident-room workflows are
  tracked in [M6 AI topology diagnosis](M6_AI_TOPOLOGY_DIAGNOSIS.md).
- P0 data model, P1 static topology, P2 monitoring/alert status linkage,
  P3 read-only AI topology diagnosis, P4 incident room workflow, P5
  controlled remediation proposal approval workflow, P6 execution evidence
  records, P7 platform operation reference validation, P8 incident
  closed-loop display, P9 runtime process topology scanning, P10 layered
  topology views, P11 business-link discovery, P12 host-onboarding topology
  confirmation, P13 remote-service labeling, P14 bidirectional dependency
  normalization and P15 fixed business-service monitoring are
  implemented. Remaining work should focus on acceptance testing with real
  production-like assets and operation-module integration depth, while keeping
  writes behind the existing authorization, approval and audit paths.
- Progress reports for this feature should use the status template in the M6
  document so each handoff states the current phase, completed work,
  verification result, open issues and recommended next phase.

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

1. Run production-like acceptance tests for M6 with real asset scopes,
   monitoring alerts, platform execution records and incident handoffs.
2. Modernize the supported OS, Python and Django runtime with migration tests.
3. Improve health checks so the ops-platform container fails health when core
   Supervisor processes are not running, rather than checking Nginx alone.
4. Add repeatable Windows and Linux acceptance scripts for RDP/VNC, monitoring,
   SFTP, notifications and AI provider compatibility.
5. Complete backup/restore, credential-key rotation and disaster-recovery
   exercises before any production designation.
