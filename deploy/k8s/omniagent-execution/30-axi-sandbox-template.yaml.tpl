# Rendered only when ENABLE_AXI_TOOLS=1 and the Axi license review gate is confirmed.
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxTemplate
metadata:
  name: agent-eval-axi-v1
  namespace: omniagent-sandbox-staging
  labels:
    app.kubernetes.io/name: agent-eval-axi
spec:
  service: true
  envVarsInjectionPolicy: Disallowed
  networkPolicyManagement: Managed
  podTemplate:
    metadata:
      labels:
        app.kubernetes.io/name: agent-eval-axi
    spec:
      automountServiceAccountToken: false
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: runtime
          image: "${AXI_RUNTIME_IMAGE}"
          imagePullPolicy: IfNotPresent
          ports:
            - name: runtime-http
              containerPort: 8888
              protocol: TCP
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          readinessProbe:
            httpGet:
              path: /readyz
              port: 8888
            initialDelaySeconds: 1
            periodSeconds: 2
            timeoutSeconds: 1
            failureThreshold: 30
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8888
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 2
            failureThreshold: 3
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
              ephemeral-storage: 512Mi
            limits:
              cpu: "2"
              memory: 2Gi
              ephemeral-storage: 2Gi
          volumeMounts:
            - name: workspace
              mountPath: /workspace
            - name: axi-home
              mountPath: /home/user/.axi
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: workspace
          emptyDir:
            sizeLimit: 1Gi
        - name: axi-home
          emptyDir:
            sizeLimit: 128Mi
        - name: tmp
          emptyDir:
            sizeLimit: 256Mi
  networkPolicy:
    ingress:
      - from:
          - namespaceSelector:
              matchLabels:
                kubernetes.io/metadata.name: omniagent-sandbox-staging
            podSelector:
              matchLabels:
                app: sandbox-router
          - namespaceSelector:
              matchLabels:
                kubernetes.io/metadata.name: "${NAMESPACE}"
            podSelector:
              matchLabels:
                app: backend
        ports:
          - protocol: TCP
            port: 8888
    egress:
      - to:
          - namespaceSelector:
              matchLabels:
                kubernetes.io/metadata.name: kube-system
            podSelector:
              matchLabels:
                k8s-app: kube-dns
        ports:
          - protocol: UDP
            port: 53
          - protocol: TCP
            port: 53
      - to:
          - namespaceSelector:
              matchLabels:
                kubernetes.io/metadata.name: "${NAMESPACE}"
            podSelector:
              matchLabels:
                app: backend
        ports:
          - protocol: TCP
            port: 8000
---
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxWarmPool
metadata:
  name: agent-eval-axi-v1
  namespace: omniagent-sandbox-staging
spec:
  replicas: 0
  updateStrategy:
    type: Recreate
  sandboxTemplateRef:
    name: agent-eval-axi-v1
