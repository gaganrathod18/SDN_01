# Commands — SDN Assignment 01 (Simple Load Balancer)

All commands needed to run every test, in order. Three terminals: **Terminal 1** = POX,
**Terminal 2** = Mininet, **Terminal 3** = tcpdump inside h5's namespace (only needed
for the transparency check).

For narrative/explanation see [`IMPL.md`](IMPL.md); for actual results see
[`REPORT.md`](../REPORT.md).

## Topology

```
Type          Nodes
----          -----
Controller    c0 → 1 controller
Hosts         h1 h2 h3 h4 h5 h6 h7 h8 → 8 hosts
Switch        s1 → 1 switch
```
h1–h4 = clients (`10.0.0.1`–`10.0.0.4`), h5–h8 = servers (`10.0.0.5`–`10.0.0.8`),
c0 = POX running `SimpleLoadBalancer`, s1 = the OpenFlow load-balancer switch.

## Process / terminal layout

```
Terminal 1
└── POX SimpleLoadBalancer
    └── PID <check with `ss -lntp | grep 6633`>
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

---

## Terminal 1 — POX

```bash
cd ~/Desktop/7sem/SDN/pox

podman run --rm -it \
  --network host \
  -v ~/Desktop/7sem/SDN/pox:/pox:Z \
  python:2.7 \
  bash -c 'cd /pox && python ./pox.py log.level --DEBUG SimpleLoadBalancer --ip=10.1.2.3 --servers=10.0.0.5,10.0.0.6,10.0.0.7,10.0.0.8'
```
Leave this running for the rest of the session.

## Terminal 2 — Mininet

```bash
cd ~/Desktop/7sem/SDN/ASSIGN_01

sudo mn --topo single,8 --controller remote,ip=127.0.0.1,port=6633 --mac --switch ovsk
```

### Disable IPv6 noise (optional, quiets the POX log)
```
mininet> py [h.cmd('sysctl -w net.ipv6.conf.all.disable_ipv6=1') for h in net.hosts]
mininet> py [h.cmd('sysctl -w net.ipv6.conf.default.disable_ipv6=1') for h in net.hosts]
```

### Baseline connectivity
```
mininet> pingall
```
Expected: heavy/100% drop — only client→VIP traffic is ever handled, and the VIP isn't
a real Mininet host, so `pingall` isn't the real test.

### Client → virtual service IP
```
mininet> h1 ping -c4 10.1.2.3
mininet> h2 ping -c4 10.1.2.3
mininet> h3 ping -c4 10.1.2.3
mininet> h4 ping -c4 10.1.2.3
```

### Inspect installed flow rules
```
mininet> sh ovs-ofctl -O OpenFlow10 dump-flows s1
mininet> sh ovs-ofctl -O OpenFlow10 dump-ports s1
```
`-O OpenFlow10` is required — POX `carp` only speaks OpenFlow 1.0; `ovs-ofctl`
otherwise defaults to OF1.3 and silently returns nothing. No `sudo` needed.

### Longer stability run
```
mininet> h1 ping -c20 10.1.2.3
```

### Negative test (should fail — out of scope by design)
```
mininet> h1 ping -c2 10.0.0.5
mininet> h1 ping -c2 10.0.0.2
```

### Controller connection check
```
mininet> sh ovs-vsctl show
```

### Other useful checks
```
mininet> nodes
mininet> net
mininet> h5 ifconfig
mininet> h5 echo $$
```

## Terminal 3 — Transparency check (tcpdump on h5)

> **The ping in this test MUST target `10.1.2.3` (the VIP), NOT `10.0.0.5` (h5's real
> IP) directly.** Pinging h5 directly is the negative test above and is *supposed* to
> fail — that's a different test.

**Option A — xterm** (if a display/X11 is available):
```
mininet> xterm h5
# in the h5 xterm:
tcpdump -XX -n -i h5-eth0
```

**Option B — mnexec** (headless, no X11 needed):
```
# Terminal 2 (mininet CLI) — get h5's namespace PID:
mininet> h5 echo $$
# note the PID printed

# Terminal 3 (any plain terminal) — enter that namespace:
sudo mnexec -a <PID_from_above> zsh
ifconfig                              # sanity check: should show h5-eth0 / 10.0.0.5
tcpdump -XX -n -i h5-eth0 'icmp'      # 'icmp' filter keeps it to ping traffic only
```

Then, back in Terminal 2, with tcpdump still running:
```
mininet> h1 ping -c2 10.1.2.3
```
Expect to see the arriving packet with source IP `10.0.0.1` (client's real IP,
untouched), destination IP `10.0.0.5` (rewritten from the VIP), and source MAC
`0a:00:00:00:00:01` (load balancer's fake MAC, not h1's real MAC).

## Cleanup
```
mininet> exit
sudo mn -c
# Ctrl-C in Terminal 1 to stop POX
```

## Useful one-off diagnostics

```bash
# check for stale/duplicate POX processes before starting a new one
sudo ss -lntp | grep -E '6633|6653'
podman ps -a
podman inspect <container_name> --format '{{.Name}}: PID {{.State.Pid}}'
podman logs <container_name>

# clean up a dead/stale container
podman stop <container_name> && podman rm <container_name>
```
