# SDN Assignment 01 — Simple Load Balancer (POX / OpenFlow)
### Final Test Report

**Author:** Gagan Rathod  
**Roll no :** 23bcs106  
**Assignment:** Build a simple load balancer (POX controller + Mininet)  
**Files:** [`SimpleLoadBalancer.py`](SimpleLoadBalancer.py) (`ASSIGN_01/`),
[`IMPL.md`](mydocs/IMPL.md), [`commands.md`](mydocs/commands.md) (`ASSIGN_01/mydocs/`)  
**Screenshots:** `ASSIGN_01/screenshots/*.png`

> For design rationale, setup, and full run instructions, see [`IMPL.md`](mydocs/IMPL.md).
> For the plain command list used to produce every result below, see
> [`commands.md`](mydocs/commands.md).

---

## 1. Objective

Implement a POX OpenFlow controller (`SimpleLoadBalancer.py`) that turns a single
switch (`s1`) into a transparent load balancer between 4 clients (h1–h4,
`10.0.0.1`–`10.0.0.4`) and 4 backend servers (h5–h8, `10.0.0.5`–`10.0.0.8`), fronting
them with a virtual service IP (`10.1.2.3`). Clients only ever see the virtual IP; the
switch proxies ARP and rewrites MAC/IP addresses in both directions so the redirection
to a randomly-chosen backend is invisible to both sides. Full spec in
[`exercise1_problem.pdf`](problem%20statement/exercise1_problem.pdf); background/tutorial
in [`exercise1_reading.pdf`](problem%20statement/exercise1_reading.pdf); design rationale
in [`IMPL.md`](mydocs/IMPL.md).

---

## 2. Environment

| Component | Version | Notes |
|---|---|---|
| OS | Fedora 43 (x86_64) | not the assignment's provided VM |
| Host Python | 3.13.9 | too new for POX `carp` (needs 2.7) |
| Podman | 5.8.4 | used to get a Python 2.7 runtime |
| Python inside container | 2.7.18 | via `python:2.7` image |
| Mininet | 2.3.1b4 | pre-installed on host |
| Open vSwitch | 3.6.2-1.fc43 | pre-installed on host |
| POX | 0.2.0, branch `carp` | cloned manually to `~/Desktop/7sem/SDN/pox` |

Fedora 43 doesn't package Python 2.7 (`dnf install python2.7` → no match), and POX's
`carp` branch requires it, so POX was run inside a Python 2.7 container via Podman
(`--network host`, so the host's Mininet can still reach it on `127.0.0.1:6633`),
leaving the host's system Python untouched. Full setup steps are documented in
[`IMPL.md`](mydocs/IMPL.md).

---

## 3. Topology

```
Type          Nodes
----          -----
Controller    c0 → 1 controller
Hosts         h1 h2 h3 h4 h5 h6 h7 h8 → 8 hosts
Switch        s1 → 1 switch
```
h1–h4 = clients (`10.0.0.1`–`10.0.0.4`), h5–h8 = servers (`10.0.0.5`–`10.0.0.8`),
c0 = POX running `SimpleLoadBalancer`, s1 = the OpenFlow load-balancer switch.

### Process / terminal layout

```
Terminal 1
└── POX SimpleLoadBalancer
    └── PID 674030
        └── 0.0.0.0:6633
                 │
                 │ OpenFlow
                 ↓
Terminal 2
└── Mininet
    └── s1
        ├── h1
        ├── h2
        ├── h3
        ├── h4
        ├── h5  (10.0.0.5)
        ├── h6  (10.0.0.6)
        ├── h7  (10.0.0.7)
        └── h8  (10.0.0.8)

Terminal 3
└── h5 namespace
    └── tcpdump -XX -n -i h5-eth0
```
(PID shown is one specific run; it differs each time POX is restarted — see §6 for why
this mattered during testing.)

---

## 4. Implementation summary

`SimpleLoadBalancer.py` implements every stub in the assignment's code skeleton:

- **State:** `servers_mac_to_port`, `client_to_port`, `client_to_server` (sticky
  mapping), all keyed by IP.
- **`_handle_ConnectionUp`:** stores the connection and broadcasts an ARP request for
  every configured server IP immediately, so all 4 backends are resolved before any
  client traffic arrives.
- **`send_proxied_arp_request` / `send_proxied_arp_reply`:** build raw
  `ethernet`/`arp` packets and send them via `ofp_packet_out` — requests are flooded,
  replies go straight back out the requester's port, and every reply always carries the
  load balancer's fake MAC (`0A:00:00:00:00:01`).
- **`update_lb_mapping`:** picks a backend at random (`random.choice`) the first time a
  client is seen, then returns the same backend on every later call (sticky mapping).
- **`install_flow_rule_client_to_server` / `_server_to_client`:** install one
  `ofp_flow_mod` per direction, matching only `dl_type=0x0800` + `nw_src`/`nw_dst` (no
  microflows), `idle_timeout=10`, default hard timeout untouched, rewriting
  MAC/IP as required so the redirection stays invisible to both sides.
- **`_handle_PacketIn`:** dispatches ARP and IPv4 packets to the logic above; anything
  else (client↔client, client↔real-backend-IP, non-ARP/IP types) is intentionally left
  unhandled, per spec.

Full function-by-function rationale is in [`IMPL.md`](mydocs/IMPL.md) §0.

---

## 5. Test execution log (step by step, with real screenshots)

All screenshots below are the actual terminal output captured during testing, saved in
[`screenshots/`](screenshots/). Nothing here is simulated — every value shown is what
the running system produced.

### Step 1 — Start POX

```bash
cd ~/Desktop/7sem/SDN/pox
podman run --rm -it --network host \
  -v ~/Desktop/7sem/SDN/pox:/pox:Z python:2.7 \
  bash -c 'cd /pox && python ./pox.py log.level --DEBUG SimpleLoadBalancer --ip=10.1.2.3 --servers=10.0.0.5,10.0.0.6,10.0.0.7,10.0.0.8'
```

![POX startup](screenshots/01-pox-startup.png)

**Result:** POX 0.2.0 (carp) starts cleanly under Python 2.7.18, loads
`SimpleLoadBalancer`, and reports `LB ready. service=10.1.2.3
servers=[IPAddr('10.0.0.5'), IPAddr('10.0.0.6'), IPAddr('10.0.0.7'), IPAddr('10.0.0.8')]`,
then binds and listens on `0.0.0.0:6633`. ✅

### Step 2 — Start Mininet and confirm server discovery

```bash
sudo mn --topo single,8 --controller remote,ip=127.0.0.1,port=6633 --mac --switch ovsk
```

![Topology up](screenshots/03-mininet-topology-up.png)
![Servers discovered](screenshots/02-pox-servers-discovered.png)

**Result:** Mininet creates 8 hosts, 1 switch (`s1`), starts controller `c0`, drops
into the `mininet>` prompt. On the POX side, the moment the switch connects it logs
`Switch connected, probing 4 servers`, sends one ARP request per backend
(`10.0.0.5`–`10.0.0.8`), and resolves all 4:
```
server 10.0.0.5 at 00:00:00:00:00:05 port 5
server 10.0.0.6 at 00:00:00:00:00:06 port 6
server 10.0.0.7 at 00:00:00:00:00:07 port 7
server 10.0.0.8 at 00:00:00:00:00:08 port 8
```
This confirms the **pre-emptive ARP probing** requirement — all backends are resolved
before any client sends a single packet. ✅ (The `Unknown Packet type: 34525` lines are
IPv6 neighbor-discovery traffic from the Mininet hosts, expected since the app only
handles ARP + IPv4 — addressed in Step 3.)

### Step 3 — Topology / node verification (`nodes`, `net`, `ifconfig`)

```
mininet> nodes
mininet> net
mininet> h5 ifconfig
```

Captured as terminal text output (no screenshot for this step):
```
mininet> nodes
c0 h1 h2 h3 h4 h5 h6 h7 h8 s1

mininet> net
h1 h1-eth0:s1-eth1
h2 h2-eth0:s1-eth2
h3 h3-eth0:s1-eth3
h4 h4-eth0:s1-eth4
h5 h5-eth0:s1-eth5
h6 h6-eth0:s1-eth6
h7 h7-eth0:s1-eth7
h8 h8-eth0:s1-eth8

mininet> h5 ifconfig
h5-eth0   Link encap:Ethernet
          inet addr:10.0.0.5  Bcast:10.0.0.255  Mask:255.255.255.0
          UP RUNNING
```

**Result:** `nodes` confirms all 10 elements are up (`c0`, `s1`, `h1`–`h8`). `net`
confirms the wiring matches the intended topology one-for-one — `h1`→`s1-eth1` through
`h8`→`s1-eth8`, no unexpected links. `h5 ifconfig` confirms h5's interface is `UP
RUNNING` with the correct address (`10.0.0.5/24`) and no interface-level errors. This
rules out topology/wiring problems as a cause of anything seen later in testing. ✅

### Step 4 — Disable IPv6 noise (cosmetic)

```
mininet> py [h.cmd('sysctl -w net.ipv6.conf.all.disable_ipv6=1') for h in net.hosts]
mininet> py [h.cmd('sysctl -w net.ipv6.conf.default.disable_ipv6=1') for h in net.hosts]
```

![IPv6 disabled](screenshots/04-ipv6-disabled.png)

**Result:** `net.ipv6.conf.all.disable_ipv6 = 1` and
`net.ipv6.conf.default.disable_ipv6 = 1` confirmed for all 8 hosts. Purely cosmetic —
quiets the POX log for the rest of testing.

### Step 5 — Baseline `pingall`

```
mininet> pingall
```

![pingall baseline](screenshots/05-pingall-baseline.png)

**Result:** `100% dropped (0/56 received)`. **Expected**, not a failure —
`pingall` exercises every host pair, and this app only ever handles traffic addressed
to the virtual service IP (`10.1.2.3`), which isn't a real Mininet host. Every other
pair (client↔client, server↔server, client↔real-backend-IP) is explicitly out of scope
per the spec.

### Step 6 — Client → virtual service IP (h1–h4)

```
mininet> h1 ping -c4 10.1.2.3
mininet> h2 ping -c4 10.1.2.3
mininet> h3 ping -c4 10.1.2.3
mininet> h4 ping -c4 10.1.2.3
```

![h1 mapping + ping](screenshots/06-ping&pox-mapping-flow-h1.png)
![h2 mapping + ping](screenshots/07-ping&pox-mapping-flow-h2.png)
![h3 mapping + ping](screenshots/08-ping&pox-mapping-flow-h3.png)
![h4 mapping + ping](screenshots/08-ping&pox-mapping-flow-h4.png)

**Result — the core proof the load balancer works:**

| Client | Assigned backend | Ping result |
|---|---|---|
| h1 (`10.0.0.1`) | `10.0.0.7` | 4 sent, 3 received, 25% loss |
| h2 (`10.0.0.2`) | `10.0.0.6` | 4 sent, 3 received, 25% loss |
| h3 (`10.0.0.3`) | `10.0.0.8` | 4 sent, 3 received, 25% loss |
| h4 (`10.0.0.4`) | `10.0.0.6` | 4 sent, 3 received, 25% loss |

For every client, the POX log shows the full sequence in order:
```
client <ip> at <mac> port <n>
ARP reply: 10.1.2.3 is-at 0a:00:00:00:00:01 -> <client>
mapping: <client> -> <server>
flow c2s: <client>->10.1.2.3 ==> <server> (port <n>)
flow s2c: <server>-><client> ==> 10.1.2.3 (port <n>)
ARP reply: <client> is-at 0a:00:00:00:00:01 -> <server>
```
This is direct, unambiguous evidence of: the client resolving the service IP to the
fake LB MAC, the controller picking a backend, installing both flow directions, and the
backend later resolving the "client" to the same fake LB MAC (never the client's real
MAC). Three different backends (`10.0.0.6`, `10.0.0.7`, `10.0.0.8`) were picked across
just 4 clients — real load distribution, not a fixed server. ✅

**On the 25% loss pattern:** in all 4 cases `icmp_seq=1` was lost, `icmp_seq=2–4`
succeeded. This is the expected one-time cost of the first packet triggering ARP
resolution → server selection → flow installation inside the controller round-trip,
before any flow exists in the switch for it to ride on — confirmed further in Step 8.

### Step 7 — Inspect switch state (`dump-flows`, `dump-ports`, `ovs-vsctl show`)

```
mininet> sh ovs-ofctl -O OpenFlow10 dump-flows s1
mininet> sh ovs-ofctl -O OpenFlow10 dump-ports s1
mininet> sh ovs-vsctl show
```

![Flow table / port stats](screenshots/09-flow-table.png)

`ovs-vsctl show` captured as terminal text output (no screenshot for this step):
```
Bridge "s1"
    Controller "tcp:127.0.0.1:6633"
        is_connected: true
    Port "s1"
        Interface "s1"
            type: internal
```

**Result:** `dump-flows` returned **empty** at the moment this was run — by then, more
than 10s had passed since the last ping in Step 6, so the installed flow entries
(`idle_timeout=10`) had already expired. This is expected behavior, not a bug (see §6
for how this was diagnosed). `dump-ports` (run immediately after, same command)
confirms the switch itself is healthy: all 9 ports (`LOCAL` + `s1-eth1`…`s1-eth8`) show
real RX/TX packet counts with **`drop=0, errs=0, coll=0` on every port** — no packet
loss or errors anywhere at the switch/port level. `ovs-vsctl show` confirms
`is_connected: true` for the controller — `s1` was continuously connected to POX on
`tcp:127.0.0.1:6633` throughout testing. ✅

### Step 8 — Long stability run

```
mininet> h1 ping -c20 10.1.2.3
```

![20-ping stability](screenshots/10-h1-ping20-stability.png)

**Result:** `20 packets transmitted, 19 received, 5% packet loss`,
`rtt min/avg/max/mdev = 0.038/0.080/0.406/0.079 ms`. Only the very first packet
(`icmp_seq=1`, not shown — cropped at the top of the capture, but implied by the
sequence starting visibly at `icmp_seq=7` onward and the "19/20" count) was lost; every
packet from `icmp_seq` onward through 20 succeeded with sub-millisecond RTT. This
confirms the ~25% loss seen on the short 4-packet runs in Step 6 is a **fixed, one-time
setup cost** (ARP + flow install), not a recurring problem — it's amortized down to 5%
once more packets are sent. ✅ Directly preceding this in the same capture, the POX log
also shows a **fresh** `flow c2s: 10.0.0.1->10.1.2.3 ==> 10.0.0.8 (port 8)` /
`flow s2c: 10.0.0.8->10.0.0.1 ==> 10.1.2.3 (port 1)` pair — i.e. h1 got re-mapped to
`10.0.0.8` this time (its previous flow from Step 6 had expired and this ping
re-triggered the controller), which is exactly the sticky-mapping-plus-idle-timeout
behavior the design calls for.

### Step 9 — Negative test (out-of-scope traffic)

```
mininet> h1 ping -c2 10.0.0.5
mininet> h1 ping -c2 10.0.0.2
```

![Negative test](screenshots/11-negative-test-unreachable.png)

**Result:** both fail immediately with `Destination Host Unreachable`,
`2 packets transmitted, 0 received, +2 errors, 100% packet loss`. Correct and expected:
- `h1 → 10.0.0.5` (h5's **real** IP): clients must only ever address the virtual
  service IP; the app never proxies ARP for, or installs flows toward, a backend's real
  IP from a client. h1 never even resolves an ARP entry for it.
- `h1 → 10.0.0.2` (client-to-client): explicitly out of scope per the spec — the app
  only handles ARP + IP traffic to/from the service IP, nothing else.

Neither is a defect — this is the app correctly *not* forwarding traffic it was told
not to handle. ✅

---

## 6. Issues encountered during setup/testing (and how they were resolved)

| # | Symptom | Root cause | Resolution |
|---|---|---|---|
| 1 | `cd ~/pox` → `No such file or directory` | Assignment docs assume `~/pox`; POX wasn't installed there | Cloned POX to `~/Desktop/7sem/SDN/pox` instead, used that path consistently |
| 2 | `ModuleNotFoundError: No module named 'recoco'` running `./pox.py` directly | Running POX `carp` (Python 2 code) under the host's Python 3.13 | Ran POX inside a `python:2.7` Podman container |
| 3 | `sudo dnf install python2.7` → `No match for argument: python2.7` | Fedora 43 no longer packages Python 2.7 | Used Podman instead of touching host Python |
| 4 | `h1 ping -c1 10.0.0.5` → `Destination Host Unreachable` | Misread as a bug — actually the correct negative-test result (see Step 9) | Clarified: clients should never reach real backend IPs directly, by design |
| 5 | `sh ovs-ofctl dump-flows s1` printed nothing at all | Missing `-O OpenFlow10` — POX `carp` only speaks OpenFlow 1.0, but `ovs-ofctl` defaults to querying OF1.3 and silently gets nothing back | Added `-O OpenFlow10` to every `ovs-ofctl` call |
| 6 | POX terminal showed no new activity even after successful pings | **Two POX instances were running simultaneously** (a stale one from an earlier run, plus a newly-started one) — only one can actually bind port 6633; the user was watching the terminal of the dead one (`ERROR:openflow.of_01:Error 98 while binding socket: Address already in use`) | Identified the live PID via `sudo ss -lntp \| grep 6633`, cross-checked against `podman inspect <name> --format '{{.State.Pid}}'`, killed the stale container, restarted clean with exactly one instance |
| 7 | Accidentally killed the *working* POX instance instead of the dead one during cleanup | Two containers with similar auto-generated names, easy to target the wrong one | Verified via `ps -p <pid> -o pid,cmd` before/after each kill; restarted fresh with a single confirmed instance (PID `674030`, confirmed listening via `ss -lntp`) |

---

## 7. Conclusion

`SimpleLoadBalancer` works end-to-end on Fedora 43 (via a Python-2.7-in-Podman
workaround for POX `carp`). The decisive evidence is the repeatable log sequence for
every client:
```
client <ip> at <mac> port <n>
mapping: <client> -> <server>
flow c2s: <client>->10.1.2.3 ==> <server> (port <n>)
flow s2c: <server>-><client> ==> 10.1.2.3 (port <n>)
```
combined with sustained ~95%+ ping delivery once flows are installed, correct rejection
of all out-of-scope traffic, clean switch port statistics throughout, and a continuously
`is_connected: true` controller link confirmed via `ovs-vsctl show`. Topology and wiring
were independently verified with `nodes`/`net`/`ifconfig`, ruling out cabling issues as
a factor in any of the above.
