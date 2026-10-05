#!/bin/bash
# The VPN against a real provider, end to end: both accounts' tunnels at once, their exit
# addresses, the kill switch with a real OpenVPN killed, automatic restart, a soak, speed,
# DNS and IPv6. Run it before an account is moved onto the VPN, and after changing a config.
#
# It reads data-dir/default/openvpn and data-dir/second/openvpn (config.ovpn and auth.txt)
# and works on COPIES, so nothing live is touched and no credential is printed. It makes
# real connections to the provider, so run it, do not loop it. From the project folder on
# the NAS, with the image built:
#
#   docker run --rm -i --cap-add NET_ADMIN --cap-add SYS_ADMIN --device /dev/net/tun #     --sysctl net.ipv4.ip_forward=1 -v "$PWD/data-dir:/app/data-dir:ro" -v "$PWD/src:/app/src:ro" #     --entrypoint bash rewards-farmer:runtime -s < tests/integration/vpn_real_provider.sh
#
# Exit status is the number of failed checks.

cd /app
D=/tmp/d; rm -rf $D; mkdir -p $D
for a in default second; do mkdir -p $D/$a; cp -r /app/data-dir/$a/openvpn $D/$a/; done
export REWARDS_DATA_DIR=$D VPN_STATE_FILE=/tmp/ns.json REWARDS_ACCOUNTS=default,second
P=0; F=0
ok() { echo "PASS  $1"; P=$((P+1)); }
bad() { echo "FAIL  $1"; F=$((F+1)); }
st() { python -c "import json;print(json.load(open('/tmp/ns.json'))['$1']['$2'])" 2>/dev/null; }
nsx() { nsenter --net="$(st $1 netns)" "${@:2}"; }

python src/vpn_config.py run -- sleep 3600 > /tmp/sup.log 2>&1 &
SUP=$!
for i in $(seq 1 200); do [ -f /tmp/ns.json ] && break; kill -0 $SUP 2>/dev/null || break; sleep 1; done
[ -f /tmp/ns.json ] || { echo "no tunnel came up"; cat /tmp/sup.log; exit 1; }
sleep 3

echo "=== both tunnels"
[ "$(st default up)" = True ] && ok "default tunnel is up" || bad "default tunnel is up"
[ "$(st second up)" = True ] && ok "second tunnel is up" || bad "second tunnel is up"
E1=$(st default exit); E2=$(st second exit)
echo "    default exit $E1 | second exit $E2"
[ -n "$E1" ] && [ "$E1" != "$E2" ] && ok "the two accounts have different exit addresses" || bad "the two accounts have different exit addresses"
[ "${E1%.*}" != "${E2%.*}" ] && ok "...in different networks (/24)" || bad "...in different networks (/24)"
[ "$(st default timezone)" = "America/New_York" ] && ok "timezone is carried to the account" || bad "timezone is carried to the account"

echo "=== what each account's traffic really looks like from outside"
A1=$(nsx default curl -s -m 12 https://api.ipify.org); A2=$(nsx second curl -s -m 12 https://api.ipify.org)
echo "    default seen as $A1 | second seen as $A2"
[ "$A1" = "$E1" ] && ok "default's traffic leaves from its own exit address" || bad "default's traffic leaves from its own exit address"
[ "$A2" = "$E2" ] && ok "second's traffic leaves from its own exit address" || bad "second's traffic leaves from its own exit address"
HOME_IP=$(curl -s -m 10 https://api.ipify.org)
echo "    (the container's own address is $HOME_IP)"
[ "$A1" != "$HOME_IP" ] && [ "$A2" != "$HOME_IP" ] && ok "neither account is seen from the real home address" || bad "neither account is seen from the real home address"

echo "=== speed and name lookups"
nsx default curl -s -m 40 -o /dev/null -w "    default download: %{speed_download} bytes/s\n" "https://speed.cloudflare.com/__down?bytes=8000000"
nsx second curl -s -m 40 -o /dev/null -w "    second download:  %{speed_download} bytes/s\n" "https://speed.cloudflare.com/__down?bytes=8000000"
nsx default getent hosts example.com >/dev/null && ok "names resolve inside the namespace" || bad "names resolve inside the namespace"
echo "    resolvers: $(grep nameserver /etc/resolv.conf | awk '{print $2}' | tr '\n' ' ')"
nsx default curl -6 -s -m 5 -o /dev/null https://api64.ipify.org && bad "IPv6 request stayed inside" || ok "an IPv6 request does not get out"

echo "=== kill switch with the REAL tunnel: kill default's OpenVPN"
T0=$(date +%s)
# Find default's OpenVPN by the namespace it runs in: its config now sits in a private,
# randomly named directory, so a name match finds nothing.
DEFAULT_NS=$(readlink "$(st default netns)")
KILLED=0
for pid in $(pgrep -x openvpn); do
  if [ "$(readlink /proc/$pid/ns/net 2>/dev/null)" = "$DEFAULT_NS" ]; then kill $pid && KILLED=$((KILLED+1)); fi
done
[ $KILLED -ge 1 ] && ok "killed default's OpenVPN ($KILLED process)" || bad "found default's OpenVPN to kill"
sleep 1
nsx default curl -s -m 4 -o /dev/null https://api.ipify.org && bad "default leaked while its tunnel was down" || ok "default has no internet while its tunnel is down"
nsx default ip route add default via 10.200.1.1 2>/dev/null
nsx default curl -s -m 4 -o /dev/null https://api.ipify.org && bad "default leaked with the route pointed at the real side" || ok "pointing the route at the real side still gets nothing out"
nsx second curl -s -m 12 -o /dev/null https://api.ipify.org && ok "second is untouched while default is down" || bad "second is untouched while default is down"
echo "    waiting for the supervisor to bring default back..."
# The state file can still say "up" for a few seconds after the kill, until the supervisor
# looks, so wait for the log line that says the tunnel is back, not for the state.
for i in $(seq 1 120); do grep -q "default: tunnel up again" /tmp/sup.log && break; sleep 1; done
T1=$(date +%s)
sleep 2
grep -q "default: the tunnel died" /tmp/sup.log && ok "the supervisor noticed the tunnel died" || bad "the supervisor noticed the tunnel died"
grep -q "default: tunnel up again" /tmp/sup.log && [ "$(st default up)" = True ] && ok "default's tunnel came back on its own ($((T1-T0))s)" || bad "default's tunnel came back on its own"
[ "$(st default exit)" = "$E1" ] && ok "...on the same exit address ($E1)" || bad "...on the same exit address (now $(st default exit))"
nsx default curl -s -m 12 -o /dev/null https://api.ipify.org && ok "default has internet again through the tunnel" || bad "default has internet again through the tunnel"

echo "=== soak: 6 checks, 20s apart, both accounts"
SAME=1
for i in 1 2 3 4 5 6; do
  sleep 20
  X1=$(nsx default curl -s -m 12 https://api.ipify.org); X2=$(nsx second curl -s -m 12 https://api.ipify.org)
  [ "$X1" = "$E1" ] && [ "$X2" = "$E2" ] || { SAME=0; echo "    check $i: default $X1 second $X2"; }
done
[ $SAME = 1 ] && ok "both exits stayed the same across the soak" || bad "both exits stayed the same across the soak"
DIED=$(grep -c "the tunnel died" /tmp/sup.log)
echo "    tunnel deaths in the log: $DIED (1 expected: the one we caused)"
[ "$DIED" -le 1 ] && ok "no unexpected tunnel drops" || bad "no unexpected tunnel drops"

echo "=== stopping"
kill -TERM $SUP; wait $SUP; C=$?
[ $C -eq 143 ] && ok "a stop signal ends the supervisor cleanly" || bad "a stop signal ends the supervisor cleanly (exit $C)"
pgrep -f "sleep infinity" >/dev/null && bad "no namespace holder left running" || ok "no namespace holder left running"
echo; echo "$P passed, $F failed"
echo "--- supervisor log"; cat /tmp/sup.log | cut -c1-170
exit $F
