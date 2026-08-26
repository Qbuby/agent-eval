apiVersion: apps/v1
kind: Deployment
metadata:
  name: "${DEPLOYMENT_NAME}"
  namespace: "${NAMESPACE}"
spec:
  template:
    metadata:
      annotations:
        agent-eval.aidong.ai/omniagent-execution: disabled
        agent-eval.aidong.ai/omniagent-previous-service-account: null
        agent-eval.aidong.ai/omniagent-restored-service-account: "${ORIGINAL_SERVICE_ACCOUNT}"
    spec:
      serviceAccountName: "${ORIGINAL_SERVICE_ACCOUNT}"
      containers:
        - name: backend
          env:
            - name: OMNIAGENT_EXECUTION_ENABLED
              $patch: delete
            - name: OMNIAGENT_EXECUTION_TENANT_ALLOWLIST
              $patch: delete
            - name: OMNIAGENT_EXECUTION_SECRET_KEY
              $patch: delete
            - name: OMNIAGENT_PRODUCT_PLANE_ENABLED
              value: "true"
            - name: OMNIAGENT_WORKER_ENABLED
              $patch: delete
            - name: OMNIAGENT_RUNNER
              $patch: delete
            - name: OMNIAGENT_KUBERNETES_RUNNER_CONFIRMED
              $patch: delete
            - name: OMNIAGENT_KUBERNETES_NAMESPACE
              $patch: delete
            - name: OMNIAGENT_KUBERNETES_TEMPLATE
              $patch: delete
            - name: OMNIAGENT_KUBERNETES_READY_TIMEOUT_SECONDS
              $patch: delete
            - name: OMNIAGENT_KUBERNETES_CLAIM_TTL_SECONDS
              $patch: delete
            - name: OMNIAGENT_ARTIFACT_SCANNER
              $patch: delete
            - name: OMNIAGENT_CLAMAV_COMMAND
              $patch: delete
