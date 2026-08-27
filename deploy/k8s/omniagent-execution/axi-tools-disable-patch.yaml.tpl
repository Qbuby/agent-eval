apiVersion: apps/v1
kind: Deployment
metadata:
  name: "${DEPLOYMENT_NAME}"
  namespace: "${NAMESPACE}"
spec:
  template:
    metadata:
      annotations:
        agent-eval.aidong.ai/omniagent-axi-tools: disabled
    spec:
      containers:
        - name: omniagent
          env:
            - name: OMNIAGENT_AXI_TOOLS_ENABLED
              $patch: delete
            - name: OMNIAGENT_TOOL_AXI_SEARCH
              $patch: delete
            - name: OMNIAGENT_TOOL_AXI_DESCRIBE
              $patch: delete
            - name: OMNIAGENT_TOOL_AXI_RUN
              $patch: delete
            - name: OMNIAGENT_TOOL_SKILL
              $patch: delete
            - name: AGENT_EVAL_INTERNAL_URL
              $patch: delete
            - name: OMNIAGENT_AXI_SANDBOX_TEMPLATE
              $patch: delete
            - name: OMNIAGENT_SANDBOX_NAMESPACE
              $patch: delete
