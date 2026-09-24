# API gateway for one organisation. __ORG__ and __IMAGE__ are substituted by
# deploy/scripts/up.sh. Each gateway mounts only the public connection profiles
# and its own wallet (wallets/org__ORG__), never the shared crypto material, and
# GATEWAY_MSP makes it refuse requests for any other organisation.
apiVersion: v1
kind: ConfigMap
metadata:
  name: agent-gateway-config
  namespace: cgc
data:
  config.json: |
    {
      "request-timeout": 30000,
      "tcert-batch-size": 10,
      "crypto-hash-algo": "SHA2",
      "crypto-keysize": 256,
      "crypto-hsm": false,
      "connection-timeout": 30000
    }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gateway-org__ORG__
  namespace: cgc
spec:
  replicas: 1
  selector:
    matchLabels: { app: gateway-org__ORG__ }
  template:
    metadata:
      labels: { app: gateway-org__ORG__ }
      # Changes only when the image is rebuilt, so apply rolls pods exactly once.
      annotations: { image-id: "__IMAGE_ID__" }
    spec:
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: cgc-data
        - name: config
          configMap:
            name: agent-gateway-config
      containers:
        - name: api
          image: __IMAGE__
          imagePullPolicy: IfNotPresent
          env:
            - { name: GATEWAY_MSP, value: "Org__ORG__MSP" }
          ports:
            - containerPort: 4000
          readinessProbe:        # ready only once the API accepts connections
            tcpSocket: { port: 4000 }
            periodSeconds: 2
          resources:
            requests: { memory: 128Mi, cpu: 50m }
            limits:   { memory: 512Mi, cpu: 500m }
          volumeMounts:
            - { name: data,   mountPath: /usr/src/app/connection-profile, subPath: connection-profile, readOnly: true }
            - { name: data,   mountPath: /usr/src/app/wallet,             subPath: wallets/org__ORG__ }
            - { name: config, mountPath: /usr/src/app/config.json,        subPath: config.json }
---
apiVersion: v1
kind: Service
metadata:
  name: gateway-org__ORG__
  namespace: cgc
spec:
  selector: { app: gateway-org__ORG__ }
  ports:
    - { port: 4000, targetPort: 4000 }
