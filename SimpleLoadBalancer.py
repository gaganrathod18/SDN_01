#-------- CODE START --------
from pox.core import core
from pox.openflow import *
import pox.openflow.libopenflow_01 as of
from pox.lib.packet.arp import arp
from pox.lib.packet.ipv4 import ipv4
from pox.lib.packet.ethernet import ethernet, ETHER_BROADCAST
from pox.lib.addresses import EthAddr, IPAddr

log = core.getLogger()

import time
import random


class SimpleLoadBalancer(object):

    def __init__(self, service_ip, server_ips = []): #initialize
        core.openflow.addListeners(self)
        self.service_ip = IPAddr(service_ip)
        self.server_ips = [IPAddr(ip) for ip in server_ips]
        self.lb_mac = EthAddr("0A:00:00:00:00:01")
        self.connection = None
        self.servers_mac_to_port = {}   # server_ip -> (mac, port)
        self.client_to_port = {}        # client_ip -> (mac, port)
        self.client_to_server = {}      # client_ip -> server_ip (sticky)
        log.info("LB ready. service=%s servers=%s", self.service_ip, self.server_ips)

    def _handle_ConnectionUp(self, event): #new switch connection
        self.lb_mac = EthAddr("0A:00:00:00:00:01")
        self.connection = event.connection
        log.info("Switch connected, probing %d servers", len(self.server_ips))
        for server_ip in self.server_ips:
            self.send_proxied_arp_request(event.connection, server_ip)

    def update_lb_mapping(self, client_ip): #update load balancing mapping
        server_ip = self.client_to_server.get(client_ip)
        if server_ip is None:
            server_ip = random.choice(self.server_ips)
            self.client_to_server[client_ip] = server_ip
            log.info("mapping: %s -> %s", client_ip, server_ip)
        return server_ip

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
        log.debug("ARP reply: %s is-at %s -> %s", reply.protosrc, requested_mac, reply.protodst)

    def send_proxied_arp_request(self, connection, ip):
        req = arp()
        req.opcode = arp.REQUEST
        req.hwsrc = self.lb_mac
        req.hwdst = ETHER_BROADCAST
        req.protosrc = self.service_ip
        req.protodst = IPAddr(ip)

        eth = ethernet(type = ethernet.ARP_TYPE, src = self.lb_mac, dst = ETHER_BROADCAST)
        eth.payload = req

        msg = of.ofp_packet_out(data = eth.pack())
        msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
        connection.send(msg)
        log.debug("ARP request for %s", ip)

    def install_flow_rule_client_to_server(self, connection, outport, client_ip, server_ip,
                                            buffer_id=of.NO_BUFFER):
        server_mac, _ = self.servers_mac_to_port[server_ip]
        msg = of.ofp_flow_mod(buffer_id = buffer_id, idle_timeout = 10)
        msg.match.dl_type = ethernet.IP_TYPE
        msg.match.nw_src = client_ip
        msg.match.nw_dst = self.service_ip
        msg.actions.append(of.ofp_action_dl_addr.set_dst(server_mac))
        msg.actions.append(of.ofp_action_nw_addr.set_dst(server_ip))
        msg.actions.append(of.ofp_action_dl_addr.set_src(self.lb_mac))
        msg.actions.append(of.ofp_action_output(port = outport))
        connection.send(msg)
        log.debug("flow c2s: %s->%s ==> %s (port %s)", client_ip, self.service_ip, server_ip, outport)

    def install_flow_rule_server_to_client(self, connection, outport, server_ip, client_ip,
                                            buffer_id=of.NO_BUFFER):
        client_mac, _ = self.client_to_port[client_ip]
        msg = of.ofp_flow_mod(buffer_id = buffer_id, idle_timeout = 10)
        msg.match.dl_type = ethernet.IP_TYPE
        msg.match.nw_src = server_ip
        msg.match.nw_dst = client_ip
        msg.actions.append(of.ofp_action_nw_addr.set_src(self.service_ip))
        msg.actions.append(of.ofp_action_dl_addr.set_src(self.lb_mac))
        msg.actions.append(of.ofp_action_dl_addr.set_dst(client_mac))
        msg.actions.append(of.ofp_action_output(port = outport))
        connection.send(msg)
        log.debug("flow s2c: %s->%s ==> %s (port %s)", server_ip, client_ip, self.service_ip, outport)

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


#launch application with following arguments:
#ip: public service ip, servers: ip addresses of servers (in string format)
def launch(ip, servers):
    log.info("Loading Simple Load Balancer module")
    server_ips = [IPAddr(x) for x in servers.replace(","," ").split()]
    core.registerNew(SimpleLoadBalancer, IPAddr(ip), server_ips)
#-------- CODE END --------
