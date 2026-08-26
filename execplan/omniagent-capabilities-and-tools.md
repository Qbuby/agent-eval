# Build OmniAgent's general data-query capability and governed actions

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be updated whenever implementation advances or the design changes. The repository has no root `PLANS.md`; maintain this file according to the ExecPlan methodology provided by the `execplan` skill.

This plan refines the business-capability portion of `execplan/omniagent-axi-k8s-execution-layer.md`. It is self-contained: a contributor can implement the capability layer from this file without relying on prior conversation.

## Purpose / Big Picture

After this work, an authenticated internal user can ask OmniAgent a new read-only question even when Agent Eval has no endpoint designed for that exact question. OmniAgent discovers the exposed data entities, inspects their fields and relationships, builds a structured query plan, and executes that plan through one governed query gateway. For example, it can answer which failed runs consumed the most tokens, or whether cases with missing current replies have lower acceptance, without developers first adding a dedicated report endpoint.

The first release exposes three general Axi data tools: `data/search`, `data/describe`, and `data/query`. The system owns a finite catalog of safe entities, fields, relationships, and aggregates, but it does not own a finite catalog of questions. The model composes queries at run time. It never receives database credentials and cannot submit SQL, URLs, HTTP methods, headers, file paths, tenant identifiers, or arbitrary code.

This is governed query access, not unrestricted database access. `data/query` accepts a query abstract syntax tree, abbreviated AST: structured JSON representing selections, filters, approved relationships, groups, aggregates, sorting, and pagination. Agent Eval validates every referenced name against a server-owned catalog, injects tenant constraints, compiles the AST to SQLAlchemy or an approved external-source adapter, runs a read-only bounded operation, and projects and redacts the result before it leaves the backend.

Write capabilities remain fixed business operations because they must preserve domain invariants, approvals, and idempotency. They use a separate prepare, approve, execute, and observe protocol. A read token cannot become a write token, and free-form conversation cannot authorize a mutation.

## Progress

- [x] (2026-08-24 17:40+08:00) Enumerated routers and domain workflows for datasets, candidates, benchmarks, evaluations, replies, metrics, routing, scheduling, and governance.
- [x] (2026-08-24 17:48+08:00) Designed an initial catalog of eighteen read-only task-specific tools, four skills, six risk classes, and a later write-action protocol.
- [x] (2026-08-25 10:00+08:00) Reframed the read layer around open-ended questions: three general data tools over a governed entity catalog, with the former task-specific tools retained as acceptance scenarios rather than interfaces.
- [x] (2026-08-25) C0: defined the data catalog, query AST, policy, errors, limits, and source-adapter contracts.
- [x] (2026-08-25) C1: implemented catalog search and entity description over nine registered safe entities.
- [x] (2026-08-25) C2: implemented the PostgreSQL/SQLAlchemy query compiler with explicit root and relationship tenant predicates, bounded query shape, timeouts, output limits, and redaction.
- [ ] C3: add approved external-source adapters and computed semantic fields without exposing credentials or arbitrary network access.
- [ ] C4 partially complete: reviewed data-investigation and domain skills exist; live model evaluations of open-ended questions remain.
- [x] (2026-08-25) C5: connected scopes to turn tokens and added local isolation, redaction, pagination, bounded-output, and query-cost rejection tests.
- [x] (2026-08-25) C6: added persisted prepare/approve/execute/observe records and browser-bound approval before any fixed write action executes.
- [ ] C7 partially complete: fail-closed tenant allowlisting exists, but no cohort has been enabled in staging or production and usefulness metrics are not yet available.

## Surprises & Discoveries

- Observation: a fixed catalog of task-specific read tools still requires developers to predict questions in advance.
  Evidence: the earlier eighteen-tool design could answer known dataset, evaluation, telemetry, and reply workflows, but a new cross-domain question still required a new endpoint and Axi schema.

- Observation: Agent Eval already has enough relational structure to support safe composition, but that structure is not itself a safe model-facing schema.
  Evidence: `src/agent_eval/db_models/tables.py` contains tenant-owned runs, results, scores, datasets, cases, and reply records, including sensitive JSON fields that must not be exposed directly.

- Observation: the existing tenant filter cannot justify giving the sandbox a database connection.
  Evidence: `src/agent_eval/db.py` enforces tenancy through SQLAlchemy ORM `do_orm_execute` events and `TenantMixin`. A direct `psql` connection or raw Core query can bypass that application-level boundary.

- Observation: some data is outside PostgreSQL or currently lacks trustworthy tenant attribution.
  Evidence: conversation datasets use Langfuse, and current Langfuse metric ingestion may stamp the internal sentinel tenant. External data must use a separate approved adapter and remains disabled when caller attribution cannot be proven.

- Observation: business semantics are not always raw columns.
  Evidence: evaluation outcomes intentionally distinguish `execution_status`, `evaluation_status`, and `acceptance_decision`; generated-reply readiness distinguishes missing, empty, failed, and current versions. The catalog needs documented computed fields rather than asking the model to infer these meanings from storage JSON.

- Observation: not every read is cheap or harmless.
  Evidence: broad joins, unbounded text, trace payloads, and high-cardinality grouping can exhaust memory or disclose sensitive content even when no row is mutated.

## Decision Log

- Decision: expose `data/search`, `data/describe`, and `data/query` as the complete general read interface.
  Rationale: progressive discovery lets OmniAgent formulate new questions while keeping the model-facing surface small and stable.
  Date/Author: 2026-08-25 / Codex.

- Decision: accept a typed query AST, never raw SQL or a natural-language query executed directly.
  Rationale: the server can validate entities, fields, operators, relationships, cardinality, limits, and cost before compiling a query; a SQL parser and denylist would be a weaker authorization boundary.
  Date/Author: 2026-08-25 / Codex.

- Decision: run all query compilation and data access in Agent Eval, not in the Kubernetes sandbox.
  Rationale: the sandbox must not receive PostgreSQL, Langfuse, or provider credentials. Agent Eval is the only layer that owns tenant identity and safe projections.
  Date/Author: 2026-08-25 / Codex.

- Decision: use a logical entity catalog rather than exposing table and column names automatically.
  Rationale: logical names remain stable across schema changes and can omit secrets, normalize domain concepts, define approved relationships, and document computed fields.
  Date/Author: 2026-08-25 / Codex.

- Decision: enforce tenancy twice: establish a non-superadmin `TenantContext` from the verified capability token and add explicit tenant predicates in each tenant-owned source adapter.
  Rationale: defense in depth prevents a missing context or a future compiler path from silently becoming cross-tenant. Tests must make either missing control fail closed.
  Date/Author: 2026-08-25 / Codex.

- Decision: joins use catalog relationship names, not arbitrary left/right expressions.
  Rationale: this prevents joining through sensitive dimension tables and bounds fan-out. Each relationship declares keys, direction, cardinality, tenant behavior, and allowed fields.
  Date/Author: 2026-08-25 / Codex.

- Decision: keep fixed write capabilities and the persisted approval protocol from the previous design.
  Rationale: generic query composition is appropriate for reads; mutations require operation-specific validation, impact preview, authorization, and idempotency.
  Date/Author: 2026-08-25 / Codex.

- Decision: credentials, platform administration, delete, arbitrary network, SQL, shell, filesystem, and Kubernetes operations remain unavailable.
  Rationale: they are not data questions and their privilege exceeds the product need.
  Date/Author: 2026-08-25 / Codex.

## Outcomes & Retrospective

The governed PostgreSQL slice is implemented locally: developers register safe data semantics once, while OmniAgent composes new read questions at run time through `data/search`, `data/describe`, and `data/query`. The earlier eighteen tools remain behavioral fixtures rather than public interfaces. Nine entities, bounded AST compilation, explicit tenant predicates, token scopes, redaction, cursor and resource limits, the native Axi client, reviewed skills, and persisted fixed-action approval now exist. External sources without trustworthy tenant attribution, live open-ended model evaluations, and staged cohort release remain incomplete. No tool or tenant is enabled by default.

## Context and Orientation

The repository is `D:/program/agent_eval`. Browser chat enters `src/agent_eval/api/routers/omniagent.py`; `src/agent_eval/services/omniagent_chat.py` proxies OmniAgent events and persists tool records. The execution plan is `execplan/omniagent-axi-k8s-execution-layer.md`: Axi runs approved native tools inside one Kubernetes sandbox per chat and receives a short-lived tenant-scoped token only for `axi_run`.

A logical entity is a safe model-facing view such as `evaluation_runs`, not necessarily one database table. A field is an allowlisted scalar or computed value. A relationship is a predeclared traversal between two entities. An aggregate summarizes rows with `count`, `count_distinct`, `sum`, `avg`, `min`, or `max` when the field permits that operation. A source adapter compiles one logical source to SQLAlchemy or to a fixed external SDK call. A scope is a token claim such as `data:query`.

The first PostgreSQL-backed catalog should cover logical entities needed by current high-value questions: `datasets`, `candidate_cases`, `benchmark_cases`, `evaluation_runs`, `evaluation_results`, `evaluation_scores`, `reply_jobs`, `reply_versions`, and `reply_case_states`. Add entities incrementally only after projection, tenancy, indexes, and semantics are reviewed. Observability entities remain disabled until telemetry rows have trustworthy tenant attribution.

The target read path is:

    OmniAgent -> axi run data/query -> sandbox native client
      -> fixed Agent Eval internal endpoint -> AST validation
      -> tenant-scoped catalog/source adapter -> read-only query
      -> projection/redaction/limits -> structured rows

The native client knows one deployment-owned internal base URL. It cannot choose a path beyond the three fixed data endpoints. Database and external-provider credentials remain only in Agent Eval.

## Data Catalog and Query Contract

Create `config/omniagent-data-catalog.yaml` and validate it at startup with models under `src/agent_eval/omniagent_data/`. The catalog is code-reviewed deployment configuration, not model-authored data. Each entity declares a stable name, description, source adapter, enabled roles, required scope, default time field, maximum lookback, safe fields, relationships, and limits.

Each field declares its type, description, sensitivity class, selectable flag, filter operators, sortable flag, groupable flag, allowed aggregates, maximum returned length, and whether it is computed. Do not register raw `agent_config`, provider configuration, headers, URLs, credentials, unbounded messages, `full_trace`, hidden reasoning, OAuth rows, tenant/user administration, entry codes, secrets, or encrypted blobs.

Relationships declare a stable name such as `evaluation_runs.results`, target entity, source and target keys held only by the server, cardinality, join type, tenant rule, and fan-out limit. The AST names only the relationship. Cross-source joins are initially prohibited; a reviewed computed entity or bounded service method may supply a cross-source view later.

The three Axi tools are:

1. `data/search` accepts `query`, optional `source`, optional `tags`, and `limit` from 1 to 20. It returns matching entity names, descriptions, useful question examples, available relationships, and whether the entity is currently enabled. It never returns physical table names or credentials.

2. `data/describe` accepts one to five entity names and optional `include_relationships`. It returns safe field names, types, descriptions, allowed operators and aggregates, relationship names and cardinality, required scope, default time bounds, and query examples. It omits physical columns and server expressions.

3. `data/query` accepts one `QueryRequest`. It returns `schema_version`, `request_id`, `query_id`, `as_of`, typed `columns`, bounded `rows`, optional `page.next_cursor`, `warnings`, and redaction metadata. It does not return generated SQL.

`QueryRequest` has this shape:

    {
      "from": "evaluation_results",
      "select": [
        {"field": "error_class", "as": "error_class"},
        {"aggregate": "count", "as": "failures"},
        {"aggregate": "avg", "field": "total_tokens", "as": "avg_tokens"}
      ],
      "where": {
        "and": [
          {"field": "execution_status", "op": "eq", "value": "failed"},
          {"field": "created_at", "op": "gte", "value": "2026-08-01T00:00:00Z"}
        ]
      },
      "relationships": [],
      "group_by": ["error_class"],
      "order_by": [{"alias": "failures", "direction": "desc"}],
      "limit": 20,
      "cursor": null
    }

The boolean filter tree supports bounded `and`, `or`, and `not`. Leaf operators are catalog-controlled from `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `in`, `contains`, `starts_with`, `is_null`, and `between`. Values are Pydantic-validated against field types. No field-to-field comparison, expression language, regex, subquery, function name, SQL fragment, or user-selected cast is accepted in the first release.

A selection is either an allowlisted field or an aggregate. Aggregates may reference only fields that declare them. `count` may omit a field. `group_by` references only groupable fields. `order_by` references a selected field or aggregate alias. Relationships are traversed only through catalog names and must be declared before target fields are selected.

## Security and Resource Limits

The internal endpoint verifies the short-lived execution token and derives tenant, user, role, session, message, and scopes from claims. It rejects tenant or identity values in the request. It enters `TenantContext(tenant_id, superadmin=False)` even when the browser user is a platform superadmin; a separate audited cross-tenant capability would be required later.

The SQLAlchemy adapter adds `tenant_id == principal.tenant_id` explicitly to every tenant-owned root and joined entity. It must never use `text()` with model-provided content. It starts a read-only transaction and applies PostgreSQL `statement_timeout`, `lock_timeout`, and idle-transaction timeout. The production database role should be read-only for this endpoint when deployment topology permits, but that role is defense in depth, not a substitute for catalog authorization.

Initial hard limits are: one root entity, at most three relationships, depth two, twenty selected outputs, twenty filter leaves, two grouped fields, five aggregates, one hundred returned rows, five seconds database time, 64 KiB serialized response, and eight `data/query` calls per turn. Catalog entries may lower these limits. Queries exceeding a limit fail before execution with `QUERY_LIMIT`; timeout returns `QUERY_TIMEOUT`; disabled or unattributed sources return `SOURCE_UNAVAILABLE`.

Lists use opaque signed cursors containing catalog version, query fingerprint, sort key, tenant, and expiry. Offset pagination is not exposed. Every query returns `as_of` because independently executed reads are not a multi-call snapshot.

Projection and redaction run before serialization. Keys matching token, secret, password, authorization, cookie, API key, private key, connection string, and configured patterns become `[REDACTED]`. String, collection, nesting, and total-byte limits apply after projection and redaction. Stored prompt or trace content is untrusted data; results instruct the model to treat it as evidence, never as instructions.

Audit records contain principal IDs, entity names, canonical AST hash, selected fields, relationship names, row count, duration, limit decisions, and status. They do not contain the capability token, generated SQL, secret values, or full returned rows. Persisted chat tool records use the projected AST and bounded result only.

Success responses use one common envelope. Stable errors include `INVALID_ARGUMENT`, `UNAUTHENTICATED`, `FORBIDDEN`, `SCOPE_DENIED`, `ENTITY_NOT_FOUND`, `FIELD_DENIED`, `RELATIONSHIP_DENIED`, `QUERY_LIMIT`, `QUERY_TIMEOUT`, `SOURCE_UNAVAILABLE`, `OUTPUT_LIMIT`, and `INTERNAL_ERROR`.

## Skills and Open-Ended Evaluations

Create `deploy/omniagent/skills/data-investigation/SKILL.md`. It teaches the model to search before guessing entity names, describe only relevant entities, formulate the smallest query that can answer the question, inspect aggregate evidence before row details, and stop when evidence is sufficient. It must not contain credentials, URLs, SQL, shell, or authority claims.

Domain skills may still encode interpretation, not access. Keep or add `evaluation-diagnosis`, `dataset-readiness`, `production-incident-triage`, and `reply-readiness` to explain domain semantics such as the three evaluation statuses or reply-state distinctions. They call the same three data tools rather than task-specific endpoints.

Add offline evaluations under `tests/omniagent_data/evals/`. The former eighteen-tool workflows become fixtures demonstrating that generic composition covers known tasks. Add genuinely unplanned combinations, including:

- Which error class increased most between two equal time windows, and what was its average token usage?
- Which completed runs have high scores but no acceptance decision, grouped by evaluation mode?
- Do cases without a current reply account for a disproportionate share of persisted-run failures?
- Which datasets contain the most cases with criteria but no expected answer?

Evaluations assert search/describe/query behavior and final semantics, not one predetermined AST. They also include ambiguous entity names, invalid relationships, prohibited fields, high-cost joins, prompt-injection strings stored in rows, absent acceptance, Judge errors, empty data, expired cursors, and attempts to request another tenant.

## Later Write Capabilities

No write capability is part of the general data tools. Before C6, add `omniagent_capability_actions`. A row stores UUID, tenant/user/session/message, capability, canonical arguments and SHA-256 digest, risk, impact preview, role, cost estimate, state, expiry, idempotency key, approval/execution identity and times, terminal summary, and audit correlation.

State is `prepared -> approved -> executing -> succeeded|failed|cancelled|expired|denied`. Use compare-and-swap transitions in transactions. Approval uses an authenticated Agent Eval UI endpoint, not free-form model text, and binds the exact digest. Execution consumes a single-use `action:execute:<action_id>` scope and accepts only `action_id`; canonical business arguments stay server-side.

First write candidates remain evaluation start/stop, reply generation/cancellation, candidate review/promotion, and dataset archive/activate. Each has a fixed prepare and execute service enforcing domain rules and server-side idempotency. Never add provider/key/config/tenant/user/role/entry-code/login-token/arbitrary HTTP/SQL/files/shell/Kubernetes/Secret/delete tools.

## Plan of Work

### C0: contracts and catalog

Create `src/agent_eval/omniagent_data/` with catalog models, AST models, policy, authorization, redaction, cursor, limits, errors, and audit contracts. Add the first logical entities to `config/omniagent-data-catalog.yaml`. Startup fails on duplicate names, unsupported operators, unsafe relationship targets, missing tenant rules, or computed fields without registered resolvers. Unit tests snapshot the public catalog and prove prohibited storage fields are absent.

### C1: discovery

Implement `search_catalog()` and `describe_entities()` plus fixed internal endpoints under `/internal/omniagent-data/v1/search` and `/describe`, with `include_in_schema=False`. Build the Axi native tools using a client whose endpoint paths are constants. Prove discovery returns logical names and semantics without physical schema, disabled entities, or fields outside the caller's role and scopes.

### C2: PostgreSQL query compiler

Implement validation as separate resolve, authorize, cost, compile, execute, and project stages. Compile only registered mapped attributes and server-held relationship definitions. Establish tenant context and add explicit tenant predicates. Apply read-only transaction and timeouts. Prove simple filters, aggregates, approved joins, signed cursor pagination, malformed AST rejection, and fail-closed behavior when tenant context or entity tenant metadata is absent.

### C3: semantic and external adapters

Add reviewed computed fields for evaluation and reply semantics. Add an adapter interface for non-PostgreSQL sources, but enable a source only when credentials remain server-side, tenant attribution is proven, filters and output are bounded, and no arbitrary URL/path is accepted. Keep observability and conversation-dataset entities disabled until those conditions hold. Cross-source joins remain unavailable.

### C4: skills and query evaluations

Add the data-investigation skill, adapt four domain skills, and create known plus novel question fixtures. Measure whether the agent searches, describes, executes minimal queries, handles empty evidence, and avoids prohibited fields. Default to eight query calls per turn and add an explicit answer-with-uncertainty path when the catalog cannot support a question.

### C5: authorization and end-to-end reads

Mint `data:discover` and `data:query` scopes in turn tokens for allowed internal roles; verify again in Agent Eval. Test two tenants, platform-superadmin downgrade to tenant scope, expiry, redaction, prompt injection, cursor tampering, query amplification, timeout, and output size. Search PostgreSQL chat JSON, app/OmniAgent logs, Langfuse, and sandbox files for test tokens and secret canaries.

### C6: prepared actions

Add action persistence, approval UI events, single-use scopes, idempotency, and audit. Implement only evaluation start/stop first. Require timeout, duplicate delivery, expiry, denial, reconnect, and sandbox recreation tests before adding more writes.

### C7: gradual release

Release general reads to an internal cohort. Measure answer completion, unsupported-question rate, search/describe/query calls, AST validation failures, denied fields, timeout, rows scanned where available, output limits, latency, and unnecessary queries. Expand catalog entities independently; a bad entity can be disabled without disabling the whole gateway.

## Concrete Steps

From `D:/program/agent_eval` run:

    python -m pytest tests/omniagent_data -q
    python -m pytest tests/test_api/test_omniagent_chat.py -q
    python -m compileall -q src/agent_eval packages/agent-eval-axi-tools
    python -m ruff check src/agent_eval/omniagent_data tests/omniagent_data
    npm --prefix frontend run build

From `D:/program/OmniAgent` after the Axi adapter exists, run:

    uv run pytest tests/test_tools_axi.py tests/test_skill.py
    uv run ruff check omniagent/tools/axi.py

In the Python 3.12 sandbox image, exercise discovery and a composed query:

    axi search "query platform data without a dedicated API"
    axi describe data/query
    axi run data/query --json '{"from":"evaluation_results","select":[{"field":"error_class"},{"aggregate":"count","as":"failures"}],"where":{"field":"execution_status","op":"eq","value":"failed"},"group_by":["error_class"],"order_by":[{"alias":"failures","direction":"desc"}],"limit":10}'

Search must find the data tools, describe must show the AST contract, and run must return a bounded success envelope. An expired token, prohibited field, unknown relationship, raw SQL-shaped argument, or excessive query must produce a stable error without contacting the database when validation can reject it first.

## Validation and Acceptance

The read capability is accepted when:

1. A question with no dedicated Agent Eval API is answered through `data/search`, `data/describe`, and one or more `data/query` calls over registered entities.
2. The four known workflows for dataset readiness, evaluation diagnosis, production triage, and reply readiness remain answerable without their former task-specific read tools.
3. `data/query` rejects raw SQL, arbitrary expressions, physical table/column names, unregistered fields, relationships, operators, aggregates, and source destinations.
4. Different tenants cannot enumerate or query each other's entities or rows. A platform superadmin turn remains bound to its current tenant unless a separate future cross-tenant capability is explicitly authorized.
5. Removing the ORM tenant listener in a focused test does not expose another tenant because the adapter's explicit predicate still applies; removing adapter tenant metadata makes the query fail closed.
6. Lists cap at 100, joins at three, traversal depth at two, and response at 64 KiB; expensive queries fail with `QUERY_LIMIT` or `QUERY_TIMEOUT`.
7. Browser JWT, expired token, wrong scope/session/message, and external-customer role are rejected.
8. SSE, persistence, audit, logs, and traces contain no execution token, database credential, generated SQL, secret field, provider configuration, or hidden chain-of-thought.
9. Unattributed external data is unavailable rather than presented as tenant data.
10. Fixed writes remain undiscoverable until their separate policy, approval, and idempotency requirements are implemented.
11. Existing chat tests, targeted backend tests, Python compilation and lint, and the frontend production build pass.
12. Restoring OmniAgent's no-tool allowlist immediately removes data access while ordinary chat remains healthy.

## Idempotence and Recovery

Search, describe, and bounded reads are retryable, but every response includes `as_of`. Expired or tampered cursors return `INVALID_ARGUMENT`; the agent may restart discovery or pagination. The canonical AST hash identifies duplicate calls for audit but does not imply a snapshot across calls.

Catalog and skills are declarative. Disable an entity, field, relationship, or source by policy without rebuilding the sandbox image. Absence from either the server catalog or Axi image means unavailable. Catalog versions invalidate old cursors.

If a compiler or adapter rollout fails, disable `data/query` first while leaving normal chat intact. Then disable affected entities or restore `tools: ["__no_tools_until_designed__"]`. The sandbox contains no database state or credential requiring cleanup.

Prepared actions are immutable. Re-prepare creates a new action; approve and execute use compare-and-swap. Idempotency outlives short tokens. Crash recovery reads action state and stored result instead of resubmitting arguments.

## Artifacts and Notes

Expected discovery and execution are:

    axi run data/search --json '{"query":"evaluation failures and token usage"}'
    -> evaluation_runs, evaluation_results, evaluation_scores

    axi run data/describe --json '{"entities":["evaluation_results"]}'
    -> safe fields, operators, aggregates, relationships, and limits

    axi run data/query --json '{...bounded AST...}'
    -> {"status":"success","data":{"columns":[...],"rows":[...],"as_of":"..."}}

There is intentionally no `list_high_token_failures` endpoint and no database connection in the sandbox. The novelty is in the AST composed by OmniAgent; authority remains in the catalog and compiler.

A resumed write call remains fixed and accepts only an action ID:

    axi run evaluations/execute_start --json '{"action_id":"..."}'

## Interfaces and Dependencies

In `src/agent_eval/omniagent_data/models.py`, define frozen Pydantic models for `EntityDefinition`, `FieldDefinition`, `RelationshipDefinition`, `QueryRequest`, selections, filters, relationships, grouping, ordering, query responses, and errors. Use discriminated unions for field selections versus aggregate selections and for boolean nodes versus filter leaves.

In `src/agent_eval/omniagent_data/catalog.py`, expose:

    def search_catalog(principal: DataPrincipal, request: SearchRequest) -> SearchResponse: ...
    def describe_entities(principal: DataPrincipal, request: DescribeRequest) -> DescribeResponse: ...
    def resolve_query(principal: DataPrincipal, request: QueryRequest) -> ResolvedQuery: ...

In `src/agent_eval/omniagent_data/compiler.py`, expose:

    async def execute_query(principal: DataPrincipal, resolved: ResolvedQuery) -> QueryResponse: ...

Define a source adapter protocol whose implementation receives only a validated `ResolvedQuery`; the model-facing request never chooses an adapter, URL, credentials, or physical schema. The PostgreSQL adapter uses SQLAlchemy mapped attributes and server-held join expressions. External adapters implement the same bounded response contract.

The native package declares one data entry point:

    [project.entry-points."axi.native_tools"]
    data = "agent_eval_axi_tools.data"

It provides `data/search`, `data/describe`, and `data/query`. `AgentEvalDataClient` uses three constant endpoint paths, reads deployment-owned `AGENT_EVAL_INTERNAL_URL` and command-only `AGENT_EVAL_EXECUTION_TOKEN`, and logs no secret headers or full bodies.

Use existing SQLAlchemy/PostgreSQL, Pydantic 2, PyJWT execution validation, and `httpx` only in the native sandbox package. Add no database driver or generic network client to the sandbox beyond what the fixed native package needs.

Revision note (2026-08-24 17:48+08:00): created the initial capability design with eighteen read-only task-specific tools, four skills, six risk classes, and persisted approval/idempotency before writes.

Revision note (2026-08-25 10:00+08:00): replaced the fixed read-tool catalog with `data/search`, `data/describe`, and AST-based `data/query` so OmniAgent can answer new questions without a dedicated endpoint. The revision explicitly keeps database credentials out of the sandbox, preserves tenant enforcement in Agent Eval, moves the former eighteen tools into acceptance coverage, and retains fixed approved operations for all writes.

Revision note (2026-08-26 12:17+08:00): reconciled C0-C7 with the current implementation. C0-C2, C5, and C6 are locally complete; C3, live C4 evaluation, and staged C7 rollout remain open.
