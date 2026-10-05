#!/bin/bash
# Real-namespace test of src/vpn_config.py and src/isolation.py.
#
# Unit tests fake the system calls. This runs the real thing, with a stand-in for
# OpenVPN (a veth pair that NATs out through the container, so a "tunnel" really
# carries traffic), and checks what the unit tests cannot: that a namespace really
# is separate, that traffic really goes out through tun0, that the kill switch
# really blocks everything else when the tunnel drops, and that the supervisor
# really restarts it.
#
# Needs a host that allows NET_ADMIN and SYS_ADMIN, and outbound internet. From the
# repository root, with the image built (docker compose build rewards-farmer):
#
#   docker run --rm --cap-add NET_ADMIN --cap-add SYS_ADMIN --device /dev/net/tun \
#     --sysctl net.ipv4.ip_forward=1 -v "$PWD/tests/integration:/it:ro" \
#     --entrypoint bash rewards-farmer:runtime /it/vpn_netns.sh
#
# It touches only this container. Exit status is the number of failed checks.

set -u
cd /app

PASS=0
FAIL=0
ok() { echo "PASS  $1"; PASS=$((PASS + 1)); }
bad() { echo "FAIL  $1"; FAIL=$((FAIL + 1)); }
check() { local d=$1; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
checknot() { local d=$1; shift; if "$@" >/dev/null 2>&1; then bad "$d"; else ok "$d"; fi; }

W=/tmp/it
rm -rf $W && mkdir -p $W/data
export REWARDS_DATA_DIR=$W/data VPN_STATE_FILE=$W/state.json REWARDS_ACCOUNTS=alpha,beta
export VPN_IP_CHECK_URL=http://10.8.1.1:8080/ NAME_SERVERS=1.1.1.1,1.0.0.1 HOME_IP_BLACKLIST=

state() { python -c "import json,sys; d=json.load(open('$VPN_STATE_FILE')); print(d['$1']['$2'])" 2>/dev/null; }
ns() { nsenter --net="$(state "$1" netns)" "${@:2}"; }
wait_for() { local t=$1; shift; for _ in $(seq 1 "$t"); do "$@" && return 0; sleep 1; done; return 1; }
is_up() { [ "$(state "$1" up)" = "True" ]; }

# --- a stand-in provider: OpenVPN is a veth pair NATed out through the container ---
cat >/usr/local/bin/openvpn <<'STUB'
#!/bin/bash
# Stand-in for OpenVPN, run inside an account's namespace. Not a real VPN: tun0 is
# one end of a veth pair whose other end sits in the container and is NATed out.
cfg=""
while [ $# -gt 0 ]; do [ "$1" = "--config" ] && cfg=$2; shift; done
N=$(ip -o link | grep -o 'vn[0-9]*' | head -1 | tr -d vn)
ip link del tun0 2>/dev/null
ip link add tun0 type veth peer name tp$N || exit 1
ip link set tp$N netns 1
ip addr add 10.8.$N.2/24 dev tun0 && ip link set tun0 up
# What real OpenVPN does: the server itself stays reachable the old way.
for ip in $(grep -E '^remote ' "$cfg" | awk '{print $2}'); do ip route add $ip via 10.200.$N.1 2>/dev/null; done
ip route replace default dev tun0
nsenter --net=/proc/1/ns/net sh -c "
  ip addr replace 10.8.$N.1/24 dev tp$N; ip link set tp$N up
  iptables -C FORWARD -i tp$N -j ACCEPT 2>/dev/null || iptables -I FORWARD -i tp$N -j ACCEPT
  iptables -C FORWARD -o tp$N -j ACCEPT 2>/dev/null || iptables -I FORWARD -o tp$N -j ACCEPT
  iptables -t nat -C POSTROUTING -s 10.8.$N.0/24 -o eth0 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s 10.8.$N.0/24 -o eth0 -j MASQUERADE"
exec sleep infinity
STUB
chmod +x /usr/local/bin/openvpn

# alpha and beta have different VPN servers, as real accounts must.
for a in alpha beta; do mkdir -p $W/data/$a/openvpn; done
printf 'client\nproto tcp\nremote 1.1.1.1 443 tcp\n' >$W/data/alpha/openvpn/config.ovpn
printf 'client\nproto tcp\nremote 1.0.0.1 443 tcp\n' >$W/data/beta/openvpn/config.ovpn

# What a server would see: a different "exit address" for each account.
python - <<'PY' &
import http.server
class H(http.server.BaseHTTPRequestHandler):
	def do_GET(self):
		body = ("203.0.113." + self.client_address[0].split(".")[2]).encode()
		self.send_response(200); self.end_headers(); self.wfile.write(body)
	def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
PY
sleep 1

# --- a config that is missing stops everything before anything is built ---
REWARDS_ACCOUNTS=alpha,gamma python src/vpn_config.py run -- sleep 5 >$W/missing.log 2>&1
check "a missing config stops the container" [ $? -ne 0 ]
checknot "...and no namespace was built" pgrep -f "sleep infinity"

# --- the real thing ---
python src/vpn_config.py run -- sleep 3600 >$W/sup.log 2>&1 &
SUP=$!

wait_for 90 is_up alpha && wait_for 30 is_up beta
check "both tunnels come up" [ "$(state alpha up)" = True ] && [ "$(state beta up)" = True ]
check "each account has a different exit address" [ "$(state alpha exit)" != "$(state beta exit)" ]
check "the exit addresses are the stand-in's per-account ones" [ "$(state alpha exit)" = 203.0.113.1 ] && [ "$(state beta exit)" = 203.0.113.2 ]

# separation
check "alpha's namespace has its own tun0 and veth" bash -c "nsenter --net=$(state alpha netns) ip -o link | grep -q tun0 && nsenter --net=$(state alpha netns) ip -o link | grep -q vn1"
checknot "alpha cannot see beta's veth" bash -c "nsenter --net=$(state alpha netns) ip -o link | grep -q vn2"
checknot "alpha cannot see beta's tunnel end" bash -c "nsenter --net=$(state alpha netns) ip -o link | grep -q tp2"
check "the namespaces are different" [ "$(readlink "$(state alpha netns)")" != "$(readlink "$(state beta netns)")" ]
check "...and different from the container's" [ "$(readlink "$(state alpha netns)")" != "$(readlink /proc/1/ns/net)" ]

# traffic goes out through the tunnel
check "alpha reaches the internet through its tunnel" ns alpha curl -s -m 10 -o /dev/null https://api.ipify.org
check "beta reaches the internet through its tunnel" ns beta curl -s -m 10 -o /dev/null https://api.ipify.org
check "a name resolves in the namespace" ns alpha getent hosts example.com

# the kill switch: drop the tunnel, then try to leak by pointing the route at the real side
ns alpha ip link del tun0
ns alpha ip route add default via 10.200.1.1
checknot "tunnel down: the internet is not reachable on the real side" ns alpha curl -s -m 6 -o /dev/null https://api.ipify.org
checknot "tunnel down: an arbitrary server is not reachable" ns alpha curl -s -m 6 -o /dev/null https://8.8.8.8
checknot "tunnel down: a name lookup does not get out" ns alpha curl -s -m 6 -o /dev/null http://example.com
check "tunnel down: the VPN server itself is still reachable (to rebuild it)" ns alpha curl -s -m 8 -k -o /dev/null https://1.1.1.1
checknot "tunnel down: beta's VPN server is not reachable from alpha" ns alpha curl -s -m 6 -k -o /dev/null https://1.0.0.1
check "beta is untouched while alpha is down" ns beta curl -s -m 10 -o /dev/null https://api.ipify.org

# the supervisor notices and restarts it
wait_for 15 bash -c "! [ \"\$(python -c \"import json; print(json.load(open('$VPN_STATE_FILE'))['alpha']['up'])\")\" = True ]"
check "the state says alpha is down" [ "$(state alpha up)" = False ]
wait_for 90 is_up alpha
check "alpha is restarted on its own and comes back up" [ "$(state alpha up)" = True ]
check "alpha is back on its own exit address" [ "$(state alpha exit)" = 203.0.113.1 ]
check "alpha reaches the internet again through the tunnel" ns alpha curl -s -m 10 -o /dev/null https://api.ipify.org
check "the log says the tunnel died" grep -q "alpha: the tunnel died" $W/sup.log
check "the log says it came back" grep -q "alpha: tunnel up again" $W/sup.log

# running an account's browser inside its namespace
cat >$W/child.py <<'PY'
import os, subprocess, urllib.request
print("account", os.environ.get("REWARDS_ACCOUNTS"), "isolated", os.environ.get("REWARDS_ISOLATED"))
print("exit", urllib.request.urlopen("http://10.8.1.1:8080/", timeout=8).read().decode())
print("flush", subprocess.run(["iptables", "-F"], capture_output=True).returncode)
print("delete", subprocess.run(["ip", "link", "del", "tun0"], capture_output=True).returncode)
PY
python - >$W/child.out 2>&1 <<PY
import sys
sys.path.insert(0, "/app/src")
import isolation
print("result", isolation.run("alpha", "$W/child.py", lambda: False))
PY
check "the child ran in the account's namespace as that account" grep -q "account alpha isolated 1" $W/child.out
check "the child's traffic left through alpha's tunnel" grep -q "exit 203.0.113.1" $W/child.out
checknot "the child cannot flush the firewall" grep -q "flush 0" $W/child.out
checknot "the child cannot delete the tunnel" grep -q "delete 0" $W/child.out
check "the run is reported as having worked" grep -q "result True" $W/child.out

# an account that is held is not run
python - >$W/held.out 2>&1 <<PY
import json, sys
sys.path.insert(0, "/app/src")
import isolation
state = json.load(open("$VPN_STATE_FILE"))
state["alpha"]["up"] = False
json.dump(state, open("$W/held.json", "w"))
isolation.STATE_FILE = __import__("pathlib").Path("$W/held.json")
print("result", isolation.run("alpha", "$W/child.py", lambda: True))
PY
check "an account whose tunnel is down is not run" grep -q "result False" $W/held.out

# stopping
kill -TERM $SUP
wait $SUP
CODE=$?
check "a stop signal ends the supervisor cleanly" [ $CODE -eq 143 ]
check "the state file is removed" [ ! -e $VPN_STATE_FILE ]
checknot "no namespace holder is left running" pgrep -f "sleep infinity"

echo
echo "$PASS passed, $FAIL failed"
[ $FAIL -ne 0 ] && { echo "--- supervisor log ---"; tail -30 $W/sup.log; }
exit $FAIL
