# IRI HAProxy Load-balancer

This is initial work to create a HAPRoxy load balancer for IRI which can be deployed in a highly available setup.

Using HAProxy's new server template syntax: https://www.haproxy.com/blog/dynamic-scaling-for-microservices-with-runtime-api/

The configuration backend is Consul. Consul's configuration enables 'watch events' which calls a handler. The handler is a Python script which adds/updates/removes backends from HAProxy.

## HAProxy

HAProxy is configured by default with 2 backends:

1. Default backend

2. PoW backend (has a lower maxconn per backend to avoid PoW DoS)

HAProxy has pre-assigned slots (can be any number, e.g. 1-2000 server slots per backend). The slots are being populated by the Python script when it detects new services registered in Consul. The Python script adds, removes or updates the HAProxy configuration.

### Commands

Example view stats from admin TCP socket:

```sh
echo "show stat" | socat stdio tcp4-connect:127.0.0.1:9999
```

## Consul

Consul exposes a simple API to register services and healthchecks. Each registered service includes a healthcheck (a simple script) which concludes whether a service is healthy or not. Based on the service's health the backend becomes active or disabled in HAProxy.

On each service registry type event in Consul the Python script is called. It ensures the state is of the services in Consul are reflected in HAProxy's configuration. It communicates with HAProxy via HAProxy's API socket.

### Commands

Example view all registered services on catalog (Consul cluster) level:
```sh
curl -s -H "X-Consul-Token: $CONSUL_HTTP_TOKEN" -X GET http://localhost:8500/v1/catalog/services | jq .
```

Example register a service (IRI node):
```sh
curl -H "X-Consul-Token: $CONSUL_HTTP_TOKEN" -X PUT -d@service.json http://localhost:8500/v1/agent/service/register
```

Example deregister a service (IRI node):
```sh
curl -H "X-Consul-Token: $CONSUL_HTTP_TOKEN" -X PUT http://localhost:8500/v1/agent/service/deregister/10.100.0.10:14265
```

View all health checks on this agent:
```sh
curl -s -H "X-Consul-Token: $CONSUL_HTTP_TOKEN" -X GET http://localhost:8500/v1/agent/checks | jq .
```

View all services on this agent:
```sh
curl -s -H "X-Consul-Token: $CONSUL_HTTP_TOKEN" -X GET http://localhost:8500/v1/agent/services | jq .
```

See Consul's API documentation for more information: https://www.consul.io/api/index.html
