# Chaincode-as-a-service server for one org. __ORG__, __IMAGE__ and
# __PACKAGE_ID__ are substituted by deploy/scripts/up.sh.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agentaction-org__ORG__
  namespace: cgc
spec:
  replicas: 1
  selector:
    matchLabels: { app: agentaction-org__ORG__ }
  strategy: { type: Recreate }
  template:
    metadata:
      labels: { app: agentaction-org__ORG__ }
      # Changes only when the image is rebuilt, so apply rolls pods exactly once.
      annotations: { image-id: "__IMAGE_ID__" }
    spec:
      containers:
        - name: chaincode
          image: __IMAGE__
          imagePullPolicy: IfNotPresent
          env:
            - { name: CHAINCODE_ID,             value: "__PACKAGE_ID__" }
            - { name: CHAINCODE_SERVER_ADDRESS, value: "0.0.0.0:7052" }
          ports:
            - containerPort: 7052
          resources:
            requests: { memory: 64Mi,  cpu: 50m }
            limits:   { memory: 256Mi, cpu: 500m }
---
apiVersion: v1
kind: Service
metadata:
  name: agentaction-org__ORG__
  namespace: cgc
spec:
  selector: { app: agentaction-org__ORG__ }
  ports:
    - { name: grpc, port: 7052, targetPort: 7052 }
