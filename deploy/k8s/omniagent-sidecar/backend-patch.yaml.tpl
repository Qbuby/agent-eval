apiVersion: apps/v1
kind: Deployment
metadata:
  name: "${DEPLOYMENT_NAME}"
  namespace: "${NAMESPACE}"
spec:
  template:
    metadata:
      annotations:
        agent-eval.aidong.ai/omniagent-config-sha256: "${CONFIG_HASH}"
        kubectl.kubernetes.io/default-container: backend
    spec:
      containers:
        - name: omniagent
          image: "${OMNIAGENT_IMAGE}"
          imagePullPolicy: IfNotPresent
          workingDir: /OmniAgent
          ports:
            - name: omniagent-http
              containerPort: 8090
              protocol: TCP
          env:
            - name: OMNIAGENT_MODEL
              value: "${OMNIAGENT_MODEL}"
            - name: LLM_PROVIDER
              value: openai
            - name: OPENAI_BASE_URL
              value: "${OMNIAGENT_BASE_URL}"
            - name: OPENAI_API_KEY
              valueFrom:
                secretKeyRef:
                  name: "${SECRET_NAME}"
                  key: OMNIAGENT_API_KEY
            - name: DB_HOST
              value: "${DB_HOST}"
            - name: DB_USER
              value: "${DB_USER}"
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: "${SECRET_NAME}"
                  key: DB_PASSWORD
            - name: LANGFUSE_PUBLIC_KEY
              value: ""
            - name: LANGFUSE_SECRET_KEY
              value: ""
          volumeMounts:
            - name: omniagent-config
              mountPath: /OmniAgent/.omniagent
              readOnly: true
          readinessProbe:
            httpGet:
              path: /openapi.json
              port: 8090
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 12
          livenessProbe:
            httpGet:
              path: /openapi.json
              port: 8090
            initialDelaySeconds: 30
            periodSeconds: 20
            timeoutSeconds: 5
            failureThreshold: 3
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
            limits:
              cpu: "2"
              memory: 2Gi
      volumes:
        - name: omniagent-config
          configMap:
            name: "${CONFIGMAP_NAME}"
            items:
              - key: config.yaml
                path: config.yaml
              - key: mcp_server.json
                path: mcp_server.json
              - key: SOUL.md
                path: prompts/SOUL.md
              - key: GUARDRAILS.md
                path: prompts/GUARDRAILS.md
              - key: AGENTS.md
                path: prompts/AGENTS.md
              - key: sitecustomize.py
                path: overlay/sitecustomize.py
              - key: overlay-init.py
                path: overlay/omniagent_overlay/__init__.py
              - key: axi_bridge.py
                path: overlay/omniagent_overlay/axi_bridge.py
              - key: axi_tools.py
                path: overlay/omniagent_overlay/axi_tools.py
              - key: skill-artifact-handling.md
                path: skills/artifact-handling/SKILL.md
              - key: skill-controlled-analysis.md
                path: skills/controlled-analysis/SKILL.md
              - key: skill-data-investigation.md
                path: skills/data-investigation/SKILL.md
              - key: skill-delegation.md
                path: skills/delegation/SKILL.md
              - key: skill-governed-actions.md
                path: skills/governed-actions/SKILL.md
              - key: skill-personal-memory.md
                path: skills/personal-memory/SKILL.md
              - key: skill-scheduled-automation.md
                path: skills/scheduled-automation/SKILL.md
