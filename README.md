<<<<<<< HEAD
# Medium Enterprise SDN

A Ryu-based OpenFlow 1.3 controller and Mininet topology for a medium-scale software development enterprise network. The project demonstrates centralized network policy enforcement, QoS differentiation, VLAN segmentation, controller failover, and dynamic attacker quarantine — all driven entirely through SDN.

---

## Table of Contents

- [Architecture](#architecture)
- [Network Topology](#network-topology)
- [Department Classification](#department-classification)
- [VLAN Configuration](#vlan-configuration)
- [QoS Queue Hierarchy](#qos-queue-hierarchy)
- [Inter-Department Communication Policy](#inter-department-communication-policy)
- [External Attacker Security Policies](#external-attacker-security-policies)
- [Controller Failover](#controller-failover)
- [Prerequisites](#prerequisites)
- [How to Run](#how-to-run)
- [Simulation & Testing](#simulation--testing)
- [File Structure](#file-structure)
- [Configuration Reference](#configuration-reference)

---

## Architecture

The system consists of two components:

| Component | File | Role |
|-----------|------|------|
| **Controller** | `contr2.py` | Ryu OpenFlow 1.3 application — MAC learning, department classification, policy enforcement, QoS assignment, attacker quarantine |
| **Topology** | `topo2.py` | Mininet script — builds the switch/host network, configures VLANs, provisions HTB QoS queues |

All classification and policy decisions are based on **switch DPID**, **ingress port**, **VLAN ID**, and **MAC addresses** — never on IP addresses.

---

## Network Topology

The network uses a **hub-and-spoke** design with a single aggregation switch (`s1`, DPID 1) at the center, connected to eight departmental switches.

```
                            ┌──────────────┐
                            │  Ryu Controller  │
                            │  (c0: 6653)      │
                            │  (c1: 6654)      │
                            └───────┬──────────┘
                                    │ OpenFlow 1.3
                    ┌───────────────┼───────────────────┐
                    │         s1 (DPID 1)               │
                    │       Aggregation Switch           │
                    │  Ports: 1  2  3  4  5  6  7  8    │
                    └──┬──┬──┬──┬──┬──┬──┬──┬───────────┘
                       │  │  │  │  │  │  │  │
            ┌──────────┘  │  │  │  │  │  │  └──────────┐
            │     ┌───────┘  │  │  │  │  └────────┐    │
            │     │    ┌─────┘  │  │  └──────┐    │    │
            │     │    │   ┌────┘  └───┐     │    │    │
            ▼     ▼    ▼   ▼          ▼     ▼    ▼    ▼
          s_hr  s_dev s_bus s_call  s_mgmt s_infra s_srv s_atk
         (DP2) (DP3) (DP4) (DP5)  (DP6)  (DP7)  (DP8) (DP9)
           │     │     │     │       │      │      │      │
          hr1  dev1  bus1  exec1   ceo    infra1 db_serv attacker
          hr2  dev2  bus2  support1 director     web_serv
                           support2 manager1
                                    manager2
```

### Aggregation Switch Port Map

| Port | Department Switch | DPID | Department |
|------|-------------------|------|------------|
| 1 | `s_hr` | 2 | HR |
| 2 | `s_dev` | 3 | Software Development (SDE) |
| 3 | `s_bus` | 4 | Business |
| 4 | `s_call` | 5 | Call Support |
| 5 | `s_mgmt` | 6 | Management |
| 6 | `s_infra` | 7 | Infrastructure |
| 7 | `s_srv` | 8 | Servers |
| 8 | `s_atk` | 9 | Attacker (External / Untrusted) |

### Host Inventory

| Host | Department | Switch Port |
|------|------------|-------------|
| `hr1`, `hr2` | HR | `s_hr` ports 2–3 |
| `dev1`, `dev2` | Software Development | `s_dev` ports 2–3 |
| `bus1`, `bus2` | Business | `s_bus` ports 2–3 |
| `exec1`, `support1`, `support2` | Call Support | `s_call` ports 2–4 |
| `ceo`, `director` | Management (Upper, VLAN 50) | `s_mgmt` ports 2–3 |
| `manager1`, `manager2` | Management (Lower, VLAN 60) | `s_mgmt` ports 4–5 |
| `infra1` | Infrastructure | `s_infra` port 2 |
| `db_serv`, `web_serv` | Servers | `s_srv` ports 2–3 |
| `attacker` | Attacker (Untrusted) | `s_atk` port 2 |

---

## Department Classification

The controller classifies traffic **exclusively** by the ingress port on the aggregation switch (`s1`, DPID 1). When a packet arrives on port *N* of `s1`, it is tagged with the department corresponding to that port (see port map above).

This approach avoids reliance on IP addresses and works at Layer 2.

---

## VLAN Configuration

VLANs are used **only** within the Management department to distinguish Upper and Lower management tiers.

| VLAN ID | Tier | Hosts | Switch Ports |
|---------|------|-------|--------------|
| 50 | Upper Management | `ceo`, `director` | `s_mgmt-eth2`, `s_mgmt-eth3` |
| 60 | Lower Management | `manager1`, `manager2` | `s_mgmt-eth4`, `s_mgmt-eth5` |

VLAN tags are applied as **access port** configuration on the management switch via `ovs-vsctl`. The uplink to `s1` remains a trunk, so tagged frames pass through to the aggregation switch where the controller inspects the VLAN ID.

---

## QoS Queue Hierarchy

Linux-HTB QoS queues are provisioned on **every** aggregation switch interface (`s1-eth1` through `s1-eth8`). The parent link is capped at 100 Mbps.

| Queue ID | Department / Traffic | Min Rate | Max Rate | HTB Priority | Flow Priority |
|----------|---------------------|----------|----------|--------------|---------------|
| 7 | Upper Management VoIP | 20 Mbps | 100 Mbps | 0 (highest) | 400 |
| 6 | Infrastructure | 15 Mbps | 100 Mbps | 1 | 300 |
| 5 | Call Support | 12 Mbps | 100 Mbps | 2 | 290 |
| 4 | Software Development | 10 Mbps | 100 Mbps | 3 | 280 |
| 3 | Business | 10 Mbps | 100 Mbps | 4 | 270 |
| 2 | Upper Management (non-VoIP) | 10 Mbps | 100 Mbps | 5 | 260 |
| 1 | Lower Management | 8 Mbps | 100 Mbps | 6 | 250 |
| 0 | HR | 5 Mbps | 100 Mbps | 7 (lowest) | 240 |

### VoIP Detection

The controller detects VoIP traffic from Upper Management hosts by inspecting UDP port numbers:

- **SIP**: UDP port 5060
- **RTP**: UDP ports 16384–32767

When detected, traffic is assigned to Queue 7 (highest priority, flow priority 400).

---

## Inter-Department Communication Policy

The controller enforces the following communication rules on the aggregation switch:

### Universal Access Departments

These departments can communicate with **every** other department:

- Management
- Infrastructure
- Servers

### Blocked Pairs

The following department pairs are **explicitly forbidden** from communicating:

| Department A | Department B | Result |
|--------------|--------------|--------|
| SDE | Business | ❌ BLOCKED |
| SDE | Call Support | ❌ BLOCKED |
| Business | Call Support | ❌ BLOCKED |

### Same-Department Communication

Hosts within the same department can always communicate with each other.

### Enforcement

When a blocked communication is detected, the controller installs a **DROP flow** at priority 500 with a 30-second idle timeout on the aggregation switch.

---

## External Attacker Security Policies

The attacker host is an **external, untrusted node** classified independently from all enterprise departments. Three security policies are enforced against it:

### Policy 1 — Restricted Access to Critical Infrastructure

The attacker is prohibited from communicating with critical enterprise systems:

- Infrastructure Department
- Management Department
- Server Department (Database Server, Web Server)

When such communication is detected, a high-priority DROP rule (priority 500) is installed and the event is logged as `ATTACKER POLICY-1`.

```
Attacker → Database Server
        ↓
Controller detects policy violation
        ↓
DROP flow installed (priority 500)
```

### Policy 2 — Department Isolation

The attacker is **not a member of any department**. Communication from the attacker to any protected department is denied by default. This prevents unauthorized lateral movement throughout the enterprise network.

The `pair_allowed()` function returns `False` whenever either the source or destination is classified as `ATTACKER`.

### Policy 3 — Dynamic Quarantine

Repeated policy violations result in automatic quarantine. The controller maintains a per-MAC violation counter for the attacker.

```
Violation 1 → DROP for specific src/dst pair
Violation 2 → DROP for specific src/dst pair
Violation 3 → QUARANTINE: blanket DROP for ALL traffic from attacker MAC
```

Once the configured threshold (default: 3) is exceeded, the controller installs a **priority 600** DROP rule matching only the attacker's source MAC address. This blocks all future traffic from the attacker without per-packet inspection.

```
Priority 600
Match:    eth_src = <attacker_mac>
Action:   DROP
```

### Priority Hierarchy

| Priority | Rule Type |
|----------|-----------|
| 600 | Attacker quarantine (blanket DROP) |
| 500 | Per-pair policy violation DROP |
| 240–400 | QoS forwarding flows |
| 10 | Non-aggregation L2 forwarding |
| 0 | Table-miss (send to controller) |

---

## Controller Failover

The system supports a **primary/standby** controller architecture for high availability.

| Controller | Port | OpenFlow Role |
|------------|------|---------------|
| `c0` (Primary) | 6653 | MASTER |
| `c1` (Standby) | 6654 | SLAVE |

### How It Works

1. Both controllers connect to every switch.
2. The standby controller runs a health-check loop (every 2 seconds) that attempts a TCP connection to the primary controller.
3. If **3 consecutive** health checks fail, the standby promotes itself to MASTER by sending `OFPCR_ROLE_MASTER` role requests to all connected switches.
4. The controller role is set via the `RYU_ROLE` environment variable (`primary` or `standby`).

### Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `RYU_ROLE` | `primary` | Controller role (`primary` or `standby`) |
| `RYU_PEER_IP` | `127.0.0.1` | IP address of the peer controller |
| `RYU_PEER_PORT` | `6653` | Port of the peer controller |

---

## Prerequisites

| Software | Version | Purpose |
|----------|---------|---------|
| Python | 3.8+ | Runtime |
| [Mininet](http://mininet.org/) | 2.3+ | Network emulation |
| [Ryu](https://ryu-sdn.org/) | 4.34+ | SDN controller framework |
| [Open vSwitch](https://www.openvswitch.org/) | 2.13+ | OpenFlow switch implementation |

### Installation (Ubuntu/Debian)

```bash
# System packages
sudo apt update
sudo apt install -y mininet openvswitch-switch

# Ryu controller (in a virtualenv recommended)
python3 -m venv ryu-env
source ryu-env/bin/activate
pip install ryu
```

---

## How to Run

### 1. Start the Ryu Controller

```bash
# Primary controller
ryu-manager contr2.py --verbose
```

For failover testing, start a standby in a separate terminal:

```bash
RYU_ROLE=standby RYU_PEER_IP=127.0.0.1 RYU_PEER_PORT=6653 \
  ryu-manager contr2.py --ofp-tcp-listen-port 6654 --verbose
```

### 2. Start the Mininet Topology

In a separate terminal:

```bash
sudo python3 topo2.py
```

Wait for the `mininet>` prompt. The topology will automatically:
- Build all switches and hosts
- Configure Management VLANs
- Provision QoS queues on the aggregation switch

### 3. Assign IP Addresses

Mininet does not auto-assign IPs. Assign them manually:

```bash
mininet> hr1 ifconfig hr1-eth0 10.0.0.1/24
mininet> hr2 ifconfig hr2-eth0 10.0.0.2/24
mininet> dev1 ifconfig dev1-eth0 10.0.0.3/24
mininet> dev2 ifconfig dev2-eth0 10.0.0.4/24
mininet> bus1 ifconfig bus1-eth0 10.0.0.5/24
mininet> bus2 ifconfig bus2-eth0 10.0.0.6/24
mininet> exec1 ifconfig exec1-eth0 10.0.0.7/24
mininet> support1 ifconfig support1-eth0 10.0.0.8/24
mininet> support2 ifconfig support2-eth0 10.0.0.9/24
mininet> ceo ifconfig ceo-eth0 10.0.0.10/24
mininet> director ifconfig director-eth0 10.0.0.11/24
mininet> manager1 ifconfig manager1-eth0 10.0.0.12/24
mininet> manager2 ifconfig manager2-eth0 10.0.0.13/24
mininet> infra1 ifconfig infra1-eth0 10.0.0.14/24
mininet> db_serv ifconfig db_serv-eth0 10.0.0.201/24
mininet> web_serv ifconfig web_serv-eth0 10.0.0.202/24
mininet> attacker ifconfig attacker-eth0 10.0.0.100/24
```

---

## Simulation & Testing

### Test 1: Intra-Department Communication (Should Pass)

```bash
mininet> hr1 ping -c 2 10.0.0.2         # hr1 → hr2 (same dept) ✅
mininet> dev1 ping -c 2 10.0.0.4         # dev1 → dev2 (same dept) ✅
```

### Test 2: Universal Department Access (Should Pass)

```bash
mininet> infra1 ping -c 2 10.0.0.1       # Infrastructure → HR ✅
mininet> ceo ping -c 2 10.0.0.3          # Management → SDE ✅
mininet> db_serv ping -c 2 10.0.0.5      # Servers → Business ✅
```

### Test 3: Blocked Department Pairs (Should Fail)

```bash
mininet> dev1 ping -c 2 10.0.0.5         # SDE → Business ❌
mininet> bus1 ping -c 2 10.0.0.7         # Business → Call Support ❌
mininet> dev1 ping -c 2 10.0.0.7         # SDE → Call Support ❌
```

### Test 4: Attacker Policy 1 — Critical Infrastructure (Should Fail)

```bash
mininet> attacker ping -c 1 10.0.0.201   # Attacker → DB Server ❌
mininet> attacker ping -c 1 10.0.0.202   # Attacker → Web Server ❌
mininet> attacker ping -c 1 10.0.0.14    # Attacker → Infrastructure ❌
mininet> attacker ping -c 1 10.0.0.10    # Attacker → CEO ❌
```

### Test 5: Attacker Policy 2 — Department Isolation (Should Fail)

```bash
mininet> attacker ping -c 1 10.0.0.1     # Attacker → HR ❌
mininet> attacker ping -c 1 10.0.0.3     # Attacker → SDE ❌
mininet> attacker ping -c 1 10.0.0.5     # Attacker → Business ❌
```

### Test 6: Attacker Policy 3 — Dynamic Quarantine

After 3+ failed attempts, verify the quarantine flow:

```bash
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1 | grep priority=600
```

### Inspect Flow Tables

```bash
# View all flows on the aggregation switch
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1

# View flows on a specific department switch
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s_hr
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s_atk
```

---

## File Structure

```
Medium_Enterprise_SDN/
├── contr2.py              # Ryu OpenFlow 1.3 controller application
├── topo2.py               # Mininet topology builder
├── ATTACKER_POLICIES.md   # Detailed attacker policy documentation & test results
└── README.md              # This file
```

---

## Configuration Reference

### Controller Constants (`contr2.py`)

| Constant | Value | Description |
|----------|-------|-------------|
| `AGGREGATION_DPID` | `1` | DPID of the aggregation switch |
| `VLAN_UPPER_MGMT` | `50` | VLAN ID for Upper Management |
| `VLAN_LOWER_MGMT` | `60` | VLAN ID for Lower Management |
| `SIP_PORT` | `5060` | SIP signaling port for VoIP detection |
| `RTP_PORT_MIN` | `16384` | RTP port range start |
| `RTP_PORT_MAX` | `32767` | RTP port range end |
| `BLOCKED_FLOW_PRIORITY` | `500` | Priority for policy violation DROP flows |
| `BLOCKED_FLOW_IDLE_TIMEOUT` | `30` | Idle timeout (seconds) for DROP flows |
| `QUARANTINE_FLOW_PRIORITY` | `600` | Priority for attacker quarantine DROP |
| `ATTACKER_QUARANTINE_THRESHOLD` | `3` | Violations before full quarantine |
| `ATTACKER_CRITICAL_TARGETS` | `{INFRASTRUCTURE, MANAGEMENT, SERVERS}` | Departments protected under Policy 1 |
| `HEALTH_CHECK_INTERVAL` | `2` | Controller liveness check interval (seconds) |
| `FAILS_BEFORE_PROMOTION` | `3` | Failed checks before standby promotion |

### Topology Constants (`topo2.py`)

| Constant | Value | Description |
|----------|-------|-------------|
| `CONTROLLER_IP` | `127.0.0.1` | Controller address |
| `PRIMARY_CONTROLLER_PORT` | `6653` | Primary controller OpenFlow port |
| `STANDBY_CONTROLLER_PORT` | `6654` | Standby controller OpenFlow port |
| `LINK_MAX_RATE` | `100,000,000` | Parent link rate (100 Mbps) |
=======
# Medium_Enterprise_SDN
A Ryu based controller implemenation for a medium scale software development enterprise network.

To run primary controller
```RYU_ROLE=primary ryu-manager --ofp-tcp-listen-port 6653 contr2.py```
To run standby controller
```RYU_ROLE=standby RYU_PEER_IP=127.0.0.1 RYU_PEER_PORT=6653 ryu-manager --ofp-tcp-listen-port 6654 contr.py```
To run mininet topo
```sudo python3 topo2.py```
>>>>>>> 9289a56853ac878f185f475c3a284646a3063012
