apiVersion: apps/v1
kind: Deployment
metadata:
  name: "${DEPLOYMENT_NAME}"
  namespace: "${NAMESPACE}"
spec:
  template:
    metadata:
      annotations:
        agent-eval.aidong.ai/omniagent-axi-tools: enabled
    spec:
      containers:
        - name: omniagent
          env:
            - name: OMNIAGENT_AXI_TOOLS_ENABLED
              value: "1"
            - name: OMNIAGENT_TOOL_AXI_SEARCH
              value: axi_search
            - name: OMNIAGENT_TOOL_AXI_DESCRIBE
              value: axi_describe
            - name: OMNIAGENT_TOOL_AXI_RUN
              value: axi_run
            - name: OMNIAGENT_TOOL_SKILL
              value: skill
            - name: AGENT_EVAL_INTERNAL_URL
              value: "http://agent-eval-omniagent-internal.${NAMESPACE}.svc:8000"
            - name: OMNIAGENT_AXI_SANDBOX_TEMPLATE
              value: agent-eval-axi-v1
            - name: OMNIAGENT_SANDBOX_NAMESPACE
              value: omniagent-sandbox-staging
