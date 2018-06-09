# IRI HAProxy Load-balancer

This is initial work to create a HAPRoxy load balancer for IRI which can be deployed in a highly available setup.

Using HAProxy's new server template syntax: https://www.haproxy.com/blog/dynamic-scaling-for-microservices-with-runtime-api/

The configuration backend is Consul. Consul's configuration enables 'watch events' which calls a handler. The handler is a Python script which adds/updates/removes backends from HAProxy.

See Consul's documentation for more information about it: https://www.consul.io/docs/index.html

## Requirements

In order to use this PoC you must have the following installed on your server:

* Ansible (>=2.4)
* CentOS (>=7.4) or Ubuntu (>=16.04)

If you don't have Docker installed, you can use the playbook to install it. See the "Installation" chapter below.

## Warning

This PoC playbook will install some packages on the system to allow it to operate and configure the services properly. **Do not use this installation on a production service!**

## Installation

Clone the repository:
```sh
git clone https://github.com/nuriel77/iri-lb-haproxy.git && cd iri-lb-haproxy
```

Variable files are located in `group_vars/all/*.yml`. If you want to edit any of those it is recommended to create variable override files, prefixed with `z-...`
. For example: `group_vars/all/z-override.yml` will be loaded last and override any previously configured variables.


The installation via the playbook is modular: you can choose what to install:

For a simple installation, run the playbook (will not install docker nor configure firewall):
```sh
ansible-playbook -i inventory -v site.yml
```

If you want the playbook to install docker for you, add the option `-e install_docker=true`:
```sh
ansible-playbook -i inventory -v site.yml -e install_docker=true
```

If you want the playbook to configure the firewall, add the option `-e configure_firewall=true`.



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

## Service JSON files

In the directory `roles/shared-files` you will find some JSON files which are service and health checks definitions which are used to register a new service (IRI node) to consul.

Here is an example with some explanation:
```
{
  "ID": "10.10.0.110:15265",    <--- This is the ID of the service. Using this ip:port combination we can later delete the service from Consul.
  "Name": "my.loadbalancer.io", <--- This is the name of the Consul service. In essence, this should also be the hostname of the loadbalancer(s).
  "tags": [
    "haproxy.maxconn=7",        <--- This tag will ensure that this IRI node is only allowed maximum 7 concurrent connections
    "haproxy.scheme=http",      <--- The authentication scheme to this IRI node is via http
    "haproxy.pow=false"         <--- This node will not allow PoW (attachToTangle disabled)
  ],
  "Address": "10.10.0.110",     <--- The IP address of the node
  "Port": 15265,                <--- The port of IRI on this node
  "EnableTagOverride": false,
  "Check": {
    "id": "10.10.0.110:15265",  <--- Just a check ID
    "name": "API 10.10.0.110:15265", <--- Just a name for the checks
    "args": ["/scripts/node_check.sh", "-a", "http://10.10.0.110:15265", "-i"], <--- This script and options will be run to check the service's health
    "Interval": "30s",          <--- The check will be run every 30 seconds
    "timeout": "5s",            <--- Timeout will occur if the check is not finished within 5 seconds
    "DeregisterCriticalServiceAfter": "1m" <--- If the health is critical, the service will be de-registered from Consul
  }
}
```

Here is an example of a service (IRI node) that supports PoW:
```
{
  "ID": "10.10.0.110:15265-pow", <--- *Note that we've appended `-pow` to the ID*
  "Name": "my.loadbalancer.io",  <--- We always use the same service name to make sure this gets configured in Haproxy
  "tags": [
    "haproxy.maxconn=7",         <--- Max concurrent connections to this node
    "haproxy.scheme=http",       <--- connection scheme (http is anyway the default)
    "haproxy.pow=true"           <--- PoW enabled node
  ],
  "Address": "10.10.0.110",
  "Port": 15265,
  "EnableTagOverride": false,
  "Check": {
    "id": "10.10.0.110:15265-pow",
    "name": "API 10.10.0.110:15265-pow",
    "args": ["/scripts/node_check.sh", "-a", "http://10.10.0.110:15265", "-i", "-p"], <--- Note the `-p` in the arguments which means we validate PoW works.
    "Interval": "30s",
    "timeout": "5s",
    "DeregisterCriticalServiceAfter": "1m"
  }
}
```

* The reason we append `-pow` to the ID of a PoW enabled node is because you can also register the same IP:PORT on the default backend to serve non-PoW requests. This keeps the two definitions separated.

A simple service's definition:
```
{
  "ID": "10.80.0.10:16265",
  "Name": "my.loadbalancer.io",
  "tags": [],
  "Address": "10.80.0.10",
  "Port": 16265,
  "EnableTagOverride": false,
  "Check": {
    "id": "10.80.0.10:16265",
    "name": "API http://node01.iota.io:16265",
    "args": ["/scripts/node_check.sh", "-a", "http://node01.iota.io:16265", "-i", "-m", "1.4.1.7"], <--- Note that we ensure the API version is minimum 1.4.1.7
    "Interval": "30s",
    "timeout": "5s",
    "DeregisterCriticalServiceAfter": "1m"
  }
}

```

HTTPS enabled service/node:
```
{
  "ID": "10.10.10.115:14265",
  "Name": "my.loadbalancer.io",
  "tags": [
    "haproxy.weight=10",    <--- sets weight for HAPRoxy
    "haproxy.scheme=https", <--- scheme is https
    "haproxy.sslverify=0"   <--- Do not SSl verify the certificate of this node
  ],
  "Address": "10.10.10.115",
  "Port": 14265,
  "EnableTagOverride": false,
  "Check": {
    "id": "10.10.10.115:14265",
    "name": "10.10.10.115:14265",
    "args": ["/usr/local/bin/node_check.sh", "-a", "https://10.10.10.115:14265", "-i", "-k"], <--- `-k` skips verifying SSL when running healthchecks.
    "Interval": "30s",
    "timeout": "5s",
    "DeregisterCriticalServiceAfter": "1m"
  }
}
```

*NOTE* that due to HAProxy DNS resolution it is problematic to register a node using only its domain name. Nodes running with dynamic DNS cannot be used.

Example using hostname, the ID must be the resolvable IP of the node's name `node05.iotanode.io`:
```
{
  "ID": "10.10.10.94:16265",
  "Name": "my.loadbalancer.io",
  "tags": [],
  "Address": "10.10.10.94",
  "Port": 16265,
  "EnableTagOverride": false,
  "Check": {
    "id": "10.10.10.94:16265",
    "name": "API http://node05.iotanode.io:16265",
    "args": ["/usr/local/bin/node_check.sh", "-a", "http://node05.iotanode.io:16265", "-i", "-m", "1.4.1.7"],
    "Interval": "30s",
    "timeout": "5s",
    "DeregisterCriticalServiceAfter": "1m"
  }
}
```

## Status

You can view the status of the backends by querying the HAProxy socket:

```sh
# echo "show stat" | socat stdio tcp4-connect:127.0.0.1:9999
# pxname,svname,qcur,qmax,scur,smax,slim,stot,bin,bout,dreq,dresp,ereq,econ,eresp,wretr,wredis,status,weight,act,bck,chkfail,chkdown,lastchg,downtime,qlimit,pid,
iid,sid,throttle,lbtot,tracked,type,rate,rate_lim,rate_max,check_status,check_code,check_duration,hrsp_1xx,hrsp_2xx,hrsp_3xx,hrsp_4xx,hrsp_5xx,hrsp_other,hanafai
l,req_rate,req_rate_max,req_tot,cli_abrt,srv_abrt,comp_in,comp_out,comp_byp,comp_rsp,lastsess,last_chk,last_agt,qtime,ctime,rtime,ttime,agent_status,agent_code,a
gent_duration,check_desc,agent_desc,check_rise,check_fall,check_health,agent_rise,agent_fall,agent_health,addr,cookie,mode,algo,conn_rate,conn_rate_max,conn_tot,
intercepted,dcon,dses,
iri_front,FRONTEND,,,0,0,360,0,0,0,0,0,0,,,,,OPEN,,,,,,,,,1,2,0,,,,0,0,0,0,,,,0,0,0,0,0,0,,0,0,0,,,0,0,0,0,,,,,,,,,,,,,,,,,,,,,http,,0,0,0,0,0,0,
iri_pow_back,irisrv1,0,0,0,0,1,0,0,0,,0,,0,0,0,0,MAINT,1,1,0,0,0,17,17,,1,3,1,,0,,2,0,,0,,,,0,0,0,0,0,0,,,,,0,0,,,,,-1,,,0,0,0,0,,,,,,,,,,,,10.20.30.40:80,,http,
,,,,,,,
iri_pow_back,irisrv2,0,0,0,0,1,0,0,0,,0,,0,0,0,0,MAINT,1,1,0,0,0,17,17,,1,3,2,,0,,2,0,,0,,,,0,0,0,0,0,0,,,,,0,0,,,,,-1,,,0,0,0,0,,,,,,,,,,,,10.20.30.40:80,,http,
,,,,,,,
iri_pow_back,irisrv3,0,0,0,0,1,0,0,0,,0,,0,0,0,0,MAINT,1,1,0,0,0,17,17,,1,3,3,,0,,2,0,,0,,,,0,0,0,0,0,0,,,,,0,0,,,,,-1,,,0,0,0,0,,,,,,,,,,,,10.20.30.40:80,,http,
,,,,,,,
iri_pow_back,BACKEND,0,0,0,0,180,0,0,0,0,0,,0,0,0,0,DOWN,0,0,0,,0,17,17,,1,3,0,,0,,1,0,,0,,,,0,0,0,0,0,0,,,,0,0,0,0,0,0,0,-1,,,0,0,0,0,,,,,,,,,,,,,,http,source,,
,,,,,
iri_back,irisrv1,0,0,0,0,,0,0,0,,0,,0,0,0,0,MAINT,1,1,0,0,0,17,17,,1,4,1,,0,,2,0,,0,,,,0,0,0,0,0,0,,,,,0,0,,,,,-1,,,0,0,0,0,,,,,,,,,,,,10.20.30.40:80,,http,,,,,,
,,
iri_back,irisrv2,0,0,0,0,7,0,0,0,,0,,0,0,0,0,UP 1/4,1,1,0,0,0,17,0,,1,4,2,,0,,2,0,,0,INI,,,0,0,0,0,0,0,,,,,0,0,,,,,-1,,,0,0,0,0,,,,Initializing,,2,4,2,,,,10.10.10.110:15265,,http,,,,,,,,
iri_back,irisrv3,0,0,0,0,,0,0,0,,0,,0,0,0,0,MAINT,1,1,0,0,0,17,17,,1,4,3,,0,,2,0,,0,,,,0,0,0,0,0,0,,,,,0,0,,,,,-1,,,0,0,0,0,,,,,,,,,,,,10.20.30.40:80,,http,,,,,,
,,
iri_back,BACKEND,0,0,0,0,180,0,0,0,0,0,,0,0,0,0,UP,1,1,0,,0,17,0,,1,4,0,,0,,1,0,,0,,,,0,0,0,0,0,0,,,,0,0,0,0,0,0,0,-1,,,0,0,0,0,,,,,,,,,,,,,,http,source,,,,,,,
stats,FRONTEND,,,0,0,720,0,0,0,0,0,0,,,,,OPEN,,,,,,,,,1,5,0,,,,0,0,0,0,,,,0,0,0,0,0,0,,0,0,0,,,0,0,0,0,,,,,,,,,,,,,,,,,,,,,http,,0,0,0,0,0,0,
stats,BACKEND,0,0,0,0,72,0,0,0,0,0,,0,0,0,0,UP,0,0,0,,0,17,,,1,5,0,,0,,1,0,,0,,,,0,0,0,0,0,0,,,,0,0,0,0,0,0,0,-1,,,0,0,0,0,,,,,,,,,,,,,,http,roundrobin,,,,,,,
```

What you see above are all the reserved slots (in this case just 3 per backend). On the `iri_back` (default backend) there's one new registered service which is being initialized. HAProxy by default runs 4 checks to verify the node is healthy (this is in addition to Consul's checks).

If the node becomes unhealthy or de-registered in Consul, the status will turn from UP to MAINT (the slots stays reserved for this node in case it returns to a healthy state).

If a new node is registered and has no more available slots it will take the slot of other nodes in MAINT status.

There is no problem to assign a large number of available slots (up to thousands) if you want to serve that many nodes. This can be configured via the playbook in `group_vars/all/all.yml` under `max_pow_backend_slots` and `max_backend_slots`.
