# OmniAgent execution staging resources

These manifests are review artifacts, not production-ready defaults. They use the reviewed
`extensions.agents.x-k8s.io/v1alpha1` API, an intentionally invalid analysis-runtime image, and
a warm pool of zero. The analysis runtime is independent of Axi and can be built locally; Axi
tools remain behind their separate license gate. Keep Agent Eval execution flags disabled until
an immutable analysis image and a staging cluster have passed acceptance.

Before enabling the resources:

The optional Axi runtime has a separate two-part supply-chain gate. Place the exact PyPI
`axi_cli-0.0.11-py3-none-any.whl` under `deploy/omniagent/execution-runtime/vendor/` without
committing it. `verify_axi_wheel.py` requires the published PyPI SHA-256
`ab9473092cac37e4f00347cd92d9e629424bcba99731c8d082b8031a29d6fbdf` and validates the
wheel metadata, entry point, archive paths, and every RECORD hash. This proves artifact identity,
not permission to use it. The wheel's 21 Python modules also match Git tag `v0.0.11` commit
`290b20e9d584d5d61cdf7bae47a83e142db569da` exactly. That commit is unsigned and has no
`LICENSE`, `COPYING`, package license field, or GitHub Release. An approved build must separately pass
`--build-arg CONFIRM_AXI_LICENSE_REVIEWED=axi-license-reviewed`; do not provide that confirmation
until legal or upstream supplies an unambiguous written license basis.

1. Build the analysis image from the repository root with
   `docker build -f deploy/omniagent/analysis-runtime/Dockerfile -t agent-eval-analysis-runtime .`.
   Push it to the reviewed registry and replace the invalid image with its immutable digest.
   This image contains no Axi package; do not use the license-gated `execution-runtime` image for
   analysis jobs.
2. Build the backend with `--build-arg INSTALL_KUBERNETES_RUNNER=1`. The default backend image
   intentionally omits the Kubernetes SDK and therefore cannot create SandboxClaims.
3. Label the backend Pods `app=backend`, or update both the Service selector and template egress
   selector together.
4. Explicitly patch the backend Pod to use the `agent-eval/omniagent-executor` ServiceAccount.
   Basic chat sidecar deployment does not grant execution permissions. The execution apply script
   renders and applies this strategic-merge patch separately from the ordinary Kubernetes
   resources. In the current sidecar topology the identity is Pod-scoped, so both containers
   share the narrow claim permissions.
5. Confirm the sandbox-router uses `app=sandbox-router` in `omniagent-sandbox-staging`.
6. Validate CRDs first, then use the execution apply script's server dry-run. Its default mode is
   offline rendering and does not read kubeconfig. Real apply requires a second confirmation.

Use the repository acceptance tool after the resources and reviewed image digest exist in
staging. Its default mode is read-only: it checks the current context, required CRDs,
server-side dry-run, least-privilege RBAC, the exact SandboxTemplate image and network policy,
the zero-size WarmPool, and the internal Service without creating a SandboxClaim.

    python deploy/k8s/omniagent-execution/staging_smoke.py \
      --tenant-id '<tenant-uuid>' \
      --analysis-image 'registry.example/analysis-runtime@sha256:<64-hex>'

The live mode is intentionally mutating and requires an exact second confirmation. It creates two
short-lived claims as the executor ServiceAccount, proves both become ready, verifies the actual
Pod image and security context, checks backend `/health`, denies public Internet and direct
sandbox-to-sandbox traffic, shortens one claim's lifecycle and observes controller deletion, then
strictly deletes and verifies every remaining claim. Any cleanup failure makes acceptance fail.

    python deploy/k8s/omniagent-execution/staging_smoke.py \
      --tenant-id '<tenant-uuid>' \
      --analysis-image 'registry.example/analysis-runtime@sha256:<64-hex>' \
      --live \
      --confirm-live create-omniagent-staging-smoke-claims

Both modes exit non-zero on the first unmet gate. The current local machine has no Kubernetes
current-context, so these commands have not yet produced staging acceptance evidence here.

After staging acceptance, the only supported apply path is explicit and double-confirmed:

    EXECUTION_TENANT_ALLOWLIST='<tenant-uuid>' \
      ANALYSIS_RUNTIME_IMAGE='registry.example/analysis-runtime@sha256:<64-hex>' \
      MODE=apply CONFIRM_EXECUTION_APPLY=apply-omniagent-execution \
      sh deploy/k8s/omniagent-execution/apply.sh

The script accepts only immutable lowercase sha256 image digests. Kubernetes names must be
lowercase DNS names, and image references reject whitespace, quotes, extra digest markers, and
other characters that could alter rendered YAML. Validation happens before any connected
operation or manifest rendering. The script applies Namespace/RBAC,
Service, SandboxTemplate, and WarmPool as resources, then patches only the named backend
Deployment's Pod identity. Before any connected enable operation it verifies the target labels,
backend container, execution Secret key, and current ServiceAccount state. The original
ServiceAccount is recorded on the Pod template so rollback does not need an operator to remember
it.

Use the supported two-phase rollback rather than deleting the shared Pod:

    MODE=server-dry-run DISABLE_EXECUTION=1 \
      sh deploy/k8s/omniagent-execution/apply.sh
    MODE=apply DISABLE_EXECUTION=1 \
      CONFIRM_EXECUTION_APPLY=apply-omniagent-execution \
      sh deploy/k8s/omniagent-execution/apply.sh

The apply path first sets `OMNIAGENT_EXECUTION_ENABLED=false` and
`OMNIAGENT_WORKER_ENABLED=false`, then waits for that rollout. It next removes optional Axi tool
configuration, removes execution credentials and runner/scanner settings, restores the recorded
ServiceAccount, and waits for the cleanup rollout. Cleanup deliberately keeps
`OMNIAGENT_PRODUCT_PLANE_ENABLED=true`, so users can still inspect historical jobs, events, and
artifacts and download authorized outputs. If the Deployment already uses
`omniagent-executor` but the previous-ServiceAccount annotation is missing, the script fails
before any apply, patch, or rollout. Cleanup records the restored ServiceAccount in
`agent-eval.aidong.ai/omniagent-restored-service-account`. If the caller loses the final rollout
response, rerunning the same rollback is safe only when the execution annotation is `disabled`,
the previous-ServiceAccount annotation is absent, and the restored-ServiceAccount annotation
exactly matches the current identity. In that proven completed state the script issues no patch
and only resumes `kubectl rollout status`; any mismatch fails before mutation. A later enable
removes the restored-ServiceAccount marker. Neither the configured original ServiceAccount nor
either identity marker may be `omniagent-executor`; rollback can never preserve execution
privileges by treating the executor identity as the restoration target. Enable and rollback also
reject lifecycle annotations that do not match the current Pod identity.
