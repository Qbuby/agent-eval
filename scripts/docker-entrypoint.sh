#!/bin/sh
# Resolve the Windows host gateway and inject it into /etc/hosts as
# host.docker.internal, then exec the container command.
#
# Why: this stack runs under WSL2-native Docker Engine (NOT Docker Desktop).
# There, compose's `host.docker.internal:host-gateway` resolves to the docker
# bridge gateway (172.17.0.1 / 172.19.0.1) — the WSL VM, NOT the Windows host.
# The agent under test is reached via an SSH tunnel that listens on the
# Windows host's 0.0.0.0:18094, so the backend must target the WSL→Windows
# host gateway. That gateway IP changes across WSL restarts, but WSL keeps the
# current value in the host's /etc/resolv.conf nameserver — so we read it at
# startup and re-point host.docker.internal there. Eval and sample-generation
# then both use the stable name http://host.docker.internal:<port>/...
#
# Resolution order (first hit wins):
#   1. $AGENT_HOST_GATEWAY            — explicit override (any env / non-WSL)
#   2. nameserver in the bind-mounted WSL host resolv.conf (/run/host-resolv.conf)
#   3. container default-route gateway — last-resort fallback
#
# Failure is non-fatal: if nothing resolves we leave /etc/hosts untouched and
# still start the app (so non-WSL deployments are unaffected).
set -e

HOST_RESOLV="/run/host-resolv.conf"
gateway=""

if [ -n "$AGENT_HOST_GATEWAY" ]; then
    gateway="$AGENT_HOST_GATEWAY"
    src="AGENT_HOST_GATEWAY env"
elif [ -r "$HOST_RESOLV" ]; then
    gateway=$(awk '/^[[:space:]]*nameserver/ { print $2; exit }' "$HOST_RESOLV")
    src="WSL host resolv.conf"
fi

if [ -z "$gateway" ]; then
    # Fallback: container default route gateway (works when the host is on the
    # same bridge, e.g. plain Linux Docker; a no-op-ish guess under WSL).
    gateway=$(ip route show default 2>/dev/null | awk '/default/ { print $3; exit }')
    src="default route"
fi

if echo "$gateway" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
    # Replace any existing host.docker.internal line, then append ours.
    grep -v '[[:space:]]host\.docker\.internal$' /etc/hosts > /etc/hosts.tmp 2>/dev/null || true
    echo "$gateway	host.docker.internal" >> /etc/hosts.tmp
    cat /etc/hosts.tmp > /etc/hosts
    rm -f /etc/hosts.tmp
    echo "[entrypoint] host.docker.internal -> $gateway (via $src)"
else
    echo "[entrypoint] no host gateway resolved (src=$src, val='$gateway'); leaving /etc/hosts as-is"
fi

exec "$@"
