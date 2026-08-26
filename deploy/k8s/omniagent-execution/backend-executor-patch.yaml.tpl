apiVersion: apps/v1
kind: Deployment
metadata:
  name: "${DEPLOYMENT_NAME}"
  namespace: "${NAMESPACE}"
spec:
  template:
    metadata:
      annotations:
        agent-eval.aidong.ai/omniagent-execution: enabled
        agent-eval.aidong.ai/omniagent-previous-service-account: "${ORIGINAL_SERVICE_ACCOUNT}"
        agent-eval.aidong.ai/omniagent-restored-service-account: null
    spec:
      serviceAccountName: omniagent-executor
      containers:
        - name: backend
          env:
            - name: OMNIAGENT_EXECUTION_ENABLED
              value: "true"
            - name: OMNIAGENT_EXECUTION_TENANT_ALLOWLIST
              value: "${EXECUTION_TENANT_ALLOWLIST}"
            - name: OMNIAGENT_EXECUTION_SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: "${EXECUTION_SECRET_NAME}"
                  key: OMNIAGENT_EXECUTION_SECRET_KEY
            - name: OMNIAGENT_PRODUCT_PLANE_ENABLED
              value: "true"
            - name: OMNIAGENT_WORKER_ENABLED
              value: "true"
            - name: OMNIAGENT_RUNNER
              value: kubernetes
            - name: OMNIAGENT_KUBERNETES_RUNNER_CONFIRMED
              value: "true"
            - name: OMNIAGENT_KUBERNETES_NAMESPACE
              value: omniagent-sandbox-staging
            - name: OMNIAGENT_KUBERNETES_TEMPLATE
              value: omniagent-execution-v1
            - name: OMNIAGENT_KUBERNETES_READY_TIMEOUT_SECONDS
              value: "180"
            - name: OMNIAGENT_KUBERNETES_CLAIM_TTL_SECONDS
              value: "900"
            - name: OMNIAGENT_ARTIFACT_SCANNER
              value: clamav
            - name: OMNIAGENT_CLAMAV_COMMAND
              value: clamscan
