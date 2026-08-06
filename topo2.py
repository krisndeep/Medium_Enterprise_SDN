#!/usr/bin/env python3
"""
topology.py

Enterprise SDN Topology for Mininet.

Builds a single aggregation switch (s1) with seven departmental OpenFlow
switches attached to it, each hosting the machines for that department.
The network is driven entirely by an external Ryu OpenFlow 1.3 controller
(enterprise_controller.py) -- this script only builds the topology and
performs the *static* configuration that cannot be done from the
controller: VLAN tagging on access ports and Linux-HTB QoS queues on the
aggregation switch's interfaces.

Aggregation switch (s1) port map (this MUST stay in sync with the
AGGREGATION_DPID / port-to-department mapping used in the controller):

    Port 1 -> HR switch
    Port 2 -> Software Development (SDE) switch
    Port 3 -> Business switch
    Port 4 -> Call Support switch
    Port 5 -> Management switch
    Port 6 -> Infrastructure switch
    Port 7 -> Servers switch
    Port 8 -> Attacker switch (external / untrusted)

No static IP addresses are assigned anywhere in this topology -- hosts
receive addresses dynamically (Mininet's default DHCP-less "no fixed IP"
behaviour is fine here since the controller never inspects IPs for
classification; if the user wants live IP connectivity they can run
`dhclient` or `ifconfig <intf> <ip>` manually from the CLI).
"""

import sys

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info


# --------------------------------------------------------------------------
# Controller connection parameters
# --------------------------------------------------------------------------
CONTROLLER_IP = '127.0.0.1'
PRIMARY_CONTROLLER_PORT = 6653
STANDBY_CONTROLLER_PORT = 6654

# --------------------------------------------------------------------------
# VLAN configuration (Management department only)
# --------------------------------------------------------------------------
VLAN_UPPER_MGMT = 50
VLAN_LOWER_MGMT = 60

# --------------------------------------------------------------------------
# QoS configuration
#
# Queue IDs here MUST exactly match the queue IDs used by
# enterprise_controller.py (get_queue()).
#
# Each entry: queue_id -> (min_rate_bps, max_rate_bps, htb_priority)
# Lower htb_priority value == served first (strict priority within HTB).
# Parent (aggregation switch interface) link is capped at 100 Mbps.
# --------------------------------------------------------------------------
LINK_MAX_RATE = 100_000_000  # 100 Mbps

QUEUE_CONFIG = {
    7: (20_000_000, 100_000_000, 0),   # Upper Management VoIP  - highest
    6: (15_000_000, 100_000_000, 1),   # Infrastructure
    5: (12_000_000, 100_000_000, 2),   # Call Support
    4: (10_000_000, 100_000_000, 3),   # Software Development
    3: (10_000_000, 100_000_000, 4),   # Business
    2: (10_000_000, 100_000_000, 5),   # Upper Management (non-VoIP)
    1: (8_000_000,  100_000_000, 6),   # Lower Management
    0: (5_000_000,  100_000_000, 7),   # HR - lowest
}


def build_network():
    """Construct and return the Mininet network object (not yet started)."""

    net = Mininet(
        controller=None,      # controller added explicitly below
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
        build=False,
    )

    info('*** Adding remote Ryu controller\n')
    net.addController(
        'c0',
        controller=RemoteController,
        ip=CONTROLLER_IP,
        port=PRIMARY_CONTROLLER_PORT,
    )
    net.addController(
        'c1',
        controller=RemoteController,
        ip=CONTROLLER_IP,
        port=STANDBY_CONTROLLER_PORT,
    )

    info('*** Adding aggregation switch\n')
    # The aggregation switch is DPID 1. All department switches connect
    # into it on ports 1-7 (explicitly pinned below via port1=/port2=).
    s1 = net.addSwitch('s1', dpid='0000000000000001', protocols='OpenFlow13')

    info('*** Adding departmental switches\n')
    s_hr = net.addSwitch('s_hr', dpid='0000000000000002', protocols='OpenFlow13')
    s_dev = net.addSwitch('s_dev', dpid='0000000000000003', protocols='OpenFlow13')
    s_bus = net.addSwitch('s_bus', dpid='0000000000000004', protocols='OpenFlow13')
    s_call = net.addSwitch('s_call', dpid='0000000000000005', protocols='OpenFlow13')
    s_mgmt = net.addSwitch('s_mgmt', dpid='0000000000000006', protocols='OpenFlow13')
    s_infra = net.addSwitch('s_infra', dpid='0000000000000007', protocols='OpenFlow13')
    s_srv = net.addSwitch('s_srv', dpid='0000000000000008', protocols='OpenFlow13')
    s_atk = net.addSwitch('s_atk', dpid='0000000000000009', protocols='OpenFlow13')

    info('*** Wiring aggregation switch to department switches\n')
    # Ports on s1 are pinned explicitly (1-8) so the controller's
    # DPID/port -> department mapping is guaranteed to hold.
    net.addLink(s1, s_hr, port1=1, port2=1)
    net.addLink(s1, s_dev, port1=2, port2=1)
    net.addLink(s1, s_bus, port1=3, port2=1)
    net.addLink(s1, s_call, port1=4, port2=1)
    net.addLink(s1, s_mgmt, port1=5, port2=1)
    net.addLink(s1, s_infra, port1=6, port2=1)
    net.addLink(s1, s_srv, port1=7, port2=1)
    net.addLink(s1, s_atk, port1=8, port2=1)

    # ----------------------------------------------------------------
    # HR department
    # ----------------------------------------------------------------
    info('*** Adding HR hosts\n')
    hr1 = net.addHost('hr1')
    hr2 = net.addHost('hr2')
    net.addLink(s_hr, hr1, port1=2, port2=0)
    net.addLink(s_hr, hr2, port1=3, port2=0)

    # ----------------------------------------------------------------
    # Software Development department
    # ----------------------------------------------------------------
    info('*** Adding Software Development hosts\n')
    dev1 = net.addHost('dev1')
    dev2 = net.addHost('dev2')
    net.addLink(s_dev, dev1, port1=2, port2=0)
    net.addLink(s_dev, dev2, port1=3, port2=0)

    # ----------------------------------------------------------------
    # Business department
    # ----------------------------------------------------------------
    info('*** Adding Business hosts\n')
    bus1 = net.addHost('bus1')
    bus2 = net.addHost('bus2')
    net.addLink(s_bus, bus1, port1=2, port2=0)
    net.addLink(s_bus, bus2, port1=3, port2=0)

    # ----------------------------------------------------------------
    # Call Support department
    # ----------------------------------------------------------------
    info('*** Adding Call Support hosts\n')
    exec1 = net.addHost('exec1')
    support1 = net.addHost('support1')
    support2 = net.addHost('support2')
    net.addLink(s_call, exec1, port1=2, port2=0)
    net.addLink(s_call, support1, port1=3, port2=0)
    net.addLink(s_call, support2, port1=4, port2=0)

    # ----------------------------------------------------------------
    # Management department (Upper + Lower, VLAN separated)
    # ----------------------------------------------------------------
    info('*** Adding Management hosts\n')
    ceo = net.addHost('ceo')
    director = net.addHost('director')
    manager1 = net.addHost('manager1')
    manager2 = net.addHost('manager2')
    # Ports pinned so we can reliably compute the switch-side interface
    # names below for VLAN tagging (s_mgmt-eth2 .. s_mgmt-eth5).
    net.addLink(s_mgmt, ceo, port1=2, port2=0)
    net.addLink(s_mgmt, director, port1=3, port2=0)
    net.addLink(s_mgmt, manager1, port1=4, port2=0)
    net.addLink(s_mgmt, manager2, port1=5, port2=0)

    # ----------------------------------------------------------------
    # Infrastructure department
    # ----------------------------------------------------------------
    info('*** Adding Infrastructure host\n')
    infra1 = net.addHost('infra1')
    net.addLink(s_infra, infra1, port1=2, port2=0)

    # ----------------------------------------------------------------
    # Servers department
    # ----------------------------------------------------------------
    info('*** Adding Server hosts\n')
    db_serv = net.addHost('db_serv')
    web_serv = net.addHost('web_serv')
    net.addLink(s_srv, db_serv, port1=2, port2=0)
    net.addLink(s_srv, web_serv, port1=3, port2=0)

    # ----------------------------------------------------------------
    # External Attacker (untrusted / outside all departments)
    # ----------------------------------------------------------------
    info('*** Adding Attacker host\n')
    attacker = net.addHost('attacker')
    net.addLink(s_atk, attacker, port1=2, port2=0)

    return net


def configure_management_vlans(net):
    """
    Configure the Management switch's host-facing ports as VLAN access
    ports using ovs-vsctl. This is the ONLY switch in the topology that
    uses VLANs.

        ceo, director   -> VLAN 50 (Upper Management)
        manager1, manager2 -> VLAN 60 (Lower Management)

    The uplink port from s_mgmt toward s1 is left untouched (default
    trunk behaviour), so tagged frames pass through to the aggregation
    switch unmodified, where the controller inspects the VLAN ID.
    """
    info('*** Configuring Management VLANs via ovs-vsctl\n')

    vlan_assignments = {
        's_mgmt-eth2': VLAN_UPPER_MGMT,   # ceo
        's_mgmt-eth3': VLAN_UPPER_MGMT,   # director
        's_mgmt-eth4': VLAN_LOWER_MGMT,   # manager1
        's_mgmt-eth5': VLAN_LOWER_MGMT,   # manager2
    }

    s_mgmt = net.get('s_mgmt')
    for intf_name, vlan_id in vlan_assignments.items():
        cmd = 'ovs-vsctl set port {intf} tag={vlan}'.format(
            intf=intf_name, vlan=vlan_id
        )
        info('    {}\n'.format(cmd))
        s_mgmt.cmd(cmd)


def configure_aggregation_qos(net):
    """
    Configure Linux-HTB QoS queues on every aggregation switch (s1)
    interface using ovs-vsctl. Queue IDs must exactly match the queue
    IDs referenced by enterprise_controller.py's get_queue().

    Each of s1's seven department-facing interfaces (s1-eth1 .. s1-eth7)
    gets its own QoS record containing the same 8 HTB queues (0-7), so
    that regardless of which department's traffic is egressing through a
    given port, the correct queue/priority is available.
    """
    info('*** Configuring aggregation switch QoS (HTB queues) via ovs-vsctl\n')

    s1 = net.get('s1')
    aggregation_ports = [
        's1-eth1', 's1-eth2', 's1-eth3', 's1-eth4',
        's1-eth5', 's1-eth6', 's1-eth7', 's1-eth8',
    ]

    for intf_name in aggregation_ports:
        # Build the "ovs-vsctl -- --id=@qN create queue ... -- ... create qos
        # ... -- set port <intf> qos=@newqos" command in one shot so that all
        # referenced queue records exist before the qos record is created.
        cmd_parts = ['ovs-vsctl']

        queue_ids = sorted(QUEUE_CONFIG.keys())
        queues_clause = []
        for qid in queue_ids:
            min_rate, max_rate, htb_priority = QUEUE_CONFIG[qid]
            cmd_parts.append('--')
            cmd_parts.append('--id=@q{qid}'.format(qid=qid))
            cmd_parts.append('create')
            cmd_parts.append('queue')
            cmd_parts.append('other-config:min-rate={}'.format(min_rate))
            cmd_parts.append('other-config:max-rate={}'.format(max_rate))
            cmd_parts.append('other-config:priority={}'.format(htb_priority))
            queues_clause.append('{qid}=@q{qid}'.format(qid=qid))

        cmd_parts.append('--')
        cmd_parts.append('--id=@newqos')
        cmd_parts.append('create')
        cmd_parts.append('qos')
        cmd_parts.append('type=linux-htb')
        cmd_parts.append('other-config:max-rate={}'.format(LINK_MAX_RATE))
        cmd_parts.append('queues={}'.format(','.join(queues_clause)))

        cmd_parts.append('--')
        cmd_parts.append('set')
        cmd_parts.append('port')
        cmd_parts.append(intf_name)
        cmd_parts.append('qos=@newqos')

        cmd = ' '.join(cmd_parts)
        info('    Configuring QoS on {}\n'.format(intf_name))
        s1.cmd(cmd)


def main():
    setLogLevel('info')

    net = build_network()

    info('*** Starting network\n')
    net.build()
    net.start()

    # Static, one-time configuration that cannot be expressed through
    # OpenFlow flow rules: VLAN tagging and HTB queue provisioning.
    configure_management_vlans(net)
    configure_aggregation_qos(net)

    info('*** Enterprise SDN topology is up. Dropping into Mininet CLI.\n')
    CLI(net)

    info('*** Stopping network\n')
    net.stop()


if __name__ == '__main__':
    sys.exit(main())