#!/bin/bash
# Three accounts on the VPN at once against the real provider: all three tunnels, three different
# exit addresses in three different networks, each seen from its own exit and none from the home
# address, and the kill switch and automatic restart for the third.
#
# Reads data-dir/{default,second,third}/openvpn (config.ovpn and auth.txt) and works on COPIES, so
# nothing live is touched and no credential is printed. It makes real connections, so run it, do not
# loop it. From the project folder on the NAS, with the image built:
#
#   docker run --rm -i --cap-add NET_ADMIN --cap-add SYS_ADMIN --device /dev/net/tun \
#     --sysctl net.ipv4.ip_forward=1 -v "$PWD/data-dir:/app/data-dir:ro" -v "$PWD/src:/app/src:ro" \
#     --entrypoint bash rewards-farmer:runtime -s < tests/integration/vpn_three_accounts.sh
#
# Exit status is the number of failed checks.

cd /app
D=/tmp/d; rm -rf $D; mkdir -p $D
for a in default second third; do mkdir -p $D/$a; cp -r /app/data-dir/$a/openvpn $D/$a/; done
export REWARDS_DATA_DIR=$D VPN_STATE_FILE=/tmp/ns.json REWARDS_ACCOUNTS=default,second,third
P=0; F=0
ok() { echo "PASS  $1"; P=$((P+1)); }
bad() { echo "FAIL  $1"; F=$((F+1)); }
st() { python -c "import json;print(json.load(open('/tmp/ns.json'))['$1']['$2'])" 2>/dev/null; }
nsx() { nsenter --net="$(st $1 netns)" "${@:2}"; }

python src/vpn_config.py run -- sleep 3600 > /tmp/sup.log 2>&1 &
SUP=$!
for i in $(seq 1 240); do [ -f /tmp/ns.json ] && break; kill -0 $SUP 2>/dev/null || break; sleep 1; done
[ -f /tmp/ns.json ] || { echo "no tunnel came up"; cat /tmp/sup.log; exit 1; }
for i in $(seq 1 60); do
  [ "$(st default up)" = True ] && [ "$(st second up)" = True ] && [ "$(st third up)" = True ] && break
  sleep 1
done
sleep 3

echo "=== three tunnels"
for a in default second third; do
  [ "$(st $a up)" = True ] && ok "$a tunnel is up" || bad "$a tunnel is up"
done
E1=$(st default exit); E2=$(st second exit); E3=$(st third exit)
echo "    default exit $E1 | second exit $E2 | third exit $E3"
[ -n "$E3" ] && [ "$E1" != "$E2" ] && [ "$E1" != "$E3" ] && [ "$E2" != "$E3" ] && ok "three different exit addresses" || bad "three different exit addresses"
[ "${E1%.*}" != "${E2%.*}" ] && [ "${E1%.*}" != "${E3%.*}" ] && [ "${E2%.*}" != "${E3%.*}" ] && ok "...in three different networks (/24)" || bad "...in three different networks (/24)"
[ "$(st third timezone)" = "America/New_York" ] && ok "third's timezone is carried to the account" || bad "third's timezone is carried to the account"

echo "=== what each account's traffic really looks like from outside"
A1=$(nsx default curl -s -m 12 https://api.ipify.org); A2=$(nsx second curl -s -m 12 https://api.ipify.org); A3=$(nsx third curl -s -m 12 https://api.ipify.org)
echo "    default seen as $A1 | second seen as $A2 | third seen as $A3"
[ "$A1" = "$E1" ] && [ "$A2" = "$E2" ] && [ "$A3" = "$E3" ] && ok "each account's traffic leaves from its own exit address" || bad "each account's traffic leaves from its own exit address"
HOME_IP=$(curl -s -m 10 https://api.ipify.org)
echo "    (the container's own address is $HOME_IP)"
[ "$A1" != "$HOME_IP" ] && [ "$A2" != "$HOME_IP" ] && [ "$A3" != "$HOME_IP" ] && ok "no account is seen from the real home address" || bad "no account is seen from the real home address"
nsx third getent hosts example.com >/dev/null && ok "names resolve inside third's namespace" || bad "names resolve inside third's namespace"
nsx third curl -6 -s -m 5 -o /dev/null https://api64.ipify.org && bad "IPv6 request stayed inside third" || ok "an IPv6 request does not get out of third"
nsx third curl -s -m 40 -o /dev/null -w "    third download: %{speed_download} bytes/s\n" "https://speed.cloudflare.com/__down?bytes=8000000"

echo "=== kill switch with the REAL tunnel: kill third's OpenVPN"
T0=$(date +%s)
THIRD_NS=$(readlink "$(st third netns)")
KILLED=0
for pid in $(pgrep -x openvpn); do
  if [ "$(readlink /proc/$pid/ns/net 2>/dev/null)" = "$THIRD_NS" ]; then kill $pid && KILLED=$((KILLED+1)); fi
done
[ $KILLED -ge 1 ] && ok "killed third's OpenVPN ($KILLED process)" || bad "found third's OpenVPN to kill"
sleep 1
nsx third curl -s -m 4 -o /dev/null https://api.ipify.org && bad "third leaked while its tunnel was down" || ok "third has no internet while its tunnel is down"
nsx third ip route add default via 10.200.3.1 2>/dev/null
nsx third curl -s -m 4 -o /dev/null https://api.ipify.org && bad "third leaked with the route pointed at the real side" || ok "pointing the route at the real side still gets nothing out"
nsx default curl -s -m 12 -o /dev/null https://api.ipify.org && nsx second curl -s -m 12 -o /dev/null https://api.ipify.org && ok "default and second are untouched while third is down" || bad "default and second are untouched while third is down"
echo "    waiting for the supervisor to bring third back..."
for i in $(seq 1 120); do grep -q "third: tunnel up again" /tmp/sup.log && break; sleep 1; done
T1=$(date +%s)
sleep 2
grep -q "third: the tunnel died" /tmp/sup.log && ok "the supervisor noticed the tunnel died" || bad "the supervisor noticed the tunnel died"
grep -q "third: tunnel up again" /tmp/sup.log && [ "$(st third up)" = True ] && ok "third's tunnel came back on its own ($((T1-T0))s)" || bad "third's tunnel came back on its own"
[ "$(st third exit)" = "$E3" ] && ok "...on the same exit address ($E3)" || bad "...on the same exit address (now $(st third exit))"
nsx third curl -s -m 12 -o /dev/null https://api.ipify.org && ok "third has internet again through the tunnel" || bad "third has internet again through the tunnel"

echo "=== soak: 4 checks, 20s apart, all three accounts"
SAME=1
for i in 1 2 3 4; do
  sleep 20
  X1=$(nsx default curl -s -m 12 https://api.ipify.org); X2=$(nsx second curl -s -m 12 https://api.ipify.org); X3=$(nsx third curl -s -m 12 https://api.ipify.org)
  [ "$X1" = "$E1" ] && [ "$X2" = "$E2" ] && [ "$X3" = "$E3" ] || { SAME=0; echo "    check $i: default $X1 second $X2 third $X3"; }
done
[ $SAME = 1 ] && ok "all three exits stayed the same across the soak" || bad "all three exits stayed the same across the soak"
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
