# IRI HAProxy Load-balancer

This is initial work to create a HAPRoxy load balancer for IRI which can be deployed in a highly available setup.

Using HAProxy's new server template syntax: https://www.haproxy.com/blog/dynamic-scaling-for-microservices-with-runtime-api/

The configuration backend is Consul. Consul's configuration enables 'watch events' which calls a handler. The handler is a Python script which adds/updates/removes backends from HAProxy.

See Consul's documentation for more information about it: https://www.consul.io/docs/index.html

## Requirements

In order to use this PoC you must have the following installed on your server:

* Docker
* Ansible (>=2.4)
* CentOS (>=7.4) or Ubuntu (>=16.04)

You can optionally use this Ansible playbook to get Docker intsalled on your server: https://github.com/geerlingguy/ansible-role-git

## Warning

This PoC playbook will install some packages on the system to allow it to operate and configure the services properly. **Do not use this installation on a production service!**

## Installation

Clone the repository:
```sh
git clone https://github.com/nuriel77/iri-lb-haproxy.git && cd iri-lb-haproxy
```

Variable files are located in `group_vars/all/*.yml`. If you want to edit any of those it is recommended to create variable override files, prefixed with `z-...`
. For example: `group_vars/all/z-override.yml` will be loaded last and override any previously configured variables.


Run the playbook:
```sh
ansible-playbook -i inventory -v site.yml
```

## Uninstall

Uninstall is best effort. It will remove all configured files, users, data directories, services, docker containers and images.

```sh
ansible-playbook -i inventory site.yml -v --tags=uninstall -e uninstall_playbook=yes
```

## Controlling Consul and Haproxy

The playbook should start Consul and Haproxy for you.

To control a service (stop/restart/reload/stop) use the following syntax, e.g:

```sh
systemctl stop consul
```

or

```sh
systemctl reload haproxy
```

To view logs use the following syntax:
```sh
journalctl -u haproxy -e
```
You can add the flag `-f` so the logs are followed live.


## Overview

Consul holds a registry of services. The services can be, in our example, IRI nodes. We register the IRI nodes in Consul using its API and add a health check.

We are able to add some meta-tags which are going to help control a configuration per IRI node, for example, whether to check if this node has PoW, whether we should authenticate to it via HTTPS, define timeout, max connections and so on.

Once a service is registered Consul begins to run periodic health checks (defined by our custom script). If the health check is successful or failed will determine if HAProxy keeps it in its pool as a valid node to proxy traffic to.

Consul uses a "watch" handler. This is a Python script that reads the services (and respective health check status) from Consul and configures HAProxy accordingly.

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

Export the Consul master token to a variable so it can be reused when using curl:
```sh
export CONSUL_HTTP_TOKEN=$(cat /etc/consul/consul_master_token)
```

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
