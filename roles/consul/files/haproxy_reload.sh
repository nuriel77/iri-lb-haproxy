#!/bin/sh

[ -n "${DEBUG}" ] && set -x

: ${HAPROXY_CONFIG_DIR:=/etc/haproxy}

# first start
if [ ! -f "$HAPROXY_CONFIG_DIR/haproxy.cfg.previous" ]; then
  echo "$0: First configuration" | logger -t haproxy-reload
  cp "$HAPROXY_CONFIG_DIR/haproxy.cfg" "$HAPROXY_CONFIG_DIR/haproxy.cfg.previous"
  systemctl haproxy start
  exit 0
fi

# configuration update occured
CHECK=$(diff -u -p "$HAPROXY_CONFIG_DIR/haproxy.cfg.previous" "$HAPROXY_CONFIG_DIR/haproxy.cfg" | egrep -c "^[-+]    server ")
echo "$0 check has diff: int $CHECK" | logger -t haproxy-reload

# we trigger a reload only when backends have been removed or added
if [ ${CHECK} -gt 0 ]; then
  echo "$0: Backend(s) has(ve) been added or removed, need to reload the configuration" | logger -t haproxy-reload
  systemctl reload haproxy
fi

cp "$HAPROXY_CONFIG_DIR/haproxy.cfg" "$HAPROXY_CONFIG_DIR/haproxy.cfg.previous"
