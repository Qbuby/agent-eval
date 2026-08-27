# Build OmniAgent's Axi execution layer on Kubernetes sandboxes

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be updated whenever implementation advances or the design changes. The repository has no root `PLANS.md`; maintain this file according to the ExecPlan methodology provided by the `execplan` skill.

## Purpose / Big Picture

After this work, an authenticated Agent Eval user can ask OmniAgent questions about approved platform data. OmniAgent will discover a capability through Axi, inspect its schema, and execute it inside a per-chat Kubernetes sandbox. The browser will continue to receive the existing `tool_start` and `tool_end` Server-Sent Events (SSE), while Agent Eval remains the authority for users, tenants, business data, and persisted chat history.

The first releasable slice is read-only. A user asks a question for which no dedicated Agent Eval endpoint exists; OmniAgent discovers `data/search`, `data/describe`, and `data/query` through Axi, composes a bounded query, and a short-lived capability token authorizes only that user's tenant-scoped read. Mutation tools are not installed, raw shell is not exposed to the model, and the entire execution feature can be disabled by restoring the current no-tool allowlist.

## Progress

- [x] (2026-08-24 16:10+08:00) Inspected Agent Eval's OmniAgent proxy, tenant-owned chat sessions, SSE normalization, persistence, and sidecar deployment.
- [x] (2026-08-24 16:20+08:00) Inspected OmniAgent's tool registry, skill provider, sandbox abstraction, Kubernetes session manager, request-level environment injection, retry behavior, and per-thread lifecycle.
- [x] (2026-08-24 16:30+08:00) Inspected Axi 0.0.11 CLI behavior, JSON envelopes, daemon protocol, native-tool registration, configuration, and release metadata.
- [x] (2026-08-24 16:40+08:00) Inspected `agent-sandbox` SandboxTemplate, SandboxClaim, warm pool, router, runtime `/execute` contract, lifecycle, NetworkPolicy behavior, and RBAC examples.
- [x] (2026-08-24 17:38+08:00) Revised the architecture into independently verifiable milestones M0 through M7 and documented the command-level credential boundary.
- [ ] M0 is locally complete except for external license approval: the official 0.0.11 PyPI wheel was downloaded from its pinned URL, verified against SHA-256 `ab9473092cac37e4f00347cd92d9e629424bcba99731c8d082b8031a29d6fbdf`, and exercised on Linux with CPython 3.12.13. The three reviewed data tools, success, business error, timeout, malformed JSON, and token non-disclosure contracts pass. GitHub tag `v0.0.11` points to unsigned commit `290b20e9d584d5d61cdf7bae47a83e142db569da`; all 21 wheel Python modules match that commit tree. Unambiguous written license approval remains unavailable.
- [x] (2026-08-26 12:17+08:00) M1 local implementation: added the three OmniAgent Axi meta-tools through an image overlay and `sitecustomize` registration, with fixed bridge argv, request files, output bounds, token isolation, and fake-sandbox tests.
- [x] (2026-08-26 12:17+08:00) M2: added short-lived, turn-bound, tenant-allowlisted execution tokens with fail-closed decoding and immediate revocation when a tenant leaves the allowlist.
- [ ] M3 partially complete: the independent non-Axi analysis runtime is published at `ghcr.io/qbuby/agent-eval-analysis-runtime@sha256:fc93a846898c79df6dec13ca2028e54712eb604e5a00147b5b389ff9f3b8f6c4`, passes restricted-container and real sandbox smoke tests, and reports zero fixable HIGH/CRITICAL findings under Trivy 0.74.0. The Axi runtime build verifies the reviewed wheel and fails before installation without explicit written-license confirmation; the Axi image remains intentionally unbuilt until that approval is supplied.
- [x] (2026-08-26 19:20+08:00) M4: deployed the pinned agent-sandbox controller/router, CRDs, zero-size warm pool, namespaced RBAC, ServiceAccount, internal Service, and Calico-enforced NetworkPolicy to isolated kind cluster `agent-eval-exec`; server-side dry-run, explicit apply, read-only acceptance, live isolation, TTL deletion, and strict cleanup all passed.
- [x] (2026-08-26 12:17+08:00) M5: implemented the three general data-query tools, governed entity catalog, bounded AST compiler, explicit root/join tenant predicates, native client package, and reviewed investigation skills.
- [x] (2026-08-27 12:37+08:00) M6: `agent-eval-exec` proved executor-authenticated Claim creation, Pod hardening, backend health, Internet and peer denial, controller TTL deletion, strict cleanup, and the published analysis digest; browser two-tenant product-plane acceptance also passed.
- [ ] M7 partially complete: tenant allowlisting, disabled-by-default flags, immutable rendering, identity preflight, interruption-safe two-phase rollback, and exact recovery-state validation are locally accepted; no tenant has been enabled in staging or production.

## Surprises & Discoveries

- Observation: OmniAgent already has the Kubernetes execution abstraction needed by this feature. `D:/program/OmniAgent/omniagent/sandbox/kubernetes.py` creates or reuses a sandbox keyed by normalized `thread_id`; `omniagent/sandbox/base.py` handles bootstrap, skill synchronization, idle recreation, and one transparent recreation attempt after infrastructure failure.
  Evidence: `KubernetesSessionManager._session_key()` normalizes the thread identifier, and `BaseSessionManager.get_session()` returns `_SessionProxy` rather than exposing Kubernetes operations to tools.

- Observation: Axi business errors cannot be classified by process exit code alone. `axi run` can emit `{"status":"error","error":"..."}` while the CLI process exits successfully.
  Evidence: the adapter must parse one complete JSON value and branch on the `status` field.

- Observation: Axi's daemon does not inherit environment changes supplied to later CLI processes. Tenant credentials are therefore reliable for native tools that execute inside `axi run`, but not for tenant-specific MCP processes already launched by the daemon.
  Evidence: the first release must use Axi native tools for Agent Eval data; MCP servers may be added only when their credentials are static and deployment-owned.

- Observation: Axi requires Python 3.12 or newer, while Agent Eval currently targets Python 3.11.
  Evidence: Axi belongs in the sandbox runtime image, not in the Agent Eval backend process.

- Observation: the Axi README says MIT, but the reviewed repository has no `LICENSE` file, no package license declaration, and no license detected by GitHub.
  Evidence: production image publication remains blocked until upstream adds an unambiguous license or legal review records an approved basis for use.

- Observation: uv retained authoritative PyPI identity metadata and a complete unpacked Axi 0.0.11 tree even though it did not retain the original wheel bytes. The published wheel SHA-256 is `ab9473092cac37e4f00347cd92d9e629424bcba99731c8d082b8031a29d6fbdf`; all 25 cached RECORD rows match. Axi 0.0.11 imports Unix `fcntl` unconditionally, so Windows is not a valid CLI proof environment.
  Evidence: `deploy/omniagent/execution-runtime/verify_axi_wheel.py` now mechanically verifies the original wheel identity and internal structure when that wheel is supplied to an approved Linux image build. It still reports license review as separately required.

- Observation: the pinned original wheel and Linux CLI contract are now directly proven. CPython 3.12.13 loaded exactly the three `data` tools through `axi.native_tools`; the loopback PoC observed correct success, `FIELD_DENIED`, `QUERY_TIMEOUT`, and malformed `status=error` envelopes without exposing its canary token.
  Evidence: `scripts/axi-poc/run_smoke.py` passes against `.codex_tmp/venv-axi-poc/bin/axi`; the downloaded wheel remains Git-ignored and is not a repository artifact.

- Observation: `SandboxTemplate.spec.envVarsInjectionPolicy` governs environment variables copied from `SandboxClaim.spec.env` into the Pod at creation time. It does not govern the runtime `/execute` request body.
  Evidence: the Python client sends `{"command": ..., "timeout": ..., "env": ...}` to `/execute`; the runtime merges `req.env` into a fresh subprocess environment. The template can and should remain `Disallowed` while `axi_run` injects a token only into its child process.

- Observation: OmniAgent's sandbox proxy retries infrastructure failures and may recreate the sandbox. Command timeouts are correctly classified as non-retryable, but a connection failure after a server accepted a command is still ambiguous.
  Evidence: the read-only first release tolerates this ambiguity. Every future write or external side-effect tool must require a server-enforced idempotency key before it is installable.

- Observation: the current runtime gathers subprocess output before returning it. Adapter-only truncation would not prevent memory or transport exhaustion.
  Evidence: M3 adds output limits and process-group cleanup in the runtime itself; legitimate large results must be paginated or written as explicit workspace artifacts.

- Observation: changing the adjacent OmniAgent repository is unnecessary for this deployment and would couple release ownership across repositories.
  Evidence: `deploy/omniagent/overlay/sitecustomize.py` registers `omniagent_overlay.axi_tools.provide_tools` only when `OMNIAGENT_AXI_TOOLS_ENABLED` is true; `tests/test_omniagent_runtime/test_axi_overlay.py` validates the bridge without modifying `D:/program/OmniAgent`.

- Observation: a safe execution rollback cannot remove the product plane because job and artifact reads use the product-plane feature flag.
  Evidence: `backend-executor-disable-patch.yaml.tpl` now keeps `OMNIAGENT_PRODUCT_PLANE_ENABLED=true`, and a real `kubectl patch --local` round trip proves reads remain configured after execution credentials, worker, runner, and scanner settings are removed.

## Decision Log

- Decision: keep four responsibilities separate: Agent Eval owns identity and business data; OmniAgent owns reasoning, tool selection, and turn state; `agent-sandbox` owns Kubernetes workload lifecycle; Axi owns capability discovery and invocation inside a sandbox.
  Rationale: these boundaries reuse existing code and prevent the backend from becoming a shell executor or the model from becoming a Kubernetes client.
  Date/Author: 2026-08-24 / Codex.

- Decision: use a thin control plane and a thick sandbox. Axi, its daemon, approved native-tool wheels, and runtime helpers are installed only in the sandbox image.
  Rationale: executable dependencies and tool credentials then share the sandbox lifetime and disappear when its SandboxClaim expires.
  Date/Author: 2026-08-24 / Codex.

- Decision: expose only `axi_search`, `axi_describe`, and `axi_run` as structured OmniAgent tools, plus `skill` and the minimum bounded file readers needed by an approved workflow. Do not expose raw `bash` in production.
  Rationale: progressive discovery avoids placing every business schema in the model context, while structured wrappers validate names, arguments, timeouts, output, and secret handling.
  Date/Author: 2026-08-24 / Codex.

- Decision: keep one sandbox per OmniAgent chat thread and use the existing `ae-chat-<uuid>` thread identifier.
  Rationale: this matches OmniAgent's session scope and isolates files and Axi daemon state between conversations.
  Date/Author: 2026-08-24 / Codex.

- Decision: mint a separate five-minute execution capability token for each assistant turn. Pass it through `configurable.execution_auth`, and inject it only into the `axi_run` subprocess as `AGENT_EVAL_EXECUTION_TOKEN`.
  Rationale: a browser login token is too broad, and generic `sandbox_env` or `mcp_auth` would make the credential available to unrelated shell or skill commands.
  Date/Author: 2026-08-24 / Codex.

- Decision: leave `envVarsInjectionPolicy: Disallowed` on the SandboxTemplate.
  Rationale: the feature needs command-level `/execute.env`, not claim-level Pod mutation. Disallowing claim injection prevents users of the claim API from altering the runtime's long-lived environment.
  Date/Author: 2026-08-24 / Codex.

- Decision: release only read-only native tools. Mutation requires a later persisted approval/resume protocol and an idempotency key enforced by the business API.
  Rationale: prompts are not authorization, and transparent infrastructure recovery makes unkeyed side effects unsafe.
  Date/Author: 2026-08-24 / Codex.

- Decision: keep local Docker Compose in no-tool mode. Use WSL Docker to build and inspect images, but use a real Kubernetes staging namespace or a disposable local cluster for sandbox behavior.
  Rationale: Docker Compose cannot faithfully validate SandboxClaim leasing, router DNS, NetworkPolicy, warm-pool rotation, or namespaced RBAC.
  Date/Author: 2026-08-24 / Codex.

- Decision: deliver the Axi meta-tools as an Agent Eval-owned overlay rather than modifying the adjacent OmniAgent source tree.
  Rationale: the overlay keeps ownership and rollback in this repository, uses the existing provider registry, and is enabled only by explicit deployment environment variables.
  Date/Author: 2026-08-26 / Codex.

- Decision: rollback execution in two deployments while preserving the read-only product plane.
  Rationale: the first rollout stops token minting and job claiming before credentials are removed; the second restores the original ServiceAccount and removes dormant execution configuration while retaining historical jobs, events, artifacts, and authorized downloads.
  Date/Author: 2026-08-26 / Codex.

- Decision: treat artifact identity and permission to use Axi as independent gates.
  Rationale: a byte-for-byte match to the published PyPI wheel proves provenance, metadata, entry points, and RECORD integrity, but the package metadata has no License field and a README assertion is not the same as recorded legal approval. The image build requires both the wheel verifier and an explicit license-review confirmation.
  Date/Author: 2026-08-26 / Codex.

## Outcomes & Retrospective

The control plane, governed data surface, overlay tools, Kubernetes analysis adapter, deployment resources, rollout gates, rollback, and staging acceptance automation now exist. The current execution-plane suite reports `124 passed, 1 skipped`; deployment-only acceptance reports `60 passed`, with an additional `23 passed` for the final Docker/Hatch build contracts. The isolated `agent-eval-exec` kind cluster runs pinned agent-sandbox commit `a9db14672e77fbd15981fb2af9b73934e29b0cfe`; server dry-run, apply, least-privilege RBAC, Pod hardening, backend health, Calico Internet/peer isolation, TTL deletion, and strict cleanup passed. The published analysis runtime digest is `sha256:fc93a846898c79df6dec13ca2028e54712eb604e5a00147b5b389ff9f3b8f6c4`. The runner backend worktree image is `sha256:fe886eb36f9549b9d2bf6bd65b5e8841e1b57ed6000d4c2316f701435ea31527`, contains SDK `0.1.dev512+ga9db14672`, passes `/health`, and scans at `0 HIGH / 0 CRITICAL` over its merged rootfs. Browser two-tenant product-plane acceptance passed in both directions with sanitized evidence and strict fixture cleanup. Production was not changed. The remaining gate is written Axi 0.0.11 license approval and the approved Axi image that depends on it.

## Context and Orientation

The main repository is `D:/program/agent_eval`. `src/agent_eval/api/routers/omniagent.py` authenticates browser requests and creates tenant-owned sessions and messages. `src/agent_eval/services/omniagent_chat.py` sends exactly two top-level fields, `question` and `configurable`, to OmniAgent; it translates upstream LangGraph events into SSE and persists final text plus tool-call records. `frontend/src/services/omniagent.ts` consumes this stream. `deploy/omniagent/config.yaml` lists environment-selected tool names whose defaults cannot match a real tool, so the deployed agent remains in no-tool mode until the execution overlay and allowlist are explicitly enabled.

The sibling repository `D:/program/OmniAgent` supplies the agent runtime. `omniagent/tools/registry.py` aggregates providers. `omniagent/tools/__init__.py` registers core and dynamic providers. `omniagent/sandbox/base.py` defines `BaseSandboxSession.execute(command, timeout, env)` and per-thread session reuse. `omniagent/sandbox/kubernetes.py` sends each command to an `agent-sandbox` runtime. Skills are instructions loaded by `omniagent/tools/skill.py`; they are not an authorization mechanism.

Axi is an agent-oriented command-line interface. Version 0.0.11 exposes `axi search`, `axi describe`, and `axi run`, uses compact JSON, and discovers native Python tools from installed entry points. A daemon is a background process kept inside one sandbox Pod to maintain MCP connections; it dies with that Pod.

`agent-sandbox` defines namespaced Kubernetes custom resources. A SandboxTemplate describes a runtime Pod. A SandboxClaim requests one sandbox for a conversation. A SandboxWarmPool keeps prestarted instances available. A router forwards `/execute`, `/upload`, and `/download` calls to the selected runtime. The model never receives Kubernetes credentials and never creates these objects directly.

The target request path is:

    Browser -> Agent Eval backend -> OmniAgent sidecar -> sandbox router
      -> per-thread SandboxClaim/Pod -> Axi CLI -> approved native tool
      -> Agent Eval internal tool API

The Sandbox Pod cannot call the backend's loopback address because it is a different Pod. M4 therefore requires a dedicated ClusterIP service or an existing stable internal service name for the Agent Eval tool API, and NetworkPolicy allows only that destination plus cluster DNS.

## Security and Trust Boundaries

Agent Eval mints an execution JSON Web Token (JWT), separate from the browser's login JWT. It contains `iss=agent-eval`, `aud=omniagent-execution`, `sub=<user UUID>`, `tenant_id`, `session_id`, `message_id`, `scopes`, `jti`, `iat`, and `exp`. Internal tool endpoints derive tenant filters from the verified token; they never trust tenant or user identifiers supplied in tool arguments. Authentication-disabled development mode keeps execution disabled unless an explicit development-only flag is set.

`OmniAgentChatService.build_payload()` places the opaque token under `configurable.execution_auth.token`. OmniAgent does not add this field to prompts or tool schemas. `axi_search` and `axi_describe` receive no token. `axi_run` passes a one-entry environment map to `BaseSandboxSession.execute`; the token must never enter the command string, Axi arguments, model-visible events, logs, Langfuse traces, or persisted message JSON.

The runtime Pod uses a non-root UID/GID, `automountServiceAccountToken: false`, `allowPrivilegeEscalation: false`, dropped Linux capabilities, `seccompProfile: RuntimeDefault`, resource and ephemeral-storage limits, and a read-only root filesystem where the runtime supports it. Writable `emptyDir` volumes are limited to `/workspace`, `/home/user/.axi`, and `/tmp`. Network egress is deny-by-default.

The runtime caps stdout and stderr before returning an HTTP response and kills the whole subprocess group on timeout or request cancellation. The OmniAgent adapter then applies its own smaller JSON and preview limits. A result that exceeds limits is an explicit `OUTPUT_LIMIT` error, not silently truncated valid JSON.

## Plan of Work

### M0: Axi license and exact CLI proof

Create a disposable Python 3.12 environment outside production images and install exactly `axi-cli==0.0.11`. Add a tiny local native tool with no network or credentials and capture the exact output of search, describe, successful run, business-error run, malformed arguments, and timeout. Record the wheel hash and reviewed upstream commit. The milestone passes only when every command emits one parseable JSON value matching the expected contract. Production publication remains blocked until the license issue is resolved in writing.

Store the repeatable smoke fixture under `tests/fixtures/axi-poc/` or `scripts/axi-poc/` in Agent Eval. It must contain no downloaded wheel and no secrets. If Axi's observed schema differs from this plan, update this living document before implementing M1.

### M1: OmniAgent Axi meta-tools

This milestone is implemented without modifying the sibling OmniAgent repository. `deploy/omniagent/overlay/omniagent_overlay/axi_tools.py` provides `axi_search`, `axi_describe`, and `axi_run`; `deploy/omniagent/overlay/sitecustomize.py` registers that provider only when `OMNIAGENT_AXI_TOOLS_ENABLED` is true. Production config selects the three names explicitly and never relies on an empty allowlist because an empty allowlist means all tools.

Validate `tool_name` with a conservative `server/tool` pattern. Serialize `arguments` with deterministic JSON and write them to a generated file below `/workspace/.axi/requests/` through `session.write_file`; the shell command reads that file as one quoted `--json` argument. The model cannot choose the request path. Clamp `top_k` and timeout to documented limits. Parse exactly one JSON value and reject trailing non-whitespace output, invalid UTF-8 replacement artifacts, wrong top-level types, and `status=error`.

Return a stable wrapper envelope such as `{"ok":true,"data":...,"meta":...}` or `{"ok":false,"error":{"code":"AXI_ERROR","message":"..."}}`. Error messages must be useful but must not contain environment values. Add fake-session tests for command construction, nested JSON, hostile tool names, missing authorization on run, Axi error envelopes with exit code zero, process failure, timeout, oversized output, and token absence from commands and results.

### M2: turn-scoped capability tokens

This milestone is implemented in `src/agent_eval/omniagent_runtime/security.py`. It defines immutable `ExecutionPrincipal`, token minting, fail-closed decoding, scope checks, a five-minute default lifetime, and the tenant rollout allowlist. The browser login token is rejected by execution decoding, and removing a tenant from the allowlist immediately rejects an already minted token.

Pass `tenant_id` and `user_id` from `src/agent_eval/api/routers/omniagent.py` into each `OmniAgentChatService`. Mint the token only after the assistant message row exists so its UUID can be bound in the claims. Keep the upstream payload's two top-level fields unchanged and add only `configurable.execution_auth`. Ensure retry creates a new assistant message and therefore a new token.

Add tests that decode claims, expire the token, substitute tenant/session/message identifiers, try a normal browser JWT, and search persisted message/tool JSON for the literal test token. No Kubernetes dependency is needed for this milestone.

### M3: hardened Axi sandbox image

The hardened runtime source exists under `deploy/omniagent/execution-runtime/`, with the bounded runtime implementation shared from `deploy/omniagent/analysis-runtime/`. The Dockerfile is intentionally not buildable as a release artifact until the reviewed Axi 0.0.11 wheel hash replaces its sentinel. It uses immutable base-image inputs, runs non-root, configures `AXI_RICH=0`, and installs the separately built `packages/agent-eval-axi-tools/` package. Do not install tools dynamically during a chat.

Use or patch the runtime so `/execute` accepts an optional per-command environment map, caps stdout/stderr, starts a new process group, and kills that group on timeout and cancellation. It must log command length, duration, and exit status, never the command body or environment. Add contract tests proving that an injected value is visible only to that subprocess, is absent on the next command, output limits work, and timed-out child processes do not survive.

Because Docker Desktop is not the active engine, run image commands through the WSL distribution where `docker info` succeeds. Compose may build the image, but sandbox behavior is not accepted until M4 runs in Kubernetes.

### M4: Kubernetes isolation and lifecycle

Create manifests below `deploy/k8s/omniagent-execution/` for a dedicated OmniAgent ServiceAccount, namespaced Role and RoleBinding, SandboxTemplate, staging SandboxWarmPool, and NetworkPolicy. Use exact resource names in one namespace and bind only the OmniAgent ServiceAccount. The Sandbox Pod has no service-account token. The template sets `spec.service: true`, `envVarsInjectionPolicy: Disallowed`, managed network policy, port 8888 probes, the M3 security context, and resource limits.

Grant only the verbs used by the reviewed client to SandboxClaims and the minimum reads needed for templates and sandbox status. Validate with `kubectl auth can-i` that the ServiceAccount can operate in the sandbox namespace and cannot do so in unrelated namespaces. Do not copy the upstream example's cluster-wide binding.

Permit ingress to runtimes only from the sandbox router. Permit egress only to cluster DNS and the dedicated Agent Eval internal-tool ClusterIP/port. Prove both an allowed request and a denied Internet request. Size the staging warm pool from measured cold-start latency; do not copy the example value blindly. Record that updating a SandboxTemplate does not replace already-warm Pods, so image upgrades require explicit pool rotation.

### M5: general data-query capability and skills

Implement the governed query design in `execplan/omniagent-capabilities-and-tools.md`. The public read surface contains `data/search`, `data/describe`, and `data/query`, not a fixed endpoint for each anticipated business question. OmniAgent discovers logical entities, inspects safe fields and relationships, and submits a typed query AST that Agent Eval validates and compiles. The server owns the finite semantic catalog; the model owns the open-ended composition of read questions.

Create the separately buildable package at `packages/agent-eval-axi-tools/` with one reviewed native entry-point module exposing the three data tools. Its client calls only three constant paths below `/internal/omniagent-data/v1`, reads `AGENT_EVAL_EXECUTION_TOKEN` and deployment-owned `AGENT_EVAL_INTERNAL_URL`, and never accepts tenant ID, user ID, URL, HTTP method, headers, raw SQL, physical table or column names, file paths, or executable code. Add the catalog, AST validator, query compiler, source adapters, projection, redaction, pagination, audit, and fixed router under `src/agent_eval/omniagent_data/` and `src/agent_eval/api/routers/omniagent_data.py`.

All database and external-provider access remains inside Agent Eval. The Sandbox Pod must not contain a PostgreSQL driver credential or connect directly to PostgreSQL: direct SQL would bypass the current SQLAlchemy ORM tenant event in `src/agent_eval/db.py`. Agent Eval must derive the tenant from the execution token, force `superadmin=False` for general data queries, add an explicit tenant predicate to every tenant-owned root and join, and enforce read-only transactions, field and relationship allowlists, cost limits, timeouts, output limits, and redaction.

Add `deploy/omniagent/skills/data-investigation/SKILL.md` to teach search, describe, minimal query construction, evidence checking, and stopping conditions. Domain-specific skills may supply terminology and interpretation guidance, but they must compose the same three data tools and contain no credentials, URLs, SQL, shell, or authorization logic. All writes remain fixed business capabilities with persisted preparation, browser approval, immutable arguments, and server-enforced idempotency.
### M6: end-to-end acceptance and audit
Enable the read-only profile in staging and run a browser chat asking `列出当前租户最近的数据集，并说明每个数据集类型`. Observe search, describe, and run as separate first-class tool events, followed by a Chinese answer and a terminal `completed` assistant message. Verify the same records persist after browser refresh.

Repeat with two tenants containing distinguishable fixtures and attempt prompt, argument, header, and URL manipulation. Verify no cross-tenant names appear. Search backend, OmniAgent, runtime, and Langfuse logs plus persisted message JSON for a unique canary token and expect no matches. Verify token expiry, network denial, absent Kubernetes token, command timeout without replay, browser disconnect producing `cancelled`, idle TTL deletion, recreation, skill resynchronization, and warm-pool replenishment.

The staging smoke script now lives at `deploy/k8s/omniagent-execution/staging_smoke.py` and returns non-zero on any failed assertion or cleanup. Its default mode is read-only. Its live mode requires the exact `create-omniagent-staging-smoke-claims` confirmation before creating two short-lived claims as the executor ServiceAccount. Preserve current SSE event names and frontend types unless an acceptance test proves a contract change is necessary.

### M7: gradual release and rollback

Add a server-owned feature flag defaulting to disabled. Roll out in this order: deploy dormant internal endpoints, deploy sandbox resources and warm pool, deploy OmniAgent with wrappers registered but not allowlisted, then enable the read-only allowlist for an internal tenant. Expand by tenant only after latency, error rate, token failures, sandbox saturation, and cleanup metrics are acceptable.

Rollback uses `DISABLE_EXECUTION=1 MODE=apply` in `deploy/k8s/omniagent-execution/apply.sh`. Phase one sets execution and worker flags false and waits for rollout. It then removes the optional Axi tool environment, deletes dormant execution configuration, restores the ServiceAccount recorded during enablement, writes the restored identity to an audit annotation, and waits for a second rollout. The product-plane flag remains true so history, jobs, artifacts, and downloads stay readable. The script fails before any mutation if the previous ServiceAccount annotation is missing, the lifecycle annotation conflicts with the Pod identity, or a previous/restored identity is `omniagent-executor`. If cleanup succeeded but the caller lost the final rollout response, rerunning rollback issues no patch and resumes rollout observation only when execution is marked disabled, the previous identity marker is absent, and the restored identity marker exactly matches the current ServiceAccount. A later enable removes the restored marker. All names and immutable image references are validated before template substitution, preventing newline, quote, and extra-digest-marker injection into rendered YAML.

Write tools are a later plan. Before any are installed, add persisted approval/resume, a server-owned risk classification, a single-use approval nonce, and a business idempotency key derived from the approved execution record. Destructive operations require approval every time.

## Concrete Steps

Run M0 from `D:/program/agent_eval`. When using the current WSL Docker engine from PowerShell, first identify the distribution:

    wsl.exe -l -q
    wsl.exe -d <distribution> -- bash -lc 'docker info >/dev/null && echo docker-ok'

Then run the Axi proof in a Python 3.12 environment and save only reproducible fixture source and expected JSON:

    uv venv --python 3.12 .venv-axi-poc
    uv pip install --python .venv-axi-poc/bin/python 'axi-cli==0.0.11'
    .venv-axi-poc/bin/axi search 'echo'
    .venv-axi-poc/bin/axi describe fixture/echo
    .venv-axi-poc/bin/axi run fixture/echo --json '{"text":"hello"}'

On Windows PowerShell outside WSL, use `.venv-axi-poc/Scripts/axi.exe`. Expect one JSON value from each command. Record `python --version`, `axi --version`, the wheel SHA-256, and the upstream commit. Do not publish an image while the license gate is unresolved.

Run M1 tests from `D:/program/OmniAgent`:

    uv sync --frozen
    uv run pytest tests/test_tools_axi.py tests/test_sandbox_session_proxy.py tests/test_skill.py
    uv run ruff check omniagent/tools/axi.py tests/test_tools_axi.py

Run M2 and M5 tests from `D:/program/agent_eval`:

    python -m pytest tests/test_api/test_omniagent_chat.py tests/test_auth/test_omniagent_execution.py tests/test_api/test_omniagent_tools.py
    python -m compileall -q src/agent_eval packages/agent-eval-axi-tools
    npm --prefix frontend run build

Build M3 through WSL from the repository mounted at `/mnt/d/program/agent_eval`:

    wsl.exe -d <distribution> -- bash -lc 'cd /mnt/d/program/agent_eval && docker build -f deploy/k8s/omniagent-execution/sandbox/Dockerfile -t agent-eval-axi-sandbox:dev .'
    wsl.exe -d <distribution> -- bash -lc 'docker run --rm agent-eval-axi-sandbox:dev axi --version'

For M4, run the repository acceptance tool against staging after the reviewed analysis image and resources exist. The first command is read-only. The second creates temporary claims and therefore requires an exact confirmation:

    python deploy/k8s/omniagent-execution/staging_smoke.py --tenant-id '<tenant-uuid>' --analysis-image 'registry.example/analysis-runtime@sha256:<64-hex>'
    python deploy/k8s/omniagent-execution/staging_smoke.py --tenant-id '<tenant-uuid>' --analysis-image 'registry.example/analysis-runtime@sha256:<64-hex>' --live --confirm-live create-omniagent-staging-smoke-claims

Expect `READ_ONLY_ACCEPTANCE_OK`, `LIVE_CLAIM_ACCEPTANCE_OK`, and final `STAGING_ACCEPTANCE_OK`. Applying manifests, changing RBAC/Secrets, publishing images, or modifying production remains a high-impact operation and requires explicit operator confirmation at execution time.

## Validation and Acceptance

The implementation is accepted only when all of the following behaviors are demonstrated:

1. `axi_search` finds `data/search`, `data/describe`, and `data/query`; a question with no dedicated business endpoint is answered from a validated AST over registered logical entities.
2. Tool start and end events travel from OmniAgent through Agent Eval SSE to the browser and remain after refresh.
3. Two tenants cannot read one another's data even when the prompt or tool arguments contain the other tenant's identifiers.
4. The model, events, persisted JSON, application logs, and traces contain no login JWT or execution JWT.
5. The Sandbox Pod has no Kubernetes token, runs non-root, and cannot access denied network destinations; the approved internal API remains reachable.
6. An expired or wrongly scoped capability token fails closed with a stable authorization error.
7. Browser disconnection leaves the assistant message `cancelled`; command timeout kills descendants and never automatically replays a side-effecting operation.
8. Runtime and adapter output limits prevent unbounded responses and report a clear `OUTPUT_LIMIT` result.
9. After idle TTL, the claim and Pod disappear; a later call recreates the sandbox and re-synchronizes skills. The warm pool replenishes.
10. Updating the template and rotating the warm pool causes new Pods to use the new image digest; old ready Pods are not silently retained.
11. Restoring `tools: ["__no_tools_until_designed__"]` immediately removes model-visible execution capability while ordinary chat remains healthy.
12. Existing OmniAgent chat tests, targeted backend tests, Python compilation, lint checks, and the frontend production build pass.

## Idempotence and Recovery

M0 uses a disposable environment and can be repeated. Image builds use immutable base digests, exact package versions, and recorded hashes. Kubernetes YAML is declarative and can be re-applied, but template changes require deliberate warm-pool rotation. Never delete arbitrary runtime Pods as normal cleanup; delete or expire the owning SandboxClaim so controllers reconcile state.

If Axi daemon state is corrupt, expire that conversation's claim; its home directory is ephemeral. If a rollout fails, disable the feature and tool allowlist before removing infrastructure. Internal endpoints remain dormant behind the same server-side feature flag. Any future audit migration must have a tested downgrade and must not be required for basic chat startup.

Do not retry an ambiguous write. The first release contains no write tools. A future write endpoint must return the stored result for a repeated idempotency key and must reject a key reused with different arguments.

## Artifacts and Notes

The expected Axi contract established in M0 is approximately:

    axi search "query data across evaluations and replies"
    -> [{"name":"data/search",...},{"name":"data/describe",...},{"name":"data/query",...}]

    axi describe data/query
    -> {"name":"query","server":"data","input_schema":{"$ref":"QueryRequest"},...}

    axi run data/query --json '{"from":"evaluation_results","select":[{"field":"error_class"},{"aggregate":"count","as":"failures"}],"where":{"field":"execution_status","op":"eq","value":"failed"},"group_by":["error_class"],"limit":20}'
    -> {"status":"success","data":{"columns":[...],"rows":[...],"as_of":"..."}}
A business error may still accompany process exit zero:

    {"status":"error","error":"execution token expired"}

The production OmniAgent profile will resemble:

    agent:
      tools: [axi_search, axi_describe, axi_run, skill, ls, read, glob, grep]
    sandbox:
      executor: kubernetes
      idle_timeout: 1800
      kubernetes:
        template_name: agent-eval-axi
        namespace: agent-sandbox-system
        sandbox_ready_timeout: 90

Do not enable `write`, `edit`, or `bash` in the initial profile. The execution token remains outside model arguments:

    configurable:
      thread_id: ae-chat-<uuid>
      language: 请用中文回复
      execution_auth:
        token: <opaque five-minute JWT>

## Interfaces and Dependencies

In `D:/program/OmniAgent/omniagent/tools/axi.py`, define input models using `Field` bounds and `default_factory=dict`:

    class AxiSearchInput(BaseModel):
        query: str
        top_k: int = Field(default=5, ge=1, le=20)

    class AxiDescribeInput(BaseModel):
        tool_name: str

    class AxiRunInput(BaseModel):
        tool_name: str
        arguments: dict[str, Any] = Field(default_factory=dict)
        timeout: float = Field(default=120.0, ge=1.0, le=300.0)

    async def provide_axi_tools(names: list[str] | None = None) -> list[StructuredTool]

Register it with:

    ToolRegistry.register("axi", provide_axi_tools)

The implementations obtain the current session with `get_session_manager().get_session(sandbox_scope(configurable))`. They call `BaseSandboxSession.execute`; they do not import Axi into the OmniAgent control container and do not call Kubernetes APIs directly.

In `src/agent_eval/auth/omniagent_execution.py`, define:

    @dataclass(frozen=True)
    class ExecutionPrincipal:
        tenant_id: UUID
        user_id: UUID
        session_id: UUID
        message_id: UUID
        scopes: frozenset[str]
        jti: UUID
        expires_at: datetime

    def create_execution_token(*, tenant_id: UUID, user_id: UUID,
                               session_id: UUID, message_id: UUID,
                               scopes: Collection[str], ttl_seconds: int = 300) -> str

    def decode_execution_token(token: str, *, required_scope: str,
                               session_id: UUID | None = None,
                               message_id: UUID | None = None) -> ExecutionPrincipal

The native package declares one reviewed entry-point module that exposes the required public Axi names:

    [project.entry-points."axi.native_tools"]
    data = "agent_eval_axi_tools.data"

That module provides `data/search`, `data/describe`, and `data/query`. Its client has three constant internal endpoint paths and no generic request method whose path, URL, HTTP method, headers, or database destination can be selected by model input. The Sandbox image contains no Agent Eval database credential.
Use exact `axi-cli==0.0.11` plus a recorded wheel hash only after the license gate passes. Pin `k8s-agent-sandbox` and its runtime image to reviewed commits or immutable digests, never `main` or `latest`. The sandbox image targets Python 3.12; Agent Eval may remain on Python 3.11.

Revision note (2026-08-24 17:38+08:00): reorganized the initial design into M0-M7 gates; clarified that claim-level `envVarsInjectionPolicy` and command-level `/execute.env` are independent; added runtime output/cancellation requirements, retry/idempotency constraints, WSL Docker commands, internal-service networking, staged release, and explicit rollback. The revision preserves the original thin-control-plane architecture and read-only first release.

Revision note (2026-08-24 17:58+08:00): replaced the provisional five-tool M5 example with the authoritative RO-1 catalog of eighteen read-only tools and four workflow skills from `execplan/omniagent-capabilities-and-tools.md`; updated Axi examples and native entry-point domains. The change keeps execution mechanics in this plan and business capability contracts in the companion plan.
Revision note (2026-08-25 14:53+08:00): changed M5 from eighteen task-specific read tools to three general data tools backed by a governed entity catalog and typed query AST. Added the explicit rule that Sandbox Pods never receive database credentials or connect directly to PostgreSQL, because doing so would bypass Agent Eval's SQLAlchemy tenant boundary. Fixed write operations, approval, and idempotency remain unchanged.

Revision note (2026-08-26 12:17+08:00): reconciled M1-M5 with the implementation in this repository, documented the overlay-based provider registration, and recorded the locally exercised two-phase rollback. M0 licensing and wheel provenance, immutable image publication, server-side dry-run, and live staging M6 acceptance remain open.

Revision note (2026-08-26 12:17+08:00): strengthened M7 rollback recovery with a restored-ServiceAccount audit marker, exact lifecycle matching, conservative template-input validation, and a prohibition on restoring the executor identity. Later staging-smoke work supersedes the validation counts captured by this revision.

Revision note (2026-08-26 12:17+08:00): added fail-closed read-only/live staging automation with executor-authenticated temporary claims, exact template and Pod security checks, backend health, network isolation, observed TTL deletion, and strict cleanup. Local acceptance is deployment `43 passed` and execution-plane `106 passed, 1 skipped`; no real staging context was available.

Revision note (2026-08-26 14:07+08:00): completed backend namespace parameterization across rendered resources and staging acceptance. A non-default `agent-eval-review` contract now proves quoted YAML namespace values, namespace selectors, the internal Service URL, the executor ServiceAccount subject, and the live `/health` FQDN all use the same validated namespace. Local acceptance remains deployment `43 passed` and execution-plane `106 passed, 1 skipped`; Axi 0.0.11 license/wheel provenance, immutable image publication, and real staging remain open because local Axi is 0.0.10, Docker is unavailable, and `kubectl` has no current context.

Revision note (2026-08-26 14:07+08:00): added a fail-closed Axi wheel supply-chain verifier and corrected M0 evidence. PyPI identity metadata fixes the 0.0.11 wheel URL and SHA-256, and the cached unpacked tree passes every RECORD hash; an approved build now also validates metadata, wheel tags, entry points, archive safety, and RECORD before installation. Original wheel bytes, Linux CLI proof, written license approval, image publication, and real staging remain open.

Revision note (2026-08-26 15:25+08:00): completed the local Axi provenance and CLI proof against the official wheel and GitHub tag commit `290b20e9d584d5d61cdf7bae47a83e142db569da`; all wheel Python modules match the tag tree. Built the independent analysis runtime from pinned Python 3.12.13 Bookworm, exercised it under restricted container settings, and obtained a Trivy 0.74.0 result of zero fixable HIGH/CRITICAL findings after upgrading `python-multipart` to 0.0.32. Current local acceptance is deployment `55 passed` and execution-plane `118 passed, 1 skipped`; written Axi license approval, registry publication, and real staging remain open.

Revision note (2026-08-26 19:20+08:00): published the non-Axi analysis runtime and runner-enabled backend to GHCR, repaired Hatch direct-reference and Docker Git build prerequisites, applied Debian security updates, and removed build-only Python tooling from the runtime image. The published analysis digest passed real read-only/live acceptance in isolated kind with Calico NetworkPolicy and no leaked Claims; the runner backend merged rootfs reports `0 HIGH / 0 CRITICAL`. Axi remains intentionally unbuilt behind written-license approval.

Revision note (2026-08-27 12:37+08:00): completed M6 browser two-tenant product-plane acceptance. Independent Alpha/Beta browser contexts passed seven UI surfaces and bidirectional list, direct-read, mutation, query, cursor/session-filter, and post-mutation ownership isolation. Sanitized JSON, screenshots, and SHA-256 evidence are under `e2e/omniagent-two-tenant/evidence/`; all dedicated runtime resources and the plaintext fixture were removed. Axi remains intentionally unbuilt and disabled pending written license approval.
