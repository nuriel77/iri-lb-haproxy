#!/usr/bin/python
import requests
import socket
import time
import json
import sys
from pprint import pprint
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry


class Struct:
    def __init__(self, **entries):
        self.__dict__.update(entries)


args = Struct(**dict(
    consul_api_server = "http://localhost:8500",
    consul_service = "my.iota.lb",
    haproxy_server = "/var/run/hapee-lb.sock",
    #haproxy_server = ('127.0.0.1', 9999)
    backend_name = "iri_back",
    pow_backend_name = "iri_pow_back",
    template_addr = '10.20.30.40:80',
    haproxy_spare_slots = 0,
    backend_base_name = 'irisrv',
    consul_tag_prefix = 'haproxy',
    socket_connect_retry = 30,
    socket_connect_timeout = 10,
    consul_token = 'xxxx-xxxx-xxxx-xxxx',
    valid_tags = ['sslverify', 'maxconn', 'weight', 'scheme', 'pow']
))


def send_haproxy_command(command):
    global args
    print("socat command: %s" % command)
    i = args.socket_connect_retry
    while i > 0:
        i -= 1

        if args.haproxy_server[0] == "/":
            haproxy_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        else:
            haproxy_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        haproxy_sock.settimeout(args.socket_connect_timeout)
        try:
            haproxy_sock.connect(args.haproxy_server)
            haproxy_sock.send(command)
            retval = ""
            while True:
                buf = haproxy_sock.recv(16)
                if buf:
                    retval += buf
                else:
                    break
            haproxy_sock.close()
        except Exception as e:
            print("Error reading from haproxy socket: %s. Attempts left: %d" %
                  (e, i))
            retval = ""
        else:
            #if retval != "":
            #    print("socat response: %s" % retval)
            return retval
        finally:
            haproxy_sock.close()
        time.sleep(0.5)


def get_consul_data(url):
    global args

    i = args.socket_connect_retry
    while i > 0:
        i -= 1
        try:
            s = requests.Session()
            s.headers.update({'X-Consul-Token': args.consul_token})
            consul_json = requests.get(url)
        except Exception as e:
            print("Failed to get backend list from Consul: %s,"
                  " attempts left: %d" % (e, i))
            time.sleep(0.5)
            continue
        else:
            consul_json.raise_for_status()
            return consul_json.json()

    sys.exit(1)

def get_service_health(checks, service_id):
    for check in checks:
        if check['ServiceID'] == service_id:
            return check['Status']


def parse_consul_services(consul_services, consul_checks):
    global args

    services = []
    for server in consul_services:
        server_health = get_service_health(consul_checks, '%s' %
                                           server['ServiceID'])

        server_data = {
            'ServiceAddress': server['ServiceAddress'],
            'ServicePort': server['ServicePort'],
            'ServiceID': server['ServiceID'],
            'weight': None,
            'scheme': 'http',
            'sslverify': 1,
            'status': server_health
        }

        # Parse service tags
        for tag in server['ServiceTags']:
            if args.consul_tag_prefix in tag:
                try:
                    # haproxy.somekey=someval
                    tag_keyval = tag.split('.')[1]
                    tag_key, tag_val = tag_keyval.split('=')
                    if tag_key in args.valid_tags:
                        server_data[tag_key] = tag_val
                except Exception as e:
                    print("Failed to get valid tag from '%s': %s" % (tag, e))

        services.append(server_data)

    if len(services) < 1:
        sys.exit(0)
    return services


def get_haproxy_backends(haproxy_slots, backend_name=None):
    global args

    haproxy_slots = haproxy_slots.split('\n')
    haproxy_backends = {
        'active_backends': {},
        'inactive_backends': {}
    }
    for backend in haproxy_slots:
        backend_values = backend.split(",")

        if len(backend_values) < 81 or backend_values[0] != backend_name:
            continue

        server_name = backend_values[1]
        if server_name == "BACKEND":
            continue

        server_data = {
            'address': backend_values[73],
            'backend_name': backend_values[0],
            'maxconn': backend_values[6],
            'weight': backend_values[18],
            'state': backend_values[17],
            'slot': backend_values[28]
        }

        if server_data['state'] == "MAINT":
            # Any server in MAINT is assumed to be unconfigured and free to use
            # (to stop a server for your own work try 'DRAIN' for the script to just skip it)
            haproxy_backends['inactive_backends'][server_name] = server_data
        else:
            haproxy_backends['active_backends'][server_name] = server_data
        
    return haproxy_backends


def find_server_in_backend(server_addr, backend):
    for slot, data in backend.iteritems():
        if 'address' in data and data['address'] == server_addr:
            return data


def service_update_active_backend(service, haproxy_data, backend_name=None):
    global args
    # Server health critical, update in haproxy
    set_maint = False
    if service['status'] != 'passing' and haproxy_data['state'] != 'MAINT':
        set_maint = True

    if (backend_name == args.pow_backend_name and
      ('pow' not in service or service['pow'] != 'true')):
        set_maint = True

    if (backend_name == args.backend_name and
      ('pow' in service and service['pow'] == 'true')):
        return

    if set_maint is True:
        print("service %s update active %s backend" %
              (service['ServiceID'], backend_name))
        send_haproxy_command("set server %s/%s state maint\n" %
                             (backend_name,
                              args.backend_base_name + haproxy_data['slot']))


def service_update_inactive_backend(service, haproxy_data, backend_name=None):
    global args
    if service['status'] != 'passing':
        return

    if (backend_name == args.pow_backend_name and
     ('pow' not in service or service['pow'] != 'true')):
        # Continue
        return True

    print("service %s update inactive %s backend" % (service['ServiceID'], backend_name))
    send_haproxy_command("set server %s/%s state ready\n" %
                         (backend_name,
                          args.backend_base_name + haproxy_data['slot']))


def service_register(service, haproxy_data, backend_name,
                     last_slot, backend_slots):
    global args

    parse_len = len(args.backend_base_name)
    slot = None
    for i, slot in enumerate(sorted(
                             backend_slots,
                             key=lambda s: int(s[parse_len:]))[last_slot-1:]):

        if backend_slots[slot]['state'] == 'MAINT':
            print("Free slot at index %s for backend %s" %
                  (backend_slots[slot]['slot'], backend_name))
            slot = backend_slots[slot]['slot']
            if (len(backend_slots.keys()[last_slot-1:]) - (i + 1)) == 1:
                print("Warning! Only 1 slot left in backend %s,"
                      " cannot assign more slots" % backend_name)

            break
    
    if slot is None:
        print("ERROR: No more slots available in backend %s" % backend_name)
        sys.exit(0)

    send_haproxy_command("set server %s/%s addr %s port %s check\n" %
                         (backend_name,
                          args.backend_base_name + slot,                          
                          service['ServiceAddress'],
                          service['ServicePort']))

    if 'maxconn' in service:
        send_haproxy_command("set maxconn server %s/%s %s\n" %
                             (backend_name,
                              args.backend_base_name + slot,
                              service['maxconn']))

    if 'weight' in service and service['weight'] is not None:
        send_haproxy_command("set server %s/%s weight %s\n" %
                             (backend_name,
                              args.backend_base_name + slot,
                              service['weight']))

    send_haproxy_command("set server %s/%s state ready\n" %
                         (backend_name,
                          args.backend_base_name + slot))

    return int(slot) + 1


def check_deregister(haproxy_pow_backend, haproxy_default_backend,
                     consul_services):
    global args
    for _, data in haproxy_pow_backend.iteritems():
        found = False
        for service in consul_services:
            if (service['ServiceID'] == data['address'] and
              data['state'] != 'MAINT'):
                found = True
                break
        print "%s found in consul: %s" % (service['ServiceID'], found)
        if found is False:
            send_haproxy_command("set server %s/%s state maint\n" %
                                 (args.pow_backend_name,
                                  args.backend_base_name + data['slot']))

    for _, data in haproxy_default_backend.iteritems():
        found = False
        for service in consul_services:
            if (service['ServiceID'] == data['address'] and
              data['state'] != 'MAINT'):
                found = True
                break
        print "%s found in consul: %s" % (service['ServiceID'], found)
        if found is False:
            send_haproxy_command("set server %s/%s state maint\n" %
                                 (args.backend_name,
                                  args.backend_base_name + data['slot']))


def process_services():
    global args

    json_data = []
    consul_services = []

    # Try from stdin
    for line in sys.stdin:
        json_data.append(line)

    try:
        consul_services = json.loads(json_data)
    except:
        print("No services json in stdin")
        if not len(consul_services):        
            # Get consul services
            url = "%s/v1/catalog/service/%s" % (args.consul_api_server,
                                                args.consul_service)
            consul_services = get_consul_data(url)

    # Get consul checks
    url = "%s/v1/health/checks/%s" % (args.consul_api_server,
                                      args.consul_service)
    consul_checks = get_consul_data(url)

    # Populatee consul services data
    consul_services = parse_consul_services(consul_services, consul_checks)

    #pprint(consul_services)

    # Get haproxy slots
    haproxy_slots = send_haproxy_command("show stat\n")
    if not haproxy_slots:
        print("Failed to get current backend list from HAProxy socket.")
        sys.exit(3)

    haproxy_pow_backend = get_haproxy_backends(haproxy_slots,
                                               backend_name=args.pow_backend_name)

    haproxy_default_backend = get_haproxy_backends(haproxy_slots,
                                                   backend_name=args.backend_name)

    # Check deregistered service
    check_deregister(haproxy_pow_backend['active_backends'],
                     haproxy_default_backend['active_backends'],
                     consul_services)

    #print("POW BE:")
    #pprint(haproxy_pow_backend)
    #print("DEF BE:")
    #pprint(haproxy_default_backend)
    pow_slot = 2
    default_slot = 2
    for service in consul_services:
        server_addr = "%s:%s" % (service['ServiceAddress'], \
                                 service['ServicePort'])
        service_id = service['ServiceID']

        haproxy_data = None

        # Service found in active pow backend
        haproxy_data = find_server_in_backend(server_addr,
                                              haproxy_pow_backend['active_backends'])
        if haproxy_data:
            print ("%s found in active pow backend" % service_id)
            service_update_active_backend(service, haproxy_data,
                                          backend_name=args.pow_backend_name)
            pow_slot += 1
            continue

        # Service found in active default backend
        haproxy_data = find_server_in_backend(server_addr,
                                              haproxy_default_backend['active_backends'])
        if haproxy_data:
            print ("%s found in active default backend" % service_id)
            service_update_active_backend(service, haproxy_data,
                                          backend_name=args.backend_name)
            default_slot += 1
            continue

        # Service found in inactive pow backend
        haproxy_data = find_server_in_backend(server_addr,
                                              haproxy_pow_backend['inactive_backends'])
        if haproxy_data:
            print ("%s found in inactive pow backend" % service_id)
            if service_update_inactive_backend(service, haproxy_data,
                                               backend_name=args.pow_backend_name):
                print("Wrong backend, proceed to next check.")
            else:    
                pow_slot += 1
                continue

        # Service found in inactive default backend
        haproxy_data = find_server_in_backend(server_addr,
                                              haproxy_default_backend['inactive_backends'])
        if haproxy_data:
            print ("%s found in inactive default backend" % service_id)
            service_update_inactive_backend(service, haproxy_data,
                                             backend_name=args.backend_name)
            default_slot += 1
            continue

        # Service not found
        print ("%s not found in any backend" % service_id)

        if 'pow' in service and service['pow'] == 'true':
            pow_slot = service_register(service, haproxy_data,
                                        args.pow_backend_name,
                                        pow_slot,
                                        haproxy_pow_backend['inactive_backends'])
            print("pow slot now at %s" % pow_slot)
        else:
            default_slot = service_register(service, haproxy_data,
                                            args.backend_name,
                                            default_slot,
                                            haproxy_default_backend['inactive_backends'])
            print("default slot now at %s" % default_slot)


if __name__ == "__main__":
    process_services()
