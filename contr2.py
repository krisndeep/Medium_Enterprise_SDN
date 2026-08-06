#!/usr/bin/env python3
"""
enterprise_controller.py

Ryu OpenFlow 1.3 controller for the enterprise SDN topology built by
topology.py.

Responsibilities
----------------
* Standard learning-switch behaviour (MAC learning, flooding of unknown
  destinations, dynamic flow installation) on every switch in the
  network.
* On the aggregation switch (DPID 1) only:
    - Classify traffic by DEPARTMENT using (dpid, ingress port).
    - Classify Management traffic by VLAN ID (50 = Upper, 60 = Lower).
    - Enforce inter-department communication policy (allow/deny).
    - Assign OpenFlow queues (QoS) and flow priorities according to
      department / VLAN / VoIP detection.

No IP addresses are ever used for classification, QoS, or policy -- only
switch DPID, ingress port, VLAN ID, and (for VoIP detection) UDP port
numbers.

This file must stay consistent with the DPID/port map, VLAN IDs, queue
IDs, and OpenFlow priorities defined in topology.py.
"""
import os
import socket
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from ryu.lib.packet import arp
from ryu.lib.packet import ipv4
from ryu.lib.packet import udp
from ryu.lib.packet import vlan as vlan_pkt


# ==========================================================================
# Network-wide constants (MUST match topology.py)
# ==========================================================================

# DPID of the aggregation switch. Department classification and policy
# enforcement only apply to traffic seen on this switch.
AGGREGATION_DPID = 1

# Aggregation switch ingress-port -> department name.
PORT_TO_DEPARTMENT = {
    1: 'HR',
    2: 'SDE',
    3: 'BUSINESS',
    4: 'CALL_SUPPORT',
    5: 'MANAGEMENT',
    6: 'INFRASTRUCTURE',
    7: 'SERVERS',
    8: 'ATTACKER',
}

# VLAN IDs used exclusively within the Management department.
VLAN_UPPER_MGMT = 50
VLAN_LOWER_MGMT = 60

# VoIP protocol ports (Upper Management only).
SIP_PORT = 5060
RTP_PORT_MIN = 16384
RTP_PORT_MAX = 32767

# Queue IDs (must match QUEUE_CONFIG queue ids in topology.py).
QUEUE_UPPER_MGMT_VOIP = 7
QUEUE_INFRASTRUCTURE = 6
QUEUE_CALL_SUPPORT = 5
QUEUE_SDE = 4
QUEUE_BUSINESS = 3
QUEUE_UPPER_MGMT = 2
QUEUE_LOWER_MGMT = 1
QUEUE_HR = 0

# Department (non-Management) -> queue.
DEPARTMENT_QUEUE = {
    'INFRASTRUCTURE': QUEUE_INFRASTRUCTURE,
    'CALL_SUPPORT': QUEUE_CALL_SUPPORT,
    'SDE': QUEUE_SDE,
    'BUSINESS': QUEUE_BUSINESS,
    'HR': QUEUE_HR,
    # SERVERS and MANAGEMENT are handled specially / by VLAN.
}

# Queue -> OpenFlow flow priority.
QUEUE_PRIORITY = {
    QUEUE_UPPER_MGMT_VOIP: 400,
    QUEUE_INFRASTRUCTURE: 300,
    QUEUE_CALL_SUPPORT: 290,
    QUEUE_SDE: 280,
    QUEUE_BUSINESS: 270,
    QUEUE_UPPER_MGMT: 260,
    QUEUE_LOWER_MGMT: 250,
    QUEUE_HR: 240,
}
DEFAULT_QOS_PRIORITY = 240      # fallback priority for any queue not listed
NON_AGGREGATION_PRIORITY = 10   # ordinary L2 forwarding flows on other switches
BLOCKED_FLOW_PRIORITY = 500     # explicit DROP flows always win
TABLE_MISS_PRIORITY = 0
BLOCKED_FLOW_IDLE_TIMEOUT = 30  # seconds

# --------------------------------------------------------------------------
# Attacker Security Policy constants
# --------------------------------------------------------------------------
# Policy 1 – Critical infrastructure departments the attacker MUST NOT reach.
ATTACKER_CRITICAL_TARGETS = {'INFRASTRUCTURE', 'MANAGEMENT', 'SERVERS'}

# Policy 3 – Dynamic quarantine: after this many violations the attacker
#             is fully quarantined (all traffic from its MAC is dropped).
ATTACKER_QUARANTINE_THRESHOLD = 3
QUARANTINE_FLOW_PRIORITY = 600  # higher than any other rule

# Inter-department communication policy.
# Departments explicitly forbidden from talking to each other.
BLOCKED_PAIRS = {
    frozenset({'SDE', 'BUSINESS'}),
    frozenset({'SDE', 'CALL_SUPPORT'}),
    frozenset({'BUSINESS', 'CALL_SUPPORT'}),
}

# Departments that are allowed to communicate with every other department.
UNIVERSAL_DEPARTMENTS = {'MANAGEMENT', 'INFRASTRUCTURE', 'SERVERS'}

HEALTH_CHECK_INTERVAL = 2 # liveness check interval in seconds
FAILS_BEFORE_PROMOTION = 3 # number of consecutive failed health checks before a standby controller takes control

class EnterpriseController(app_manager.RyuApp):
    """OpenFlow 1.3 enterprise learning switch with VLAN/QoS/policy logic."""

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(EnterpriseController, self).__init__(*args, **kwargs)
        # mac_to_port[dpid][mac] = port
        self.mac_to_port = {}

        # -- Attacker dynamic quarantine state --
        # violation_count[mac_address] = int
        self.violation_count = {}
        # quarantined[mac_address] = True when full quarantine is active
        self.quarantined = {}

        # -- primary/standby controller failover support --
        self.datapaths = {}
        role = os.environ.get('RYU_ROLE', 'primary').lower()
        self.is_primary = (role == 'primary')
        self.current_role = 'MASTER' if self.is_primary else 'SLAVE'

        self.peer_ip = os.environ.get('RYU_PEER_IP', '127.0.0.1')
        self.peer_port = int(os.environ.get('RYU_PEER_PORT', '6653'))
        self.gen_id = int(time.time())  

        self.logger.info('Startting as %s -> initial OpenFlow role %s', role.upper(), self.current_role)

        if not self.is_primary:
            hub.spawn(self._monitor_primary)

    # ----------------------------------------------------------------
    # primary/standby controller failover support
    # ----------------------------------------------------------------
    def _monitor_primary(self):
        fails = 0
        while True:
            hub.sleep(HEALTH_CHECK_INTERVAL)
            if self._peer_alive():
                fails = 0
                continue
            fails+=1
            self.logger.warning('Primary unreachable (%d/%d checks failed)', fails, FAILS_BEFORE_PROMOTION)
            if fails >= FAILS_BEFORE_PROMOTION and self.current_role != 'MASTER':
                self.logger.warning('Primary presumed DOWN -> promoting standby to MASTER')
                self._promote_to_master()

    def _peer_alive(self):
        try:
            s=socket.create_connection((self.peer_ip, self.peer_port), timeout=1)
            s.close()
            return True
        except OSError:
            return False

    def _promote_to_master(self):
        self.current_role = 'MASTER'
        self.gen_id = int(time.time())
        for dp in self.datapaths.values():
            self._send_role_request(dp, dp.ofproto.OFPCR_ROLE_MASTER)

    def _send_role_request(self, datapath, role):
        parser = datapath.ofproto_parser
        datapath.send_msg(parser.OFPRoleRequest(datapath, role, self.gen_id))

    # ----------------------------------------------------------------
    # Switch connection / table-miss installation
    # ----------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        self.logger.info('Switch connected: dpid=%s', datapath.id)

        role = ofproto.OFPCR_ROLE_MASTER if self.current_role == 'MASTER' else ofproto.OFPCR_ROLE_SLAVE
        self._send_role_request(datapath, role)

        self.logger.info('Switch connected: dpid=%s', datapath.id)

        # Table-miss flow: send anything not matched by a higher-priority
        # rule up to the controller.
        match = parser.OFPMatch()
        actions = [
            parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)
        ]
        self.add_flow(datapath, TABLE_MISS_PRIORITY, match, actions)
        self.logger.info('Installed table-miss flow on dpid=%s', datapath.id)

    @set_ev_cls(ofp_event.EventOFPStateChange, DEAD_DISPATCHER)
    def switch_disconnect_handler(self, ev):
        dp = ev.datapath
        if dp and dp.id in self.datapaths:
            del self.datapaths[dp.id]
            self.logger.info('Switch disconnected: dpid=%s', dp.id)

    # ----------------------------------------------------------------
    # Helper: install a flow entry
    # ----------------------------------------------------------------
    def add_flow(self, datapath, priority, match, actions,
                 idle_timeout=0, hard_timeout=0, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        if buffer_id is not None:
            mod = parser.OFPFlowMod(
                datapath=datapath, buffer_id=buffer_id, priority=priority,
                match=match, instructions=inst,
                idle_timeout=idle_timeout, hard_timeout=hard_timeout,
            )
        else:
            mod = parser.OFPFlowMod(
                datapath=datapath, priority=priority,
                match=match, instructions=inst,
                idle_timeout=idle_timeout, hard_timeout=hard_timeout,
            )
        datapath.send_msg(mod)

    # ----------------------------------------------------------------
    # Helper: department classification (aggregation switch only)
    # ----------------------------------------------------------------
    def get_department(self, dpid, in_port):
        """Return the department name for a given aggregation-switch port,
        or None if this dpid is not the aggregation switch / port unknown."""
        if dpid != AGGREGATION_DPID:
            return None
        return PORT_TO_DEPARTMENT.get(in_port)

    # ----------------------------------------------------------------
    # Helper: extract VLAN ID (Management sub-classification)
    # ----------------------------------------------------------------
    def get_management_vlan(self, msg):
        
            ofproto = msg.datapath.ofproto
            vlan_vid = msg.match.get('vlan_vid')

            if vlan_vid is None or not (vlan_vid & ofproto.OFPVID_PRESENT):
                return None

            return vlan_vid & ~ofproto.OFPVID_PRESENT
    # ----------------------------------------------------------------
    # Helper: inter-department policy check
    # ----------------------------------------------------------------
    def pair_allowed(self, src_group, dst_group):
        """Return True if src_group is permitted to communicate with
        dst_group, per the enterprise communication policy."""
        if src_group is None or dst_group is None:
            # Unknown classification (e.g. flooding) - do not block.
            return True

        # --- Attacker Policy 2: Department Isolation ---
        # The attacker is not a member of any department.  ALL
        # communication from/to the ATTACKER group is denied.
        if src_group == 'ATTACKER' or dst_group == 'ATTACKER':
            return False

        if src_group == dst_group:
            return True

        if src_group in UNIVERSAL_DEPARTMENTS or dst_group in UNIVERSAL_DEPARTMENTS:
            return True

        if frozenset({src_group, dst_group}) in BLOCKED_PAIRS:
            return False

        return True

    # ----------------------------------------------------------------
    # Helper: VoIP detection (Upper Management only)
    # ----------------------------------------------------------------
    def is_voip(self, pkt):
        """Detect SIP (UDP/5060) or RTP (UDP/16384-32767) traffic."""
        udp_header = pkt.get_protocol(udp.udp)
        if udp_header is None:
            return False

        for port in (udp_header.src_port, udp_header.dst_port):
            if port == SIP_PORT:
                return True
            if RTP_PORT_MIN <= port <= RTP_PORT_MAX:
                return True
        return False

    # ----------------------------------------------------------------
    # Helper: queue selection
    # ----------------------------------------------------------------
    def get_queue(self, department, vlan_id, pkt):
        """Select the OpenFlow queue ID for this flow."""
        if department == 'MANAGEMENT':
            if vlan_id == VLAN_UPPER_MGMT:
                if self.is_voip(pkt):
                    self.logger.info('VoIP traffic detected on VLAN %s -> queue %s',
                                      VLAN_UPPER_MGMT, QUEUE_UPPER_MGMT_VOIP)
                    return QUEUE_UPPER_MGMT_VOIP
                return QUEUE_UPPER_MGMT
            if vlan_id == VLAN_LOWER_MGMT:
                return QUEUE_LOWER_MGMT
            # Untagged / unexpected management traffic: fail safe to the
            # lower-priority management queue.
            self.logger.warning('Management traffic without recognised VLAN '
                                 '(vlan=%s) - defaulting to lower-management queue',
                                 vlan_id)
            return QUEUE_LOWER_MGMT

        # SERVERS has no dedicated queue in the spec; treat as best-effort
        # using the Business queue tier (mid-priority, not called out
        # explicitly). Any department not explicitly listed also falls
        # back here.
        return DEPARTMENT_QUEUE.get(department, QUEUE_BUSINESS)

    # ----------------------------------------------------------------
    # Helper: priority selection
    # ----------------------------------------------------------------
    def get_priority(self, queue_id):
        return QUEUE_PRIORITY.get(queue_id, DEFAULT_QOS_PRIORITY)

    # ----------------------------------------------------------------
    # Main packet-in handler
    # ----------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        if self.current_role != 'MASTER':
            return
        
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        # Ignore LLDP / IPv6 discovery noise.
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst = eth.dst
        src = eth.src

        self.mac_to_port.setdefault(dpid, {})

        # ---- MAC learning ------------------------------------------------
        if src not in self.mac_to_port[dpid]:
            self.logger.info('MAC learned: dpid=%s mac=%s port=%s', dpid, src, in_port)
        self.mac_to_port[dpid][src] = in_port

        # ---- Protocol-aware logging (ARP / IPv4) --------------------------
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            arp_header = pkt.get_protocol(arp.arp)
            if arp_header is not None:
                self.logger.debug('ARP packet: dpid=%s src=%s dst_ip=%s',
                                   dpid, arp_header.src_ip, arp_header.dst_ip)
        elif eth.ethertype == ether_types.ETH_TYPE_IP:
            ip_header = pkt.get_protocol(ipv4.ipv4)
            if ip_header is not None:
                self.logger.debug('IPv4 packet: dpid=%s src=%s dst=%s',
                                   dpid, ip_header.src, ip_header.dst)

        # ---- Department / VLAN classification (aggregation switch only) --
        src_department = self.get_department(dpid, in_port)
        vlan_id = None
        if src_department == 'MANAGEMENT':
            #self.logger.info('FULL MATCH FIELDS: %s', dict(msg.match.items()))
            #self.logger.info('RAW FRAME HEX: %s', msg.data.hex())
            vlan_id = self.get_management_vlan(msg)
            self.logger.info('Management VLAN detected: dpid=%s in_port=%s vlan=%s',
                              dpid, in_port, vlan_id)

        # ---- Determine egress port via MAC table --------------------------
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        dst_department = None
        if dpid == AGGREGATION_DPID and out_port != ofproto.OFPP_FLOOD:
            dst_department = self.get_department(dpid, out_port)

        # ---- Policy enforcement (aggregation switch only, both ends known) -
        if dpid == AGGREGATION_DPID and src_department is not None and dst_department is not None:
            if not self.pair_allowed(src_department, dst_department):
                # --- Attacker-specific enhanced enforcement ---
                if src_department == 'ATTACKER':
                    self._handle_attacker_violation(
                        datapath, parser, in_port, src, dst,
                        src_department, dst_department,
                    )
                    return  # Drop this packet; do not forward it.

                match = parser.OFPMatch(in_port=in_port, eth_src=src, eth_dst=dst)
                self.add_flow(
                    datapath, BLOCKED_FLOW_PRIORITY, match, actions=[],
                    idle_timeout=BLOCKED_FLOW_IDLE_TIMEOUT,
                )
                self.logger.warning(
                    'BLOCKED communication: %s -> %s (dpid=%s in_port=%s src=%s dst=%s)',
                    src_department, dst_department, dpid, in_port, src, dst,
                )
                return  # Drop this packet; do not forward it.

       
        # ---- Build forwarding actions (with QoS on aggregation switch) ----
        if dpid == AGGREGATION_DPID and src_department is not None and out_port != ofproto.OFPP_FLOOD:
            queue_id = self.get_queue(src_department, vlan_id, pkt)
            priority = self.get_priority(queue_id)
            actions = [
                parser.OFPActionSetQueue(queue_id),
                parser.OFPActionOutput(out_port),
            ]
            self.logger.info(
                'Flow install: dpid=%s dept=%s vlan=%s queue=%s priority=%s '
                'in_port=%s out_port=%s src=%s dst=%s',
                dpid, src_department, vlan_id, queue_id, priority,
                in_port, out_port, src, dst,
            )
        else:
            actions = []
            if dpid != AGGREGATION_DPID:
                # 1. Egressing via uplink (port 1) towards the aggregation switch -> Push VLAN
                if out_port == 1:
                    # Manually apply VLAN headers specifically for the Management switch (DPID 6)
                    if dpid == 6:
                        vid = 50 if in_port in (2, 3) else 60
                        actions.append(parser.OFPActionPushVlan(ether_types.ETH_TYPE_8021Q))
                        actions.append(parser.OFPActionSetField(vlan_vid=(vid | ofproto.OFPVID_PRESENT)))
                
                # 2. Ingressing from uplink (port 1), egressing to a host port -> Pop VLAN safely
                elif in_port == 1 and out_port != ofproto.OFPP_FLOOD and out_port != 1:
                    # Explicitly validate the presence of the 802.1Q header prior to execution
                    if 'vlan_vid' in msg.match:
                        actions.append(parser.OFPActionPopVlan())

            actions.append(parser.OFPActionOutput(out_port))
            priority = NON_AGGREGATION_PRIORITY

        # ---- Install the flow (avoid re-triggering packet-in for known dst)
        if out_port != ofproto.OFPP_FLOOD:
            match_fields = {'in_port': in_port, 'eth_src': src, 'eth_dst': dst}

            # On the aggregation switch, a bare L2 match is too coarse: if
            # e.g. an ARP packet is the first packet seen between a given
            # MAC pair, its flow (installed with a generic non-VoIP queue)
            # would silently "shadow" all later traffic between the same
            # two MACs -- including UDP VoIP traffic -- since matching
            # packets never reach the controller again for reclassification.
            # Including eth_type (and UDP port info) keeps ARP, plain IP,
            # and VoIP UDP traffic in separate flow entries so each gets
            # independently classified.
            if dpid == AGGREGATION_DPID:
                match_fields['eth_type'] = eth.ethertype
                if src_department == 'MANAGEMENT' and vlan_id is not None:
                    match_fields['vlan_vid'] = vlan_id | ofproto.OFPVID_PRESENT
                if eth.ethertype == ether_types.ETH_TYPE_IP:
                    ip_header = pkt.get_protocol(ipv4.ipv4)
                    if ip_header is not None:
                        match_fields['ip_proto'] = ip_header.proto
                        if ip_header.proto == 17:  # UDP
                            udp_header = pkt.get_protocol(udp.udp)
                            if udp_header is not None:
                                match_fields['udp_src'] = udp_header.src_port
                                match_fields['udp_dst'] = udp_header.dst_port

            match = parser.OFPMatch(**match_fields)
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, priority, match, actions, buffer_id=msg.buffer_id)
                return
            else:
                self.add_flow(datapath, priority, match, actions)

        # ---- Send this specific packet out immediately ---------------------
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port,
            actions=actions, data=data,
        )
        datapath.send_msg(out)

    # ----------------------------------------------------------------
    # Attacker Policy Enforcement
    # ----------------------------------------------------------------
    def _handle_attacker_violation(self, datapath, parser, in_port,
                                   src_mac, dst_mac,
                                   src_department, dst_department):
        """Handle a policy violation originating from the ATTACKER.

        Policy 1 – Installs a high-priority DROP rule for the specific
                   src/dst pair when the destination is critical infrastructure.
        Policy 2 – Installs a DROP for any other blocked pair.
        Policy 3 – Tracks violations; once the threshold is exceeded,
                   installs a blanket MAC-based quarantine rule that drops
                   ALL traffic from the attacker.
        """
        # --- Policy 1: Log and block critical-infrastructure access ---
        if dst_department in ATTACKER_CRITICAL_TARGETS:
            self.logger.warning(
                'ATTACKER POLICY-1: Blocked attacker access to critical '
                'infrastructure: %s -> %s (src=%s dst=%s)',
                src_department, dst_department, src_mac, dst_mac,
            )
        else:
            self.logger.warning(
                'ATTACKER POLICY-2: Blocked attacker lateral movement: '
                '%s -> %s (src=%s dst=%s)',
                src_department, dst_department, src_mac, dst_mac,
            )

        # Install a DROP for this specific src->dst pair.
        match = parser.OFPMatch(in_port=in_port, eth_src=src_mac, eth_dst=dst_mac)
        self.add_flow(
            datapath, BLOCKED_FLOW_PRIORITY, match, actions=[],
            idle_timeout=BLOCKED_FLOW_IDLE_TIMEOUT,
        )

        # --- Policy 3: Dynamic Quarantine ---
        self.violation_count.setdefault(src_mac, 0)
        self.violation_count[src_mac] += 1
        count = self.violation_count[src_mac]
        self.logger.warning(
            'ATTACKER POLICY-3: Violation count for %s = %d / %d',
            src_mac, count, ATTACKER_QUARANTINE_THRESHOLD,
        )

        if count >= ATTACKER_QUARANTINE_THRESHOLD and not self.quarantined.get(src_mac):
            self.quarantined[src_mac] = True
            # Blanket quarantine: drop ALL traffic from the attacker's MAC
            # regardless of destination, on all ports.
            quarantine_match = parser.OFPMatch(eth_src=src_mac)
            self.add_flow(
                datapath, QUARANTINE_FLOW_PRIORITY, quarantine_match,
                actions=[],  # DROP
            )
            self.logger.critical(
                'ATTACKER QUARANTINED: MAC %s fully blocked at priority %d '
                'after %d violations',
                src_mac, QUARANTINE_FLOW_PRIORITY, count,
            )
