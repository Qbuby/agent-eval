# Build OmniAgent's durable product plane

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must remain current as implementation proceeds. The repository does not contain a root `PLANS.md`; this file follows the ExecPlan methodology from the local `execplan` skill.

## Purpose / Big Picture

After this work, an internal user can use OmniAgent for work that outlives one streaming chat request. The user can submit a bounded Python analysis, inspect or cancel the durable job, approve an exact fixed business action, receive completion events after reconnecting, and explicitly save or remove personal memory. All records remain tenant- and owner-scoped. The browser displays approvals and durable activity inside the existing OmniAgent page without converting the chat transport to WebSocket.

The first operational release is intentionally closed to arbitrary network, SQL, shell, package installation, cross-tenant administration, and model-selected credentials. Agent Eval is the authority for identities, events, actions, jobs, artifacts, memory, quotas, schedules, and notifications. OmniAgent chooses reviewed Axi capabilities; Kubernetes sandboxes execute bounded work but hold no business credentials.

## Progress

- [x] (2026-08-25 18:10+08:00) Recovered the existing execution and governed-query designs and inspected the current chat, tenancy, scheduler, notification, and MinIO boundaries.
- [x] (2026-08-25 21:40+08:00) P0: added policy clamping, a separate execution JWT, deterministic digests, state-machine contracts, and 15 passing unit tests.
- [x] (2026-08-25 23:55+08:00) P1: added frozen product-plane storage plus durable events, leases, fixed actions, artifacts, memories, quota accounting, schedules, notifications, and Outbox delivery.
- [x] (2026-08-25 23:05+08:00) P2: added owner-scoped browser APIs, execution-JWT-only internal APIs, resumable durable event SSE, digest-bound browser decisions, turn-bound chat token injection, and low-frequency durable message events.
- [x] (2026-08-25 23:55+08:00) P3: added fail-closed artifact ingestion, bounded development Python execution, durable analysis jobs, quota reservation, fixed action execution, reliable scheduling, and notification delivery.
- [x] (2026-08-25 23:55+08:00) P4: added the responsive activity, approvals, artifacts, memories, notifications, and schedules work panel to the existing chat page.
- [ ] P5 partially complete: the separate Axi native client package, seven reviewed skills, license-gated runtime image, and fail-closed Kubernetes deployment exist. The official Axi wheel, upstream tag source, Linux CLI contract, non-Axi registry images, and isolated-cluster execution path are verified; Axi production enablement remains blocked by written license approval.
- [x] (2026-08-27 12:37+08:00) P6: migration 0043, tests, frontend build, WSL Docker builds, server-side dry-run, explicit isolated-cluster apply, live sandbox E2E, registry publication, security scans, and browser two-tenant E2E pass.
- [x] (2026-08-26 16:10+08:00) P5 runner continuation: implemented the fail-closed Kubernetes analysis adapter, worker/API wiring, opt-in pinned SDK packaging, and protocol-level lifecycle/security tests. Live staging validation remains part of the broader P5/P6 gates.
- [x] (2026-08-26 12:17+08:00) Added and locally exercised the two-phase execution rollback, including ServiceAccount recording/restoration, fail-before-mutation identity checks, Axi cleanup ordering, and preserved read/download product access.
- [x] (2026-08-26 12:17+08:00) Made rollback interruption-safe: cleanup records the restored ServiceAccount, an exact completed-state retry only resumes rollout observation, and mismatched recovery state fails before mutation.

## Surprises & Discoveries

- Observation: the current OmniAgent chat is already tenant- and owner-scoped, but its SSE request remains the only live presentation channel.
  Evidence: `src/agent_eval/api/routers/omniagent.py` persists sessions/messages and returns one `StreamingResponse` per turn; there is no durable event cursor.

- Observation: current scheduling is durable only at the task-spec row level and assumes one backend process.
  Evidence: `src/agent_eval/scheduler/eval_scheduler.py` explicitly documents a single-instance assumption and has no database lease or execution history.

- Observation: OmniAgent's `present_files` keeps MinIO credentials out of the sandbox, but does not create Agent Eval-owned artifact records.
  Evidence: `D:/program/OmniAgent/omniagent/tools/present_files.py` uploads directly under `<thread_id>/<path>` and returns optional presigned URLs.

- Observation: full-application OpenAPI generation is extremely slow under the current Windows Python 3.14 environment, while module compilation, Ruff, direct route registration, and focused unit tests complete.
  Evidence: the first seven chat tests passed, but the route test remained inside `create_app().openapi()` until interrupted; replacing schema generation with direct route inspection avoids exercising unrelated schemas.

- Observation: the sandbox SDK exposes no recursive directory download operation, and a transport timeout cannot safely be retried because the remote command may still have executed.
  Evidence: `OmniAgentSandboxClient` exposes only `run`, `write`, `read`, and `destroy`; its `/execute` request timeout is longer than the runtime command timeout, while the runtime returns exit code 124 after killing the process group.

- Observation: cancellation of `asyncio.to_thread` does not stop the synchronous SDK operation.
  Evidence: the protocol fake blocks the SDK thread and proves cancellation waits for the claim destroy attempt before propagating `CancelledError`.

## Decision Log

- Decision: implement the product plane additively beside existing chat and schedulers, controlled by feature flags.
  Rationale: existing evaluation and reply execution are process-local and high risk to rewrite in the same change; additive durable primitives can be tested and adopted capability by capability.
  Date/Author: 2026-08-25 / Codex.

- Decision: keep request SSE for token streaming and add a second cursor-based durable event SSE.
  Rationale: approvals and jobs must survive disconnects, while token streaming already works and need not be destabilized.
  Date/Author: 2026-08-25 / Codex.

- Decision: use a storage adapter with a filesystem development implementation and a server-side MinIO implementation.
  Rationale: tests and local development must not require object storage, while production object bytes still stay behind Agent Eval authorization.
  Date/Author: 2026-08-25 / Codex.

- Decision: execute generated Python only through a runner interface; local subprocess execution is development-only and production selects the Kubernetes runner.
  Rationale: the API and state machine can be accepted locally without weakening the production sandbox boundary.
  Date/Author: 2026-08-25 / Codex.

- Decision: use one short-lived SandboxClaim per analysis job attempt and never transparently replay a command after transport failure.
  Rationale: per-attempt isolation makes cleanup and retry ownership explicit, while replaying a non-idempotent `/execute` request could run user code twice.
  Date/Author: 2026-08-26 / Codex.

- Decision: enumerate outputs with an uploaded fixed helper and download allowlisted relative paths one by one.
  Rationale: the SDK has no recursive download API; a bounded JSON manifest avoids shell interpolation and archive extraction path traversal.
  Date/Author: 2026-08-26 / Codex.

- Decision: classify SDK/control-plane failures separately from user-code, timeout, log-limit, and output-policy failures.
  Rationale: only infrastructure failures may use the durable job retry path; replaying a user-code attempt after a known result would violate at-most-once execution semantics for that attempt.
  Date/Author: 2026-08-26 / Codex.

- Decision: keep the Kubernetes SDK out of the default backend image.
  Rationale: cluster access must require an explicit build-time opt-in as well as runtime feature flags and the review confirmation gate.
  Date/Author: 2026-08-26 / Codex.

- Decision: an action registry owns validation, preview, and execution; model-facing APIs can prepare and inspect actions but browser-authenticated APIs alone can approve or deny them.
  Rationale: free-form text is not authorization and immutable canonical arguments must remain server-side.
  Date/Author: 2026-08-25 / Codex.

## Outcomes & Retrospective

P0 and P2 are complete. Migration 0043 freezes ten product-plane tables without importing live ORM metadata. The browser can recover durable events and inspect or decide its own jobs/actions, while internal capabilities require a separate turn-bound execution JWT that never gains superadmin behavior. The artifact storage core exists and is fail-closed; workers, schedules, notifications, quota enforcement, and frontend surfaces remain.

P1 through P4 are now implemented end to end behind disabled-by-default feature flags. The backend also exposes nine reviewed logical entities through `data/search`, `data/describe`, and a bounded SQLAlchemy AST compiler with explicit root and join tenant predicates. The Compose database is at revision 0043 and the rebuilt backend, frontend, PostgreSQL, and OmniAgent containers reached healthy state with HTTP 200 responses. Axi/Kubernetes production activation remains intentionally blocked by license and immutable-image review gates.

The Agent Eval Kubernetes analysis runner is implemented and accepted in isolated kind. Each job attempt receives one DNS-safe Claim identity; fixed scripts and inputs are uploaded without interpolating user content into commands; outputs are enumerated by a fixed helper and revalidated during download; cleanup covers success, failure, timeout, and cancellation. The current execution-plane regression reports `124 passed, 1 skipped`; deployment-only acceptance reports `60 passed`, and final Docker/Hatch build contracts report `23 passed`. Ruff, compilation, TOML, shell, and whitespace checks pass. The published analysis runtime `sha256:fc93a846898c79df6dec13ca2028e54712eb604e5a00147b5b389ff9f3b8f6c4` passed real server dry-run, apply, Pod hardening, backend health, Calico network isolation, TTL deletion, and cleanup. The runner-enabled backend `sha256:fe886eb36f9549b9d2bf6bd65b5e8841e1b57ed6000d4c2316f701435ea31527` contains the pinned SDK, passes `/health`, and scans at `0 HIGH / 0 CRITICAL` over its merged rootfs. Browser two-tenant acceptance now proves seven owner-scoped UI surfaces plus list, direct-read, mutation, query, cursor, and post-mutation ownership isolation in both directions. Production was not changed; Axi written-license approval is the only remaining external gate.

## Context and Orientation

The repository root is `D:/program/agent_eval`. FastAPI routes are registered in `src/agent_eval/api/app.py`. The existing OmniAgent browser API is `src/agent_eval/api/routers/omniagent.py`; its upstream SSE normalization and message persistence live in `src/agent_eval/services/omniagent_chat.py`. SQLAlchemy models are centralized in `src/agent_eval/db_models/tables.py`, and Alembic migrations live in `alembic/versions/` with current head `0043`.

Every product-plane row that belongs to a customer inherits `TenantMixin`. A durable event is an append-only row with a monotonically increasing database identity used as a reconnect cursor. A lease is a database timestamp proving one worker owns a queued job until that timestamp; another worker may recover the job only after expiry. An outbox is a database row representing a notification that still needs delivery, allowing retries after process restart. A capability action is an immutable, digest-bound request for one registered business operation.

The browser authenticates with the normal user JWT. Axi clients use a separate execution JWT with issuer `agent-eval`, audience `omniagent-execution`, tenant, user, session, message, scopes, budgets, unique token ID, and five-minute expiry. Decoding always constructs a principal with no superadmin bypass.

## Plan of Work

P0 creates `src/agent_eval/omniagent_runtime/` for policy defaults, canonical JSON hashing, execution JWTs, stable errors, and state transitions. Unit tests prove a browser JWT is rejected, scopes and budgets are clamped, and action hashes are deterministic.

P1 adds migration `0043` and matching ORM rows. Events, jobs, attempts, actions, artifacts, memories, schedules, outbox deliveries, and quota ledger entries all carry explicit tenant and owner keys. Services use compare-and-swap updates for action and job transitions. Job claiming uses `SELECT ... FOR UPDATE SKIP LOCKED`; lease expiry can requeue only infrastructure failures and never a reported code failure.

P2 extends the browser router with event, job, action, artifact, memory, schedule, and notification endpoints. It adds internal routes under `/internal/omniagent/v1` authenticated only by execution JWT. Browser approval accepts only `approve` or `deny` plus the server-provided digest. Durable event SSE supports `after`, sends heartbeat comments, and can be replaced by polling the same endpoint.

P3 adds artifact storage and analysis execution. Uploads stream to a staging object while hashing and enforcing 50 MiB. A scanner interface defaults to a fail-closed production mode and a clearly named development scanner. Available objects are downloaded only through an authenticated route. Analysis jobs carry code plus declared artifact IDs; the worker materializes only those inputs and publishes only files below the job output directory. The local runner uses isolated mode, a sanitized environment, process groups, output caps, and no shell. Production configuration refuses the local runner.

P4 extends `frontend/src/pages/OmniAgentPage.tsx` in the existing quiet operational visual language. It adds attachment and activity controls, persistent approval cards with approve/deny commands, job progress, artifact download links, notification state, and a memory drawer. Reconnect starts from the last durable cursor and reconciles events with normal message history.

P5 creates `packages/agent-eval-axi-tools/`, reviewed skills under `deploy/omniagent/skills/`, and Kubernetes manifests under `deploy/k8s/omniagent-execution/`. Native tools call only fixed internal paths and never accept URLs or credentials. NetworkPolicy allows DNS and the Agent Eval internal service only. The sandbox has no service account token, runs non-root, drops capabilities, and has bounded CPU, memory, PIDs, time, and ephemeral storage.

P6 validates contracts and observable behavior. PostgreSQL tests cover isolation and leases; API tests cover reconnect, duplicate decisions, ownership, and quota rejection; runner tests cover path traversal, timeout, cancellation, and output limits; frontend build verifies types. WSL Docker builds the pinned sandbox image. Kubernetes manifests are first checked with client dry-run and only applied after explicit production approval.

## Concrete Steps

From `D:/program/agent_eval`, create `.codex_tmp` and run `python -m pytest tests/test_omniagent_runtime tests/test_api/test_omniagent_chat.py tests/test_deployment -q --basetemp .codex_tmp/pytest-final`. The current result is `124 passed, 1 skipped`; the two warnings are caused by a pre-existing 31-byte test HMAC key. Run the Ruff, compileall, shell-syntax, TOML, and `git diff --check` commands recorded in this plan. Build the backend with `--build-arg INSTALL_KUBERNETES_RUNNER=1` only for reviewed Kubernetes deployments; the Dockerfile applies current Debian security updates, installs the pinned SDK, validates dependencies, and removes build-only Python packaging tools. Do not apply production resources automatically.

## Validation and Acceptance

An internal user can refresh during a running job and recover progress from durable events, approve an exact action once, inspect/download tenant-owned artifacts, and save/delete explicit personal memory. Cross-tenant IDs return 404. Browser JWTs fail on internal capability routes. Dangerous Python behavior is rejected or terminated. Existing text chat and message history remain compatible.

## Idempotence and Recovery

Migration `0043` is additive and its downgrade removes only new product-plane tables. Workers acquire jobs through leases, action execution is keyed by `action_id`, terminal states cannot move backward, and feature flags can stop token minting and job claiming while preserving reads and downloads. The supported Kubernetes rollback first rolls out execution and worker flags as false, then removes dormant execution configuration and restores the recorded ServiceAccount. It intentionally leaves `OMNIAGENT_PRODUCT_PLANE_ENABLED=true` so historical jobs and artifacts remain available. Cleanup records the restored ServiceAccount as an audit marker. A retry after cleanup only waits for the final rollout when that marker matches the current identity, execution is marked disabled, and the previous identity marker is absent; inconsistent states remain fail-closed. Namespace, Deployment, Secret, ServiceAccount, and image inputs are validated before manifest rendering. A previous or restored identity may never be `omniagent-executor`, preventing rollback metadata from preserving execution privileges.

## Artifacts and Notes

The baseline includes `0042_add_omniagent_chat_sessions.py`, request-level SSE, and no generic object storage in Agent Eval. New capabilities remain disabled until their server feature flags are enabled.

## Interfaces and Dependencies

`src/agent_eval/omniagent_runtime/security.py` defines `ExecutionPrincipal`, `mint_execution_token`, and `decode_execution_token`. `policy.py` defines effective limits. `events.py` handles durable cursors. `jobs.py` handles leasing. `actions.py` owns registered operations. `artifacts.py` defines storage and scanning. `runner.py` defines `LocalDevelopmentPythonRunner`, `KubernetesAnalysisRunner`, `RunnerInfrastructureError`, and `runner_configuration_error`. The optional `kubernetes-runner` dependency pins `k8s-agent-sandbox` to commit `a9db14672e77fbd15981fb2af9b73934e29b0cfe`; `Dockerfile` installs it only when `INSTALL_KUBERNETES_RUNNER=1`. Existing FastAPI, SQLAlchemy, PyJWT, Pydantic, httpx, PostgreSQL, React, and TanStack Query dependencies are reused.

Revision note (2026-08-25): created this implementation plan after the user approved the durable product-plane design, reconciled with current chat persistence, schedulers, Feishu identity binding, and OmniAgent-side MinIO upload behavior.

Revision note (2026-08-25 21:40+08:00): recorded completed P0 contracts, frozen migration 0043, lease recovery semantics, the current P1 remainder, and the Axi license publication gate after implementation and targeted validation.

Revision note (2026-08-25 23:05+08:00): recorded completed P2 browser/internal APIs, execution-token chat injection, durable message events, the Windows OpenAPI validation slowdown, and the partially complete P3 artifact service after interruption recovery.

Revision note (2026-08-25 23:55+08:00): recorded completed P1-P4 product behavior, the governed-query C0-C2 slice, Axi/Kubernetes review-gated P5 artifacts, migration 0043, focused validation, frontend build, and successful WSL Docker Compose rebuild without enabling execution scopes.

Revision note (2026-08-26 15:20+08:00): resumed P5 at the Kubernetes analysis runner, recorded the verified SDK/runtime contract, and fixed the implementation direction around per-attempt claims, no command replay, and manifest-driven output retrieval.

Revision note (2026-08-26 16:10+08:00): completed the Agent Eval Kubernetes analysis adapter and its worker/API/packaging integration, recorded cancellation and retry semantics, and captured a passing local runner subset. That historical subset is superseded by the current execution-plane baseline; real-cluster P5/P6 gates remain open.

Revision note (2026-08-26 12:17+08:00): added the locally verified two-phase rollback contract, corrected the Alembic head and test command, and preserved all real-cluster, immutable-image, and Axi-license gates. Later recovery tests supersede the validation count captured by this revision.

Revision note (2026-08-26 12:17+08:00): made completed rollback safely reentrant with an exact restored-ServiceAccount marker, added strict lifecycle and manifest-input validation, prohibited restoration to the executor identity, and added a fail-closed read-only/live staging acceptance tool. Current local acceptance is deployment `43 passed` and execution-plane `106 passed, 1 skipped`; real staging has not run.

Revision note (2026-08-26 14:07+08:00): finished backend namespace parameterization and added a non-default namespace contract covering rendered resources, executor identity, the internal Service URL, and the live health probe. The local baseline remains deployment `43 passed` and execution-plane `106 passed, 1 skipped`; real-cluster, immutable-image, and Axi 0.0.11 license/wheel gates remain open after re-audit.

Revision note (2026-08-26 14:07+08:00): added a two-part Axi image supply-chain gate. The build now verifies the official 0.0.11 PyPI wheel SHA-256 and internal RECORD/metadata/entry-point structure while separately requiring explicit written-license confirmation. Cached artifact evidence advances provenance review but does not complete Linux CLI, image, license, or staging gates.

Revision note (2026-08-26 15:25+08:00): verified the official Axi 0.0.11 wheel, upstream tag source, and Linux/Python 3.12 CLI contract; kept written license approval as a separate fail-closed build gate. Built and restricted-smoked the independent analysis runtime, moved it to a pinned Python 3.12.13 Bookworm base, upgraded `python-multipart` to 0.0.32, and obtained zero fixable HIGH/CRITICAL findings from Trivy 0.74.0. Current local acceptance is deployment `55 passed` and execution-plane `118 passed, 1 skipped`; registry publication and real staging remain open.

Revision note (2026-08-26 19:20+08:00): completed isolated-kind server dry-run/apply/live acceptance with Calico, published immutable analysis and runner-backend GHCR digests, fixed Hatch direct-reference and Docker Git prerequisites, and hardened the backend image to `0 HIGH / 0 CRITICAL` on its merged rootfs. Production was not changed; browser two-tenant E2E and Axi license approval remain.

Revision note (2026-08-27 12:37+08:00): completed P6 browser two-tenant acceptance against an isolated PostgreSQL/backend/frontend stack. Alpha and Beta independent browser contexts each showed only their own session, activity, approval, artifact, memory, notification, and schedule data. Bidirectional checks passed for eight list surfaces, four direct reads, five mutations, memory query, event cursor/session filtering, and post-mutation ownership. Sanitized JSON, screenshots, and hashes are under `e2e/omniagent-two-tenant/evidence/`; the dedicated containers, network, volume, image, and plaintext fixture were removed. Axi written-license approval remains the only external gate.
