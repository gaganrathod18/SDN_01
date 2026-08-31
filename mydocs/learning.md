# learning.md — `SimpleLoadBalancer.py` explained line by line

This walks through [`SimpleLoadBalancer.py`](../SimpleLoadBalancer.py) top to bottom —
what each import, field, and function does, and *why* it's written that way. For the
higher-level design rationale see [`IMPL.md`](IMPL.md) §0; for proof it actually works
see [`REPORT.md`](../REPORT.md).

---

## 1. Background you need first

**OpenFlow, in three events.** A POX app is really just a set of event handlers. POX
calls a method automatically if its name matches `_handle_<EventName>` on a component
registered with `core.openflow.addListeners(self)`. This app only needs two:

- **`ConnectionUp`** — fires once, the moment a switch connects to the controller.
  Good place to do setup that should happen exactly once per connection.
- **`PacketIn`** — fires every time a packet arrives at the switch that doesn't match
  any existing flow table entry, and gets punted up to the controller to decide what to
  do with it.

**Three OpenFlow messages this app sends:**
- `ofp_packet_out` — "switch, send this exact packet out of this port, right now." Used
  for controller-crafted packets (ARP requests/replies) and for forwarding the very
  first packet of a new flow while the flow rule is still being installed.
- `ofp_flow_mod` — "switch, install this rule in your flow table so future matching
  packets are handled by hardware/software fast-path, without asking me again." This is
  what makes the load balancer fast after the first packet.
- Implicitly, **not** sending anything — the switch's default behavior for unmatched,
  un-actioned traffic is to drop it, which this app relies on for out-of-scope traffic
  (see §7).

**Why ARP matters here.** Before any two hosts can exchange IP packets on the same
Ethernet segment, each needs the other's MAC address, learned via ARP. Because this
switch is a transparent proxy, it can't let real ARP happen — if it did, clients would
learn a *real* backend's MAC and IP packets would bypass the controller/switch rewriting
entirely. So the switch intercepts every ARP request and answers on behalf of whoever
was asked for, always with its own fake MAC. This is called **ARP spoofing/proxying**,
and it's the mechanism that makes the whole redirection invisible.

---

## 2. Imports

```python
from pox.core import core
from pox.openflow import *
import pox.openflow.libopenflow_01 as of
from pox.lib.packet.arp import arp
from pox.lib.packet.ipv4 import ipv4
from pox.lib.packet.ethernet import ethernet, ETHER_BROADCAST
from pox.lib.addresses import EthAddr, IPAddr
```

| Import | What it's for |
|---|---|
| `core` | POX's global registry/event bus — `core.openflow` is where `ConnectionUp`/`PacketIn` events come from; `core.getLogger()` gives a named logger |
| `pox.openflow.libopenflow_01 as of` | the actual OpenFlow 1.0 protocol objects: `ofp_flow_mod`, `ofp_packet_out`, `ofp_action_*`, `ofp_match`, constants like `OFPP_FLOOD`/`NO_BUFFER` |
| `arp` | lets you build/read ARP packet fields (`opcode`, `hwsrc`, `hwdst`, `protosrc`, `protodst`) |
| `ipv4` | the parsed IPv4 header class (`srcip`, `dstip`, ...) — imported for clarity even though this app reads it only via `packet.payload` |
| `ethernet`, `ETHER_BROADCAST` | builds/reads Ethernet frames (`src`, `dst`, `type`); `ETHER_BROADCAST` is `ff:ff:ff:ff:ff:ff` |
| `EthAddr`, `IPAddr` | typed wrappers around MAC/IP addresses so comparisons (`==`, `in`) and formatting just work |

`import random` (further down) is used for picking a backend server; `import time` is
unused leftover from the assignment's code skeleton.

---

## 3. Class state — `__init__`

```python
def __init__(self, service_ip, server_ips = []):
    core.openflow.addListeners(self)
    self.service_ip = IPAddr(service_ip)
    self.server_ips = [IPAddr(ip) for ip in server_ips]
    self.lb_mac = EthAddr("0A:00:00:00:00:01")
    self.connection = None
    self.servers_mac_to_port = {}   # server_ip -> (mac, port)
    self.client_to_port = {}        # client_ip -> (mac, port)
    self.client_to_server = {}      # client_ip -> server_ip (sticky)
    log.info("LB ready. service=%s servers=%s", self.service_ip, self.server_ips)
```

- `core.openflow.addListeners(self)` — this one line is what wires up `_handle_*`
  methods to actual POX events. Without it, `_handle_ConnectionUp` and
  `_handle_PacketIn` would just be regular methods that never get called.
- `self.service_ip` / `self.server_ips` — normalized to `IPAddr` objects immediately
  (they arrive as plain strings from `launch()`), so every later comparison
  (`==`, `in self.server_ips`) works correctly instead of comparing strings to objects.
- `self.lb_mac` — the fake MAC every host on the network will believe belongs to
  whichever IP they asked about. Fixed value from the assignment spec.
- **Three dictionaries are the entire "brain" of the load balancer:**
  - `servers_mac_to_port[server_ip] = (mac, switch_port)` — filled once, right after
    connection, by ARP-probing every server. Lets the app build flow rules toward a
    server without needing to ask "which port is this server on?" every time.
  - `client_to_port[client_ip] = (mac, switch_port)` — filled the first time a client
    ARPs for the service IP. Needed later to build the reverse (server→client) flow.
  - `client_to_server[client_ip] = server_ip` — the actual load-balancing decision,
    cached so a given client keeps talking to the same backend for as long as the
    mapping lives (see §5).

---

## 4. `_handle_ConnectionUp` — pre-emptive server discovery

```python
def _handle_ConnectionUp(self, event):
    self.lb_mac = EthAddr("0A:00:00:00:00:01")
    self.connection = event.connection
    log.info("Switch connected, probing %d servers", len(self.server_ips))
    for server_ip in self.server_ips:
        self.send_proxied_arp_request(event.connection, server_ip)
```

Fires once, when the switch first connects to the controller. It stores the
`connection` object (used everywhere else to actually send messages to the switch), and
immediately fires off one ARP request per configured server. This is the "pre-emptive"
part the assignment spec asks for: by resolving all servers' MAC addresses *before* any
client traffic exists, the controller never has to stall a client's first packet waiting
to find out where a server lives — that information is already in
`servers_mac_to_port` by the time it's needed.

---

## 5. `update_lb_mapping` — the actual load-balancing decision

```python
def update_lb_mapping(self, client_ip):
    server_ip = self.client_to_server.get(client_ip)
    if server_ip is None:
        server_ip = random.choice(self.server_ips)
        self.client_to_server[client_ip] = server_ip
        log.info("mapping: %s -> %s", client_ip, server_ip)
    return server_ip
```

This is deliberately tiny. `dict.get(client_ip)` returns `None` if the client hasn't
been seen before; only in that case does it call `random.choice()` and cache the
result. Every subsequent call for the *same* client just returns the cached value
without picking again — this is what makes the mapping **sticky**: once h1 is assigned
to h8, it keeps talking to h8 for as long as that dictionary entry exists (which, since
nothing ever expires it, is for the life of the controller process). The `log.info`
only fires on the *first* assignment, which is why the POX log shows exactly one
`mapping: ...` line per client rather than one per packet.

---

## 6. ARP proxying — `send_proxied_arp_reply` / `send_proxied_arp_request`

### `send_proxied_arp_reply`

```python
def send_proxied_arp_reply(self, packet, connection, outport, requested_mac):
    req = packet.payload
    reply = arp()
    reply.opcode = arp.REPLY
    reply.hwsrc = requested_mac
    reply.hwdst = req.hwsrc
    reply.protosrc = req.protodst
    reply.protodst = req.protosrc

    eth = ethernet(type = ethernet.ARP_TYPE, src = requested_mac, dst = packet.src)
    eth.payload = reply

    msg = of.ofp_packet_out(data = eth.pack())
    msg.actions.append(of.ofp_action_output(port = outport))
    connection.send(msg)
```

`packet` here is the *incoming* ARP request; `packet.payload` is its ARP header
(`req`). Building the reply is a mechanical field swap — this is exactly what real ARP
replies do, just constructed by hand instead of by the OS network stack:

| Reply field | Value | Why |
|---|---|---|
| `opcode` | `arp.REPLY` | marks this as an answer, not a question |
| `hwsrc` | `requested_mac` (always `self.lb_mac`) | "the MAC you asked about is *me*" — this is the entire trick |
| `hwdst` | `req.hwsrc` | send the answer back to whoever asked |
| `protosrc` | `req.protodst` | the IP that was being asked about, now claiming to answer for it |
| `protodst` | `req.protosrc` | the IP of whoever asked |

The Ethernet frame around it uses `src = requested_mac` (again, the fake LB MAC) and
`dst = packet.src` (the asker's real MAC — the reply must reach their NIC even though
its IP-layer answer lies about identity). `eth.pack()` serializes the whole frame to
raw bytes, which `ofp_packet_out` then tells the switch to emit out `outport` — the same
port the original request arrived on, so it's a direct, non-flooded reply.

### `send_proxied_arp_request`

```python
def send_proxied_arp_request(self, connection, ip):
    req = arp()
    req.opcode = arp.REQUEST
    req.hwsrc = self.lb_mac
    req.hwdst = ETHER_BROADCAST
    req.protosrc = self.service_ip
    req.protodst = IPAddr(ip)
    ...
    msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
```

The mirror operation: the controller crafts a request asking "who has `ip`?", claiming
to be `self.service_ip` (so replies come back addressed to the service, keeping the
server-facing side consistent with what servers will later see), and floods it out
every port (`OFPP_FLOOD`) since the controller doesn't yet know which port the target is
on — that's exactly what this function exists to discover.

---

## 7. Installing flow rules — where the actual redirection happens

Both of the next two functions build an `ofp_flow_mod` — a rule the switch will apply to
matching packets **without** asking the controller again, until it expires
(`idle_timeout=10`, i.e. 10 seconds of no matching traffic).

### `install_flow_rule_client_to_server`

```python
def install_flow_rule_client_to_server(self, connection, outport, client_ip, server_ip, buffer_id=of.NO_BUFFER):
    server_mac, _ = self.servers_mac_to_port[server_ip]
    msg = of.ofp_flow_mod(buffer_id = buffer_id, idle_timeout = 10)
    msg.match.dl_type = ethernet.IP_TYPE
    msg.match.nw_src = client_ip
    msg.match.nw_dst = self.service_ip
    msg.actions.append(of.ofp_action_dl_addr.set_dst(server_mac))
    msg.actions.append(of.ofp_action_nw_addr.set_dst(server_ip))
    msg.actions.append(of.ofp_action_dl_addr.set_src(self.lb_mac))
    msg.actions.append(of.ofp_action_output(port = outport))
```

**The match** (`msg.match`) says: "any IPv4 packet (`dl_type = 0x0800`) whose source is
this specific client and whose destination is the virtual service IP." This is
intentionally *not* a microflow — it doesn't match on ports, protocol, or MAC — matching
only on IP addresses is what the assignment requires, and it means one rule covers *all*
traffic (ping, TCP, anything) between that client and the service, not just one
connection.

**The actions**, applied in order to every matching packet:
1. `set_dst(server_mac)` — rewrite the destination MAC from the LB's fake MAC to the
   real server's MAC, so the frame actually reaches the right physical/virtual NIC.
2. `set_dst(server_ip)` — rewrite the destination IP from the virtual service IP to the
   server's real IP. This is the actual redirection.
3. `set_src(self.lb_mac)` — rewrite the source MAC to the LB's fake MAC (not the
   client's real MAC), so the server's ARP table only ever learns about the load
   balancer, never the client directly.
4. `output(port = outport)` — finally, send it out the port the target server sits on.

Note what's **not** rewritten: the source IP. The client's real IP is left untouched,
so the server can still see (and reply to) the actual originating client — the
assignment spec calls this out explicitly ("the source client IP intact").

### `install_flow_rule_server_to_client`

```python
def install_flow_rule_server_to_client(self, connection, outport, server_ip, client_ip, buffer_id=of.NO_BUFFER):
    client_mac, _ = self.client_to_port[client_ip]
    ...
    msg.match.nw_src = server_ip
    msg.match.nw_dst = client_ip
    msg.actions.append(of.ofp_action_nw_addr.set_src(self.service_ip))
    msg.actions.append(of.ofp_action_dl_addr.set_src(self.lb_mac))
    msg.actions.append(of.ofp_action_dl_addr.set_dst(client_mac))
    msg.actions.append(of.ofp_action_output(port = outport))
```

The mirror rule for return traffic: matches packets from the real server back to the
real client, and rewrites **source** IP/MAC (server's real identity → service IP + fake
LB MAC) so the client believes it's still talking directly to `10.1.2.3`, then sets the
destination MAC to the client's real MAC and sends it out the client's port. Between
these two flow-mods, neither side of the conversation ever sees the other's true
identity — that's the whole transparency guarantee.

---

## 8. `_handle_PacketIn` — the dispatcher

```python
def _handle_PacketIn(self, event):
    packet = event.parsed
    connection = event.connection
    inport = event.port

    if not packet.parsed:
        return
    if packet.type == packet.ARP_TYPE:
        self._handle_arp(packet, connection, inport)
    elif packet.type == packet.IP_TYPE:
        self._handle_ip(event, packet, connection, inport)
    else:
        log.info("Unknown Packet type: %s" % packet.type)
    return
```

Every packet that reaches the controller (because no flow rule matched it yet) comes
through here first. `packet.parsed` guards against a corrupt/truncated frame POX
couldn't fully decode. Then it's a simple type switch: ARP goes to `_handle_arp`, IPv4
goes to `_handle_ip`, and everything else (IPv6, LLDP, etc.) is logged and dropped —
per spec, "you should only consider ARP and IP protocol packet types."

---

## 9. `_handle_arp` — answering ARP requests, both directions

```python
def _handle_arp(self, packet, connection, inport):
    arp_pkt = packet.payload

    if arp_pkt.opcode == arp.REPLY:
        if arp_pkt.protosrc in self.server_ips:
            self.servers_mac_to_port[arp_pkt.protosrc] = (packet.src, inport)
            log.info("server %s at %s port %s", arp_pkt.protosrc, packet.src, inport)
        return

    if arp_pkt.opcode != arp.REQUEST:
        return

    if arp_pkt.protodst == self.service_ip and arp_pkt.protosrc not in self.server_ips:
        self.client_to_port[arp_pkt.protosrc] = (packet.src, inport)
        log.info("client %s at %s port %s", arp_pkt.protosrc, packet.src, inport)
        self.send_proxied_arp_reply(packet, connection, inport, self.lb_mac)
    elif arp_pkt.protosrc in self.server_ips:
        self.send_proxied_arp_reply(packet, connection, inport, self.lb_mac)
```

Three cases, in order:

1. **It's a reply, and it's from a server** — this is the answer to a
   `send_proxied_arp_request` sent in `_handle_ConnectionUp`. Learn and cache
   `(mac, port)` for that server in `servers_mac_to_port`. (Replies from anything else
   are ignored — the controller never asked clients anything.)
2. **It's a request, addressed to the service IP, from a non-server** — a client asking
   "who has `10.1.2.3`?" First, learn the client's `(mac, port)` — this is the *only*
   place `client_to_port` gets populated, which is why a server can only later resolve a
   client's MAC after that client has already ARPed for the service at least once (a
   sequencing detail the assignment spec calls out explicitly). Then answer with the
   fake LB MAC.
3. **It's a request, and the asker is a known server** — a server asking "who has
   `<some client IP>`?" (really: "who is this traffic from?"). Answer with the fake LB
   MAC again — the server should never learn a client's real MAC, only the load
   balancer's.

Any other combination (e.g. a request for an unrelated IP) falls through and is
silently ignored — correctly, since it's out of scope.

---

## 10. `_handle_ip` — where flows actually get installed

```python
def _handle_ip(self, event, packet, connection, inport):
    ip_pkt = packet.payload
    src_ip, dst_ip = ip_pkt.srcip, ip_pkt.dstip
    buffer_id = event.ofp.buffer_id

    if dst_ip == self.service_ip and src_ip not in self.server_ips:
        server_ip = self.update_lb_mapping(src_ip)
        if server_ip not in self.servers_mac_to_port or src_ip not in self.client_to_port:
            log.warning("missing mac/port for %s or %s, dropping", src_ip, server_ip)
            return

        server_mac, server_port = self.servers_mac_to_port[server_ip]
        client_port = self.client_to_port[src_ip][1]

        self.install_flow_rule_client_to_server(connection, server_port, src_ip, server_ip, buffer_id)
        self.install_flow_rule_server_to_client(connection, client_port, server_ip, src_ip, of.NO_BUFFER)

        if buffer_id == of.NO_BUFFER:
            msg = of.ofp_packet_out(data = event.ofp.data)
            msg.actions.append(of.ofp_action_dl_addr.set_dst(server_mac))
            msg.actions.append(of.ofp_action_nw_addr.set_dst(server_ip))
            msg.actions.append(of.ofp_action_dl_addr.set_src(self.lb_mac))
            msg.actions.append(of.ofp_action_output(port = server_port))
            connection.send(msg)
```

**Branch 1 — a client's IP packet addressed to the service.** This is the path that
runs on the very first packet of a new (or expired) flow:

1. `update_lb_mapping(src_ip)` — get (or assign) the backend for this client.
2. Sanity-check that both the server's and the client's `(mac, port)` are already
   known — if either is missing (shouldn't normally happen, since servers are probed on
   connect and clients are learned via ARP before they can send IP traffic), log a
   warning and drop rather than crash on a `KeyError`.
3. **Install both flow directions immediately** — not just the one this packet needs.
   Installing the reverse (`server_to_client`) rule proactively means the *server's*
   first reply packet also has a fast-path rule waiting for it, instead of triggering a
   second controller round-trip.
4. **Handle the triggering packet itself.** If the switch buffered it
   (`buffer_id != NO_BUFFER`), the `buffer_id` passed into the flow-mod above tells the
   switch to run the new rule's actions on that buffered packet automatically — no extra
   message needed. If it *wasn't* buffered (`buffer_id == NO_BUFFER`), the code falls
   back to explicitly reconstructing the same rewrite as a one-off `ofp_packet_out`,
   using the raw `event.ofp.data` bytes — otherwise that very first packet would be
   silently lost while only the *next* one benefits from the new rule.

```python
    elif src_ip in self.server_ips and dst_ip in self.client_to_port:
        client_mac, client_port = self.client_to_port[dst_ip]
        self.install_flow_rule_server_to_client(connection, client_port, src_ip, dst_ip, buffer_id)

        if buffer_id == of.NO_BUFFER:
            msg = of.ofp_packet_out(data = event.ofp.data)
            msg.actions.append(of.ofp_action_nw_addr.set_src(self.service_ip))
            msg.actions.append(of.ofp_action_dl_addr.set_src(self.lb_mac))
            msg.actions.append(of.ofp_action_dl_addr.set_dst(client_mac))
            msg.actions.append(of.ofp_action_output(port = client_port))
            connection.send(msg)
```

**Branch 2 — a server's IP packet back to a known client.** This is a safety net for
the case where a server's reply reaches the controller *before* branch 1's proactive
`install_flow_rule_server_to_client` call has taken effect (a race that can happen on
the very first exchange of a session). It installs the same reverse rule and, again,
explicitly forwards the packet if it wasn't buffered.

Anything that matches **neither** branch (client↔client, server↔server, traffic to a
real backend IP instead of the service IP) simply falls through the `if`/`elif` with no
`else` — nothing is sent, so the switch's implicit default (drop) applies. That silence
is deliberate: it's what makes the negative tests in [`REPORT.md`](../REPORT.md) §9
(client pinging a backend directly, or another client) correctly fail instead of
leaking traffic the app was never asked to handle.

---

## 11. `launch()` — how POX wires it all up

```python
def launch(ip, servers):
    log.info("Loading Simple Load Balancer module")
    server_ips = [IPAddr(x) for x in servers.replace(","," ").split()]
    core.registerNew(SimpleLoadBalancer, IPAddr(ip), server_ips)
```

POX looks for a module-level `launch()` function and calls it with whatever
`--key=value` arguments were passed on the command line
(`--ip=10.1.2.3 --servers=10.0.0.5,10.0.0.6,10.0.0.7,10.0.0.8`), both arriving as plain
strings. `servers.replace(",", " ").split()` turns the comma-separated string into a
list of individual IP strings, each wrapped in `IPAddr`.
`core.registerNew(SimpleLoadBalancer, ...)` instantiates the class (calling `__init__`)
and registers it as a POX component, which is what makes `core.openflow.addListeners`
inside `__init__` actually start receiving events.

---

## 12. Putting it all together — one full request, start to finish

For `h1 ping 10.1.2.3` on a freshly-started controller:

1. **Connect time:** `_handle_ConnectionUp` fires → 4 ARP requests flooded, one per
   server → each server replies → `_handle_arp` (reply branch) fills
   `servers_mac_to_port` for all 4.
2. **h1 sends its first ARP** ("who has `10.1.2.3`?") → `_handle_arp` (request branch,
   case 2) learns `client_to_port[10.0.0.1]`, replies with the fake LB MAC.
3. **h1 sends the ping's IP packet** → `_handle_PacketIn` → `_handle_ip` branch 1:
   `update_lb_mapping` picks a server, both flow rules get installed, the packet itself
   is forwarded to the chosen server (rewritten: dst MAC/IP → server, src MAC → LB).
4. **The server ARPs for "the client"** (really: for the source of that rewritten
   packet, i.e. what it thinks is `10.0.0.1` reachable via the LB's MAC — in practice it
   already has the LB's MAC from the Ethernet frame, but if it needs to ARP, `_handle_arp`
   request-branch case 3 answers with the fake LB MAC again).
5. **The server's ICMP reply** matches the pre-installed `server_to_client` flow rule
   directly in the switch — no controller involvement — rewritten (src IP/MAC → service
   IP + fake LB MAC) and delivered to h1's port.
6. h1 sees a reply that appears to come from `10.1.2.3`, never learning a real server
   was ever involved.

This is exactly the sequence [`REPORT.md`](../REPORT.md) §5 Step 6 captures in real POX
log output.
