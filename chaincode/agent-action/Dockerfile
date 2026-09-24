# AgentAction chaincode — chaincode-as-a-service microservice.
FROM golang:1.19-alpine AS build

COPY ./ /go/src/github.com/cgc/agentaction
WORKDIR /go/src/github.com/cgc/agentaction

# Build application
RUN go build -o chaincode -v .

# Production ready image
FROM alpine:3.11 as prod

COPY --from=build /go/src/github.com/cgc/agentaction/chaincode /app/chaincode

USER 1000

WORKDIR /app
CMD ./chaincode
