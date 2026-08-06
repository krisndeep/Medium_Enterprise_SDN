# External Attacker Security Policies

## Overview

The attacker host is an **external, untrusted node** that is not part of any enterprise department. It connects to the network through a dedicated switch (`s_atk`, DPID 9) wired to the aggregation switch (`s1`) on **port 8**.

The controller classifies traffic from port 8 as belonging to the `ATTACKER` group and enforces three security policies against it.

---

## What Was Added

### Topology Changes (`topo2.py`)

| Component | Details |
|-----------|---------|
| Switch | `s_atk` (DPID 9) connected to `s1` port 8 |
| Host | `attacker` connected to `s_atk` port 2 |
| QoS | Extended to cover `s1-eth8` |

### Controller Changes (`contr2.py`)

| Item | Details |
|------|---------|
| Port mapping | Port 8 → `ATTACKER` department |
| `ATTACKER_CRITICAL_TARGETS` | `{INFRASTRUCTURE, MANAGEMENT, SERVERS}` |
| `ATTACKER_QUARANTINE_THRESHOLD` | 3 violations |
| `QUARANTINE_FLOW_PRIORITY` | 600 (highest in the network) |
| `pair_allowed()` | Returns `False` for any pair involving `ATTACKER` |
| `_handle_attacker_violation()` | New method for Policy 1/2/3 enforcement |
| `violation_count` dict | Tracks per-MAC violation counts |
| `quarantined` dict | Tracks quarantine state |

---

## Policy 1 — Restricted Access to Critical Infrastructure

**Rule**: The attacker is blocked from communicating with Infrastructure, Management, and Server departments (Database Server, Web Server).

**How it works**: When the controller sees traffic from the ATTACKER group to a critical target, it installs a DROP rule at priority 500 and logs it as `ATTACKER POLICY-1`.

### Scenario

```
attacker (10.0.0.100) → db_serv (10.0.0.201)    ❌ BLOCKED
attacker (10.0.0.100) → web_serv (10.0.0.202)   ❌ BLOCKED
attacker (10.0.0.100) → infra1 (10.0.0.203)     ❌ BLOCKED
attacker (10.0.0.100) → ceo (10.0.0.204)        ❌ BLOCKED
```

**Mininet commands**:

```
mininet> attacker ifconfig attacker-eth0 10.0.0.100/24
mininet> db_serv ifconfig db_serv-eth0 10.0.0.201/24
mininet> web_serv ifconfig web_serv-eth0 10.0.0.202/24
mininet> infra1 ifconfig infra1-eth0 10.0.0.203/24
mininet> ceo ifconfig ceo-eth0 10.0.0.204/24

mininet> attacker ping -c 1 10.0.0.201
# Result: 100% packet loss

mininet> attacker ping -c 1 10.0.0.202
# Result: 100% packet loss
```

**Verified**: ✅ Attacker cannot reach Database Server or Web Server.

---

## Policy 2 — Department Isolation

**Rule**: The attacker is not a member of any department. Communication from the attacker to any protected department is denied.

**How it works**: The `pair_allowed()` function returns `False` whenever either the source or destination group is `ATTACKER`. This prevents lateral movement to HR, SDE, Business, Call Support, or any other department.

### Scenario

```
attacker (10.0.0.100) → hr1 (10.0.0.1)          ❌ BLOCKED
attacker (10.0.0.100) → hr2 (10.0.0.2)          ❌ BLOCKED
attacker (10.0.0.100) → dev1 (10.0.0.3)         ❌ BLOCKED
attacker (10.0.0.100) → bus1 (10.0.0.4)         ❌ BLOCKED
```

**Mininet commands**:

```
mininet> hr1 ifconfig hr1-eth0 10.0.0.1/24
mininet> hr2 ifconfig hr2-eth0 10.0.0.2/24
mininet> dev1 ifconfig dev1-eth0 10.0.0.3/24

mininet> attacker ping -c 1 10.0.0.1
# Result: 100% packet loss

mininet> attacker ping -c 1 10.0.0.3
# Result: 100% packet loss
```

**Verified**: ✅ Attacker cannot reach any department.

---

## Policy 3 — Dynamic Quarantine

**Rule**: After 3 policy violations, the controller installs a blanket DROP rule (priority 600) matching only `eth_src = attacker MAC`. This blocks **all** future traffic from the attacker without needing to inspect each packet.

**How it works**: Each time `_handle_attacker_violation()` is called, the violation counter for the attacker's MAC increments. Once it reaches the threshold (3), a quarantine flow is installed on the aggregation switch.

### Scenario

```
Attempt 1: attacker → db_serv     → violation_count = 1
Attempt 2: attacker → hr1         → violation_count = 2
Attempt 3: attacker → dev1        → violation_count = 3  ← QUARANTINE TRIGGERED

After quarantine:
attacker → ANY host              ❌ Dropped at switch level (never reaches controller)
```

**Quarantine flow rule installed on s1**:

```
priority=600, eth_src=<attacker_mac>, actions=DROP
```

**Verify with**:

```
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1 | grep priority=600
```

---

## Test Results Summary

All 5 ping tests from the attacker were blocked:

| # | Command | Target Dept | Result | Policy |
|---|---------|-------------|--------|--------|
| 1 | `attacker ping -c 1 10.0.0.201` | SERVERS | 100% loss | Policy 1 |
| 2 | `attacker ping -c 1 10.0.0.202` | SERVERS | 100% loss | Policy 1 |
| 3 | `attacker ping -c 1 10.0.0.1` | HR | 100% loss | Policy 2 |
| 4 | `attacker ping -c 1 10.0.0.2` | HR | 100% loss | Policy 2 |
| 5 | `attacker ping -c 1 10.0.0.1` | HR | 100% loss | Policy 2 |

Controller log entries confirming the blocks:

```
BLOCKED communication: SERVERS -> ATTACKER (dpid=1 in_port=7 src=00:00:00:00:00:0f dst=00:00:00:00:00:11)
BLOCKED communication: SERVERS -> ATTACKER (dpid=1 in_port=7 src=00:00:00:00:00:10 dst=00:00:00:00:00:11)
BLOCKED communication: HR -> ATTACKER (dpid=1 in_port=1 src=00:00:00:00:00:01 dst=00:00:00:00:00:11)
BLOCKED communication: HR -> ATTACKER (dpid=1 in_port=1 src=00:00:00:00:00:02 dst=00:00:00:00:00:11)
BLOCKED communication: HR -> ATTACKER (dpid=1 in_port=1 src=00:00:00:00:00:01 dst=00:00:00:00:00:11)
```

---

## Configuration

These constants in `contr2.py` can be tuned:

| Constant | Default | Purpose |
|----------|---------|---------|
| `ATTACKER_CRITICAL_TARGETS` | `{INFRASTRUCTURE, MANAGEMENT, SERVERS}` | Departments protected by Policy 1 |
| `ATTACKER_QUARANTINE_THRESHOLD` | `3` | Violations before full quarantine |
| `QUARANTINE_FLOW_PRIORITY` | `600` | Priority of quarantine DROP rule |
| `BLOCKED_FLOW_PRIORITY` | `500` | Priority of per-pair DROP rules |
| `BLOCKED_FLOW_IDLE_TIMEOUT` | `30` | Seconds before idle per-pair DROP rules expire |
