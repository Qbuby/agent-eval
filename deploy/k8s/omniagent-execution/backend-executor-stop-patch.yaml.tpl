apiVersion: apps/v1
kind: Deployment
metadata:
  name: "${DEPLOYMENT_NAME}"
  namespace: "${NAMESPACE}"
spec:
  template:
    metadata:
      annotations:
        agent-eval.aidong.ai/omniagent-execution: stopping
    spec:
      containers:
        - name: backend
          env:
            - name: OMNIAGENT_EXECUTION_ENABLED
              value: "false"
            - name: OMNIAGENT_WORKER_ENABLED
              value: "false"
