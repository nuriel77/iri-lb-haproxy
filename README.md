# IRI HAProxy Load-balancer

This is initial work to create a HAPRoxy load balancer for IRI, which can also be deployed in a highly available setup.

The configuration backend is Consul. In addition to consul, Consul Template binary is used to watch services in Consul and update Haproxy.

See Consul's documentation for more information about it: https://www.consul.io/docs/index.html

And Consul-Template: https://github.com/hashicorp/consul-template


## TODO

* Add more usage examples to the README / write a short blog with usage examples
* Test support of certbot script with this installation
* Test support of HTTPS backends client verification
* Provide helper script to add/remove/update services (nodes)

## Table of contents

  * [Requirements](#requirements)
  * [Warning](#warning)
  * [Installation](#installation)
  * [Uninstall](#uninstall)
  * [Controlling Consul and Haproxy](#controlling-consul-and-haproxy)
  * [Overview](#overview)
  * [HAProxy](#haproxy)
    * [Commands](#commands)
  * [Consul](#consul)
    * [Commands](#commands)
  * [Service JSON Files](#service-json-files)
  * [Status](#status)
  * [Appendix](#appendix)
    * [File Locations](#file-locations)
    * [Run with Docker Compose](#run-with-docker-compose)

## Requirements

In order to use this PoC you must have the following installed on your server:

* Ansible (>=2.4)
* CentOS (>=7.4) or Ubuntu (>=16.04)

If you don't have Docker installed, you can use the playbook to install it. See the "Installation" chapter below.

## Warning

This PoC playbook will install some packages on the system to allow it to operate and configure the services properly. **Do not use this installation on a production service!**

## Installation

There are two ways to get up and running. The best and most complete is via Ansible. The second is building the Consul container yourself and running via the provided docker-compose.yml file. See the Appendix chapter for more information on running via Docker Compose.

To install Ansible see https://docs.ansible.com/ansible/latest/installation_guide/intro_installation.html

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

*NOTE* that with Ubuntu Bionic (18.04+) you need to add the option `-e docker_apt_release_channel=edge`!

If you want the playbook to configure the firewall, add the option `-e configure_firewall=true`.
```sh
ansible-playbook -i inventory -v site.yml -e install_docker=true -e configure_firewall=true
```
*NOTE* that to specify an alternative SSH port use the option `-e ssh_port=[port number]`

## Uninstall

Uninstall is best effort. It will remove all configured files, users, data directories, services, docker containers and images.

```sh
ansible-playbook -i inventory site.yml -v --tags=uninstall -e uninstall_playbook=yes
```

## Controlling Consul, Consul-template and Haproxy

The playbook should start Consul and Haproxy for you.

To control a service (stop/restart/reload/stop) use the following syntax, e.g:

```sh
systemctl stop consul
```

consul-template:

```sh
systemctl restart consul-template
```

or haproxy:

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

We are able to add some meta-tags that are going to help control a configuration per IRI node, for example, whether to check if this node has PoW, whether we should authenticate to it via HTTPS, define timeout, weight, max connections and so on.

Once a service is registered Consul begins to run periodic health checks (defined by our custom script). If the health check is successful or failed will determine if HAProxy keeps it in its pool as a valid node to proxy traffic to.

Consul-template watches for any changes in consul (new/removed services, health check status etc) and updates haproxy's configuration if needed.

## HAProxy

HAProxy is configured by default with 2 backends:

1. Default backend

2. PoW backend (has a lower maxconn per backend to avoid PoW DoS)

Consul-template uses a haproxy.cfg.tmpl file -- this file is configured on the fly and provided to haproxy.

### Commands

Example view stats from admin TCP socket:

```sh
echo "show stat" | socat stdio tcp4-connect:127.0.0.1:9999
```
Alternatively, use a helper script:

```sh
show-stat
```
or
```sh
show-stat services
```

## Consul

Consul exposes a simple API to register services and healthchecks. Each registered service includes a healthcheck (a simple script) that concludes whether a service is healthy or not. Based on the service's health the backend becomes active or disabled in HAProxy.

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

In the directory `roles/shared-files` you will find some JSON files, which are service and health checks definitions. Those are used to register a new service (IRI node) to consul.

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
  "ID": "10.10.0.110:15265",     <--- Service unique ID
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
    "name": "API 10.10.0.110:15265",
    "args": ["/scripts/node_check.sh", "-a", "http://10.10.0.110:15265", "-i", "-p"], <--- Note the `-p` in the arguments, that means we validate PoW works.
    "Interval": "30s",
    "timeout": "5s",
    "DeregisterCriticalServiceAfter": "1m"
  }
}
```

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

## Status

To view a compact view of HAProxy's current status run:
```sh
show-stat
```

This will result in something like:
```sh
# pxname,svname,status,weight,addr
iri_pow_back,irisrv2,MAINT,1,149.210.154.132:14265
iri_back,irisrv2,UP,1,185.10.48.110:15265
iri_back,irisrv3,MAINT,1,201.10.48.110:15265
iri_back,irisrv4,UP,1,80.61.194.94:16265
```
We see the backend name, service slot name, status (UP or MAINT), the weight, IP address and port.

When a service is in MAINT it means it has been disabled because either health check is failing or explicitly set to maintenance mode.


## Appendix

### File Locations

Consul's configuration file:
```sh
/etc/consul/conf.d/main.json
```

Bash script that runs the IRI node health checks:
```sh
/usr/local/bin/node_check.sh
```

HAProxy's configuration file:
```sh
/etc/haproxy/haproxy.cfg
```

Consul's systemd control file:
```sh
/etc/systemd/system/consul.service
```

HAproxy's systemd control file:
```sh
/etc/systemd/system/haproxy.service
```

Consul-template systemd file:
```sh
/etc/systemd/system/consul-template.service
```

Consul-template haproxy template
```sh
/etc/haproxy/haproxy.cfg.tmpl
```

Consul template binary:
```sh
/opt/consul-template/consul-template
```

Consul template plugin script
```sh
/opt/consul-template/consul-template-plugin.py
```

### Run with Docker Compose

To avoid installing dependencies you can also run the containers using Docker Compose. See the provided `docker-compose.yml` file.

You will have to build at least the Consul container because it is customized to support some additional dependencies:

Enter the directory `roles/consul/files/docker` and run:

```sh
docker build -t consul:latest .
```
This will make the Consul image ready.

To run the containers, in the main folder, simply execute:
```sh
docker-compose up -d
```
or
```sh
docker-compose stop
```

*NOTE* consul-template cannot be run in a container as it requires access to `systemctl` commands. For now it is best left to run as a binary on the host.
