# IMPL.md — Simple Load Balancer (POX / OpenFlow) — Step-by-Step Guide

Source docs: [`exercise1_problem.pdf`](../problem%20statement/exercise1_problem.pdf) (spec) +
[`exercise1_reading.pdf`](../problem%20statement/exercise1_reading.pdf) (tutorial).
Implementation: [`SimpleLoadBalancer.py`](../SimpleLoadBalancer.py) (at the project root,
one level up from this file). Results of actually running it: [`REPORT.md`](../REPORT.md).

This guide reflects what actually worked on **Fedora 43**, where the assignment's
assumed Mininet VM (with POX + Python 2.7 pre-installed) isn't available — POX had to
be cloned manually and run via Podman to get the Python 2.7 runtime it needs.

---

## 0. Design summary (for reference)

- `service_ip` (public VIP) is exposed by the switch; real backends are `server_ips`
  (10.0.0.5–8); clients are 10.0.0.1–4. Fake LB MAC: `0A:00:00:00:00:01`.
- State kept in the controller: `servers_mac_to_port`, `client_to_port`,
  `client_to_server` (sticky mapping), all keyed by IP.
- On `ConnectionUp`: broadcast ARP for every server IP.
- On ARP: proxy replies with the LB's fake MAC in both directions; learn client/server
  MAC+port as requests/replies pass through.
- On first IP packet client→service: pick a backend (`update_lb_mapping`), install one
  flow rule each direction (`nw_src`/`nw_dst` + `dl_type=0x0800` match, MAC/IP rewrite
  actions, `idle_timeout=10`, no microflows, default hard timeout untouched).

---

## 1. Environment actually used

| Component | Version | Notes |
|---|---|---|
| OS | Fedora 43 (x86_64) | not the assignment's provided VM |
| Host Python | 3.13.9 | too new for POX `carp` (needs 2.7) |
| Python 2 (native) | not installed | not packaged for Fedora 43 (see Step 3) |
| Podman | 5.8.4 | used to get a Python 2.7 runtime |
| Python inside container | 2.7.18 | via `python:2.7` image |
| Mininet | 2.3.1b4 | already installed on host |
| Open vSwitch | 3.6.2-1.fc43 | already installed on host |
| POX | 0.2.0, branch `carp` | cloned manually, not pre-installed |

**Directory layout used** (POX lives beside the assignment, not at `~/pox`):
```
~/Desktop/7sem/SDN/
├── pox/                              <- cloned here (not ~/pox)
│   ├── pox.py
│   ├── pox/
│   ├── ext/
│   │   └── SimpleLoadBalancer.py     <- copy used at runtime
│   └── ...
└── ASSIGN_01/
    ├── README.md
    ├── REPORT.md
    ├── SimpleLoadBalancer.py         <- source of truth, edit here
    ├── mydocs/
    │   ├── IMPL.md                   <- this file
    │   ├── commands.md
    │   └── commands.txt              <- local scratch, gitignored
    ├── problem statement/
    │   ├── exercise1_problem.pdf
    │   └── exercise1_reading.pdf
    └── screenshots/                  <- terminal screenshots referenced by REPORT.md
```
Adjust every `~/pox` reference below to your actual POX path if it differs
(`~/Desktop/7sem/SDN/pox` in this setup).

---

## Step 1 — Check what's already installed

```bash
mn --version
ovs-vsctl --version
podman --version
python --version
```
**Expected:**
```
2.3.1b4
ovs-vsctl (Open vSwitch) 3.6.2-1.fc43
DB Schema 8.8.0
podman version 5.8.4
Python 3.13.9
```
Mininet, Open vSwitch, and Podman needed **no installation** — only POX and a
Python 2.7 runtime were missing.

---

## Step 2 — Get POX and check out the `carp` branch

`~/pox` did not exist on this system, so POX was cloned next to the assignment folder
instead:
```bash
cd ~/Desktop/7sem/SDN
git clone https://github.com/noxrepo/pox.git
cd pox
git checkout carp
git branch --show-current
```
**Expected:** `carp`

---

## Step 3 — Get a Python 2.7 runtime (via Podman)

POX's `carp` branch requires Python 2.7. Fedora 43 doesn't package it
(`sudo dnf install python2.7` → `No match for argument: python2.7`), so it's pulled as
a container image instead of touching the host's system Python:
```bash
podman pull docker.io/library/python:2.7
podman run --rm python:2.7 python --version
```
**Expected:**
```
Python 2.7.18
```

---

## Step 4 — Install the load balancer app into POX

```bash
cp ~/Desktop/7sem/SDN/ASSIGN_01/SimpleLoadBalancer.py \
   ~/Desktop/7sem/SDN/pox/ext/SimpleLoadBalancer.py
ls -l ~/Desktop/7sem/SDN/pox/ext/SimpleLoadBalancer.py
```
**Expected:** file listed with a recent timestamp, no errors.
The assignment folder's copy stays the source of truth — re-run this `cp` after every
edit.

---

## Step 5 — Start the POX controller (inside the Python 2.7 container)

```bash
cd ~/Desktop/7sem/SDN/pox

podman run --rm -it \
  --network host \
  -v ~/Desktop/7sem/SDN/pox:/pox:Z \
  python:2.7 \
  bash -c 'cd /pox && python ./pox.py log.level --DEBUG SimpleLoadBalancer --ip=10.1.2.3 --servers=10.0.0.5,10.0.0.6,10.0.0.7,10.0.0.8'
```

What each flag does:
| Flag | Purpose |
|---|---|
| `--rm` | remove the container once POX exits |
| `-it` | interactive, so POX logs stream to the terminal |
| `--network host` | share the host's network namespace, so Mininet on the host can reach POX's `127.0.0.1:6633` |
| `-v ...:/pox:Z` | mount the POX checkout into the container (`:Z` for SELinux labeling on Fedora) |
| `python:2.7` | the Python 2.7.18 image |

**Expected output:**
```
POX 0.2.0 (carp) / Copyright 2011-2013 James McCauley, et al.
INFO:SimpleLoadBalancer:Loading Simple Load Balancer module
INFO:SimpleLoadBalancer:LB ready. service=10.1.2.3 servers=[IPAddr('10.0.0.5'), IPAddr('10.0.0.6'), IPAddr('10.0.0.7'), IPAddr('10.0.0.8')]
DEBUG:core:Running on CPython (2.7.18/Apr 20 2020 19:27:10)
INFO:core:POX 0.2.0 (carp) is up.
```
Leave this running — keep this terminal as **Terminal 1**.

---

## Step 6 — Start Mininet with the load-balancer topology

In **Terminal 2**:
```bash
sudo mn --topo single,8 --controller remote,ip=127.0.0.1,port=6633 --mac --switch ovsk
```
(the explicit `ip=127.0.0.1,port=6633` avoids any ambiguity about which controller/port
Mininet targets — plain `--controller remote` defaults to the same address but was less
reliable to reason about when debugging the Podman networking above).

**Expected (Mininet side):**
```
*** Creating network
*** Adding controller
*** Adding hosts:
h1 h2 h3 h4 h5 h6 h7 h8
*** Adding switches:
s1
*** Adding links:
...
*** Starting controller
c0
*** Starting 1 switches
s1 ...
mininet>
```
**Expected (Terminal 1 / POX side), once the switch connects:**
```
INFO:openflow.of_01:[Con 1/1] Connected to 00-00-00-00-00-01
INFO:SimpleLoadBalancer:Switch connected, probing 4 servers
DEBUG:SimpleLoadBalancer:ARP request for 10.0.0.5
DEBUG:SimpleLoadBalancer:ARP request for 10.0.0.6
DEBUG:SimpleLoadBalancer:ARP request for 10.0.0.7
DEBUG:SimpleLoadBalancer:ARP request for 10.0.0.8
INFO:SimpleLoadBalancer:server 10.0.0.5 at 00:00:00:00:00:05 port 5
INFO:SimpleLoadBalancer:server 10.0.0.6 at 00:00:00:00:00:06 port 6
INFO:SimpleLoadBalancer:server 10.0.0.7 at 00:00:00:00:00:07 port 7
INFO:SimpleLoadBalancer:server 10.0.0.8 at 00:00:00:00:00:08 port 8
```
This confirms pre-emptive ARP probing (spec requirement #1) worked — all 4 servers
resolved **before** any client traffic arrives.

> If Mininet is started **before** POX is running, you'll see
> `Unable to contact the remote controller at 127.0.0.1:6653/6633` — the hosts/switch
> still get created, but the switch can't connect until POX is up. Always start POX
> first (Step 5), then Mininet (Step 6).

### Step 6a — Disable IPv6 on the Mininet hosts (recommended)

Mininet hosts generate IPv6 neighbor-discovery traffic that the app doesn't handle
(it only implements ARP + IPv4, per spec) — this shows up as noisy
`Unknown Packet type: 34525` lines in the POX log (`34525` = `0x8710`... actually
EtherType `0x86DD`, IPv6). Silence it from the Mininet CLI:
```bash
mininet> py [h.cmd('sysctl -w net.ipv6.conf.all.disable_ipv6=1') for h in net.hosts]
mininet> py [h.cmd('sysctl -w net.ipv6.conf.default.disable_ipv6=1') for h in net.hosts]
```
**Expected:** `net.ipv6.conf.all.disable_ipv6 = 1` and
`net.ipv6.conf.default.disable_ipv6 = 1` printed once per host (8 lines each).
This is cosmetic only — the load balancer's IPv4/ARP handling was already correct
without it — but it makes the POX log much easier to read while testing.

---

## Step 7 — Test: client ping to the service IP

```bash
mininet> h1 ping -c4 10.1.2.3
```
**Expected (Mininet side):**
```
PING 10.1.2.3 (10.1.2.3) 56(84) bytes of data.
64 bytes from 10.1.2.3: icmp_seq=1 ttl=64 time=X ms
64 bytes from 10.1.2.3: icmp_seq=2 ttl=64 time=X ms
64 bytes from 10.1.2.3: icmp_seq=3 ttl=64 time=X ms
64 bytes from 10.1.2.3: icmp_seq=4 ttl=64 time=X ms

--- 10.1.2.3 ping statistics ---
4 packets transmitted, 4 received, 0% packet loss
```
**Expected (POX side):**
```
INFO:SimpleLoadBalancer:client 10.0.0.1 at 00:00:00:00:00:01 port 1
INFO:SimpleLoadBalancer:mapping: 10.0.0.1 -> 10.0.0.6      # server chosen at random
DEBUG:SimpleLoadBalancer:flow c2s: 10.0.0.1->10.1.2.3 ==> 10.0.0.6 (port 6)
DEBUG:SimpleLoadBalancer:flow s2c: 10.0.0.6->10.0.0.1 ==> 10.1.2.3 (port 1)
```

Or, as a quick smoke test across all hosts (client↔client and client↔server-that's-not-
the-VIP pairs are expected to fail — see Step 10):
```bash
mininet> pingall
```
Note: `pingall` itself will show heavy loss (only the 4 client→VIP paths are ever
handled — all other pairs are intentionally out of scope, see §0). Use a direct
`h<n> ping <vip>` instead to test the actual load-balancing path.

> **First-packet loss is expected.** The very first packet on a new client→VIP flow
> triggers ARP discovery → client identification → server selection →
> client-to-server flow install → server-to-client flow install, all inside the
> controller round-trip; only packets after that ride the installed flow. A short
> `ping -c3`/`-c5` will typically show ~20–33% loss (1 of 3–5 lost); a longer
> `ping -c20` settles to ~5% (1 of 20), confirming it's a one-time setup cost, not an
> ongoing problem. See §"Verified test run" below for real numbers.

---

## Step 8 — Test: load balancing across multiple clients

```bash
mininet> h2 ping -c4 10.1.2.3
mininet> h3 ping -c4 10.1.2.3
mininet> h4 ping -c4 10.1.2.3
```
**Expected:** each ping succeeds (0% loss); POX logs one `mapping: <client> -> <server>`
line per new client, e.g.:
```
INFO:SimpleLoadBalancer:mapping: 10.0.0.2 -> 10.0.0.5
INFO:SimpleLoadBalancer:mapping: 10.0.0.3 -> 10.0.0.8
INFO:SimpleLoadBalancer:mapping: 10.0.0.4 -> 10.0.0.6
```
Selection is random — repeat over a few runs to confirm more than one server gets
picked, demonstrating actual load distribution.

---

## Step 9 — Test: inspect installed flow rules

```bash
sudo ovs-ofctl dump-flows s1
```
**Expected (abridged):**
```
cookie=0, duration=..., idle_timeout=10, priority=..., ip,nw_src=10.0.0.1,nw_dst=10.1.2.3 \
  actions=mod_dl_dst:00:00:00:00:00:06,mod_nw_dst:10.0.0.6,mod_dl_src:0a:00:00:00:00:01,output:6
cookie=0, duration=..., idle_timeout=10, priority=..., ip,nw_src=10.0.0.6,nw_dst=10.0.0.1 \
  actions=mod_nw_src:10.1.2.3,mod_dl_src:0a:00:00:00:00:01,mod_dl_dst:00:00:00:00:00:01,output:1
```
Confirms: matches are IP-only (no exact microflow match on ports/macs),
`idle_timeout=10`, and the rewrite actions match §0's design.

Wait >10s without traffic, rerun the same command — **expected:** both entries are
gone (idle-timeout expiry confirmed).

---

## Step 10 — Test: verify transparency with tcpdump

```bash
mininet> xterm h5
```
In the h5 xterm (use whichever server got picked in Step 7):
```bash
tcpdump -XX -n -i h5-eth0
```
Back in the Mininet CLI:
```bash
mininet> h1 ping -c2 10.1.2.3
```
**Expected in h5's tcpdump:**
- Echo request arrives with **source IP `10.0.0.1`** (client's real IP, untouched),
  **destination IP `10.0.0.6`** (h5's own real IP, rewritten from the service IP), and
  **source MAC `0a:00:00:00:00:01`** (LB's fake MAC, not h1's real MAC).
- Echo reply goes back with h5's own source IP/MAC and destination `10.0.0.1`/LB fake
  MAC — rewritten again in transit so h1 sees it as coming from `10.1.2.3`.

This confirms the balancing is **transparent**: h5 never sees `10.1.2.3`, h1 never sees
`10.0.0.6`.

---

## Step 11 — Negative test (sanity check on unhandled traffic)

```bash
mininet> h1 ping -c2 10.0.0.2
```
**Expected:** fails/times out (100% loss) — client-to-client forwarding is explicitly
out of scope per the spec, and the app correctly does not flood or forward it.
```bash
mininet> h1 ping -c2 10.0.0.10
```
**Expected:** also fails (non-existent host) — same as the hub/L2-learning baseline in
the tutorial (three unanswered ARPs is normal here, not a bug).

---

## Step 12 — Cleanup

```bash
mininet> exit
sudo mn -c
```
In Terminal 1: `Ctrl-C` to stop POX (the `--rm` flag removes the container
automatically).

---

## Troubleshooting quick-reference

| Symptom | Cause | Fix / check |
|---|---|---|
| `cd: no such file or directory: /home/<user>/pox` | assignment docs assume `~/pox`, but POX wasn't installed there | clone POX to wherever's convenient (e.g. `~/Desktop/7sem/SDN/pox`) and use that path consistently in every command |
| `ModuleNotFoundError: No module named 'recoco'` when running `./pox.py` directly | running POX `carp` under the host's Python 3.13 instead of Python 2.7 | run POX inside the `python:2.7` Podman container (Step 5), not with the host `python`/`python3` |
| `SyntaxWarning: "is not" with 'str' literal` | same root cause as above — old POX code parsed by Python 3 | same fix as above |
| `sudo dnf install python2.7` → `No match for argument: python2.7` | Fedora 43 doesn't package Python 2.7 | don't install it on the host — use Podman (Step 3) instead |
| `find ~ -type f -name "pox.py"` / `sudo find / ...` return nothing | POX genuinely not installed anywhere yet | clone it (Step 2) |
| Mininet starts but prints `Unable to contact the remote controller at 127.0.0.1:6653/6633` | POX wasn't running yet when Mininet started | start POX (Step 5) **before** Mininet (Step 6); topology/hosts still get created either way, they just can't reach the controller until it's up |
| POX crashes right after `Connected to ...` | `ofp_action_dl_addr`/`ofp_action_nw_addr` API mismatch on your POX version | inside the container: `python -c "import pox.openflow.libopenflow_01 as of; print of.ofp_action_dl_addr.set_dst"` |
| `h1 ping 10.1.2.3` gets no reply at all | ARP never resolved — check Step 6 log for all 4 `server ... at ...` lines | re-check `--servers` matches the actual host IPs |
| Ping stalls after ~10s of silence, then works again | expected — idle timeout expired, next packet re-triggers PacketIn + flow reinstall | not a bug |
| Different clients keep landing on the same server every run | small sample size + `random.choice` | re-run a few times; check `mapping:` log lines across runs |
| `INFO:SimpleLoadBalancer:Unknown Packet type: 34525` spamming the log | IPv6 neighbor-discovery traffic from Mininet hosts (EtherType `0x86DD`); the app only handles ARP + IPv4 per spec | cosmetic only — disable IPv6 on the hosts (Step 6a) if it's cluttering your log |
| `h1 ping -c1 10.0.0.5` (a real backend IP) fails with `Destination Host Unreachable` | expected — clients should only ever address the virtual service IP (`10.1.2.3`); the backend IPs are intentionally not directly reachable from clients | not a bug — this is the same out-of-scope behavior as Step 11's negative test |
| `pingall` shows mostly "X" / heavy loss | `pingall` tries every host pair; only client→VIP (`10.1.2.3`) traffic is handled | expected — use `h<n> ping 10.1.2.3` to test the real path, not `pingall` |

---

## Final architecture (as actually run)

```
                    Fedora 43 Host
                         │
          ┌──────────────┴──────────────┐
          │                              │
       Mininet                    Podman Container
          │                              │
      Open vSwitch                 Python 2.7.18
          │                              │
       s1 ◄──────── OpenFlow ───────► POX carp
      / | \                             │
    h1 h2 ... h8                 SimpleLoadBalancer
```
POX reaches the host's OpenFlow listener because the container runs with
`--network host`.

---

## Key lesson

The blocker was never the load balancer logic — it was a **Python version mismatch**:
POX `carp` requires Python 2.7, but Fedora 43 ships Python 3.13 and no longer packages
2.7. Rather than patching the OS, Podman supplied an isolated Python 2.7.18 runtime
(`python:2.7` image) to run the unmodified POX `carp` branch, leaving the host's system
Python untouched:

```
Fedora 43 → Python 3.13 (host, unchanged)
Fedora 43 → Podman → python:2.7 → Python 2.7.18 → POX 0.2.0 (carp) → SimpleLoadBalancer
                                                                          ↕ OpenFlow
                                                        Mininet + Open vSwitch (host)
```

---

## Quick command reference

```bash
# one-time setup
cd ~/Desktop/7sem/SDN && git clone https://github.com/noxrepo/pox.git
cd pox && git checkout carp
podman pull docker.io/library/python:2.7

# after every edit to SimpleLoadBalancer.py
cp ~/Desktop/7sem/SDN/ASSIGN_01/SimpleLoadBalancer.py \
   ~/Desktop/7sem/SDN/pox/ext/SimpleLoadBalancer.py

# Terminal 1 — controller
cd ~/Desktop/7sem/SDN/pox
podman run --rm -it --network host \
  -v ~/Desktop/7sem/SDN/pox:/pox:Z python:2.7 \
  bash -c 'cd /pox && python ./pox.py log.level --DEBUG SimpleLoadBalancer --ip=10.1.2.3 --servers=10.0.0.5,10.0.0.6,10.0.0.7,10.0.0.8'

# Terminal 2 — network
cd ~/Desktop/7sem/SDN/ASSIGN_01
sudo mn --topo single,8 --controller remote,ip=127.0.0.1,port=6633 --mac --switch ovsk
# then, inside mininet> (optional but recommended, quiets IPv6 noise in the POX log)
py [h.cmd('sysctl -w net.ipv6.conf.all.disable_ipv6=1') for h in net.hosts]
py [h.cmd('sysctl -w net.ipv6.conf.default.disable_ipv6=1') for h in net.hosts]
h1 ping -c20 10.1.2.3
h2 ping -c20 10.1.2.3

# cleanup
sudo mn -c
```

---

## Part 2 — Verified test run (actual results)

This section records a real end-to-end run that confirmed the load balancer works, with
the actual numbers observed (not just expected/theoretical output as in Steps 1–12).

### Startup — confirmed working

POX (Step 5) came up cleanly:
```
POX 0.2.0 (carp) / Copyright 2011-2013 James McCauley, et al.
INFO:SimpleLoadBalancer:Loading Simple Load Balancer module
INFO:SimpleLoadBalancer:LB ready. service=10.1.2.3 servers=[...]
DEBUG:core:POX 0.2.0 (carp) going up...
DEBUG:core:Running on CPython (2.7.18/Apr 20 2020 19:27:10)
DEBUG:core:Platform is Linux-7.1.8-100.fc43.x86_64-x86_64-with-debian-10.3
INFO:core:POX 0.2.0 (carp) is up.
DEBUG:openflow.of_01:Listening on 0.0.0.0:6633
```
Mininet (Step 6, using the explicit `remote,ip=127.0.0.1,port=6633` form) connected
successfully:
```
INFO:openflow.of_01:[00-00-00-00-00-01 1] connected
INFO:SimpleLoadBalancer:Switch connected, probing 4 servers
DEBUG:SimpleLoadBalancer:ARP request for 10.0.0.5
DEBUG:SimpleLoadBalancer:ARP request for 10.0.0.6
DEBUG:SimpleLoadBalancer:ARP request for 10.0.0.7
DEBUG:SimpleLoadBalancer:ARP request for 10.0.0.8
INFO:SimpleLoadBalancer:server 10.0.0.5 at 00:00:00:00:00:05 port 5
INFO:SimpleLoadBalancer:server 10.0.0.6 at 00:00:00:00:00:06 port 6
INFO:SimpleLoadBalancer:server 10.0.0.7 at 00:00:00:00:00:07 port 7
INFO:SimpleLoadBalancer:server 10.0.0.8 at 00:00:00:00:00:08 port 8
```
All 4 backend servers discovered before any client traffic — confirms the pre-emptive
ARP probing requirement.

### `pingall` baseline (expected heavy loss)

```
mininet> pingall
*** Results: 100% dropped (0/56 received)
```
Expected — `pingall` exercises every host pair, and only client→VIP (`10.1.2.3`)
traffic is ever handled by this app (§0 / Step 11). Not a failure of the load balancer.

### Virtual IP reachability

```
mininet> h1 ping -c 3 10.1.2.3
3 packets transmitted, 2 received, 33.3333% packet loss
```
The 2 successful replies confirmed the VIP is reachable; the one lost packet is the
first-packet ARP/flow-setup cost described in Step 7.

### Client-to-server mapping & flow installation — confirmed

```
INFO:SimpleLoadBalancer:client 10.0.0.1 at 00:00:00:00:00:01 port 1
INFO:SimpleLoadBalancer:mapping: 10.0.0.1 -> 10.0.0.8
DEBUG:SimpleLoadBalancer:flow c2s: 10.0.0.1->10.1.2.3 ==> 10.0.0.8 (port 8)
DEBUG:SimpleLoadBalancer:flow s2c: 10.0.0.8->10.0.0.1 ==> 10.1.2.3 (port 1)
```
This is the core proof the load balancer is working: client `10.0.0.1` was
transparently mapped to real backend `10.0.0.8` while continuing to address the
virtual IP `10.1.2.3`. The ARP reply the client actually saw:
```
DEBUG:SimpleLoadBalancer:ARP reply: 10.1.2.3 is-at 0a:00:00:00:00:01 -> 10.0.0.1
```
confirms the client only ever learns the fake LB MAC for the VIP, never a real
backend's identity.

### Multiple clients — load spread across backends

```
10.0.0.1 -> 10.0.0.8
10.0.0.2 -> 10.0.0.5
10.0.0.3 -> 10.0.0.5
10.0.0.4 -> 10.0.0.8
```
with matching reverse flows installed for each, e.g.:
```
DEBUG:SimpleLoadBalancer:flow s2c: 10.0.0.5->10.0.0.2 ==> 10.1.2.3 (port 2)
DEBUG:SimpleLoadBalancer:flow s2c: 10.0.0.5->10.0.0.3 ==> 10.1.2.3 (port 3)
```
(In this particular run, backends `10.0.0.6`/`10.0.0.7` weren't picked — expected with
only 4 clients and `random.choice` across 4 servers; re-running spreads it further.)

### Per-host ping results (`-c5`, short run)

| Host | Result | Avg RTT |
|---|---|---|
| h1 | 5 sent, 4 received, 20% loss | 0.125 ms |
| h2 | 5 sent, 4 received, 20% loss | 0.133 ms |
| h3 | 5 sent, 4 received, 20% loss | 0.098 ms |
| h4 | 5 sent, 4 received, 20% loss | 0.099 ms |

In every case only `icmp_seq=1` was lost — consistent with the first-packet
controller-round-trip cost, not a functional problem.

### Direct backend access — confirmed correctly blocked

```
mininet> h1 ping -c 1 10.0.0.5
From 10.0.0.1 icmp_seq=1 Destination Host Unreachable
1 packets transmitted, 0 received, 100% packet loss
```
Correct: clients should only ever reach the service via `10.1.2.3`; the app never
installs a rule that makes a backend directly reachable from a client, by design.

### Longer stability runs (`-c20`)

| Host | Result | Avg RTT | Min RTT | Max RTT |
|---|---|---|---|---|
| h1 | 20 sent, 19 received, 5% loss | 0.071 ms | 0.038 ms | 0.378 ms |
| h2 | 20 sent, 19 received, 5% loss | 0.074 ms | 0.040 ms | 0.375 ms |

Only `icmp_seq=1` lost in both cases; `icmp_seq=2` onward all succeeded — loss rate
drops from ~20% (5-packet run) to ~5% (20-packet run) simply because the one-time
setup cost is amortized over more packets, confirming it's fixed overhead, not a
recurring drop:
```
ARP discovery → client identification → server selection →
c2s flow install → s2c flow install → (all further packets use installed flows)
```

### Final results

| Check | Result |
|---|---|
| POX starts (Python 2.7.18 in container) | ✅ |
| `SimpleLoadBalancer` module loads | ✅ |
| POX listens on 6633 | ✅ |
| Mininet switch connects to POX | ✅ |
| All 4 servers discovered via pre-emptive ARP | ✅ |
| Virtual IP `10.1.2.3` reachable from clients | ✅ |
| Client→server mapping generated (`update_lb_mapping`) | ✅ |
| c2s flow installed | ✅ |
| s2c flow installed | ✅ |
| h1–h4 → VIP all succeed | ✅ |
| h1 20-ping stability | 19/20 (95%) |
| h2 20-ping stability | 19/20 (95%) |

**Conclusion:** the `SimpleLoadBalancer` POX controller works end-to-end on this setup.
The decisive evidence is the `Switch connected, probing 4 servers` → all 4 servers
discovered → `mapping: 10.0.0.1 -> 10.0.0.8` → `flow c2s: ...` / `flow s2c: ...`
sequence, plus sustained ~95% ping delivery once flows are installed (the remaining
~5% is the one-time first-packet controller round-trip, not an ongoing defect).
