#!/usr/bin/env python3
"""
qos_before_after.py

Runs the exact same 8-department concurrent UDP contention test TWICE
against the SAME running topology instance -- no restart in between --
with the SAME total link capacity (100 Mbps) enforced in both phases,
so the ONLY variable that changes is whether that capacity is divided
into 8 prioritized HTB classes or left as one flat, undifferentiated
queue.

  Phase 1 ("before"): a plain TBF (Token Bucket Filter) caps s1's
                       Servers-facing interface at 100 Mbps -- one flat
                       pipe, first-come-first-served, no department
                       priority at all. This is what "no QoS" actually
                       means on a real network with a real bandwidth
                       limit (as opposed to Mininet's effectively-
                       uncapped default virtual link speed, which isn't
                       a meaningful baseline to compare against).

  Phase 2 ("after"):  the flat TBF is removed and configure_aggregation_qos()
                       provisions the real 8-class HTB setup from
                       Topo2.py's QUEUE_CONFIG on the same interface,
                       same 100 Mbps total cap. The IDENTICAL test is
                       re-run. Because iperf assigns a fresh ephemeral
                       source port on every invocation, Phase 2's flows
                       are genuinely new 5-tuples as far as the flow
                       table is concerned, so the controller
                       re-classifies them fresh.

Each flow offers 20 Mbps (160 Mbps aggregate demand vs. the 100 Mbps
cap -- a believable ~1.6x oversubscription, enough to force real
scheduling decisions without every flow collapsing to near-total loss
regardless of priority).

Usage:
    Run this INSTEAD of topology.py for this experiment (it builds and
    starts the network itself, using the same build_network() /
    configure_management_vlans() / configure_aggregation_qos() from
    topology.py -- make sure topology.py is in the same directory, or
    edit the import below to match its actual filename).

        sudo python3 qos_before_after.py

    The Ryu controller (enterprise_controller.py) must already be
    running separately, same as normal:

        ryu-manager enterprise_controller.py --verbose

Produces:
    qos_results_before.csv
    qos_results_after.csv

Then run plot_qos_comparison.py (outside Mininet) to generate graphs.
"""

import csv
import re
import sys
import time

from mininet.log import setLogLevel, info

# Matches the user's actual topology filename: Topo2.py
from Topo2 import (
    build_network,
    configure_management_vlans,
    configure_aggregation_qos,
)


# ============================================================
# Test configuration -- identical departmental contention test used
# throughout this project, just labelled per-phase so before/after
# never overwrite each other's log files or results.
# ============================================================

TEST_DURATION = 30
OFFERED_RATE = 20  # Mbps offered per flow.
#
# Lowered from 100 -> 20. With 8 flows this is 160 Mbps aggregate demand
# against a 100 Mbps cap (~1.6x oversubscription) -- enough real
# contention to force meaningful scheduling decisions, without being so
# extreme (800 Mbps vs 100 Mbps) that literally every flow collapses to
# near-100% loss regardless of priority, which drowns out the actual
# before/after story.

# The SAME 100 Mbps cap applied to s1's Servers-facing interface in
# BOTH phases -- must match LINK_MAX_RATE in Topo2.py's QUEUE_CONFIG
# setup, so "before" and "after" differ only in whether that capacity
# is divided into 8 prioritized classes or left as one flat queue.
BASELINE_CAP_MBIT = 100
BOTTLENECK_INTERFACE = 's1-eth7'  # s1 <-> s_srv (web_serv's uplink)

TESTS = [
    {"queue": 0, "name": "HR",                   "host": "hr1",      "port": 5000, "type": "normal"},
    {"queue": 1, "name": "Lower Management",      "host": "manager1", "port": 5001, "type": "normal"},
    {"queue": 2, "name": "Upper Management",      "host": "ceo",      "port": 5002, "type": "normal"},
    {"queue": 3, "name": "Business",              "host": "bus1",     "port": 5003, "type": "normal"},
    {"queue": 4, "name": "SDE",                   "host": "dev1",     "port": 5004, "type": "normal"},
    {"queue": 5, "name": "Call Support",          "host": "support1", "port": 5005, "type": "normal"},
    {"queue": 6, "name": "Infrastructure",        "host": "infra1",   "port": 5006, "type": "normal"},
    {"queue": 7, "name": "Upper Management VoIP", "host": "ceo",      "port": 20000, "type": "voip"},
]

MINIMUM_RATES = {0: 5, 1: 8, 2: 10, 3: 10, 4: 10, 5: 12, 6: 15, 7: 20}

CSV_FIELDS = [
    'phase', 'queue_id', 'queue_name', 'source_host', 'destination_host',
    'minimum_rate_mbps', 'offered_rate_mbps', 'throughput_mbps',
    'packet_loss_percent', 'latency_ms',
]


def apply_flat_baseline(net):
    """Apply a single, undifferentiated 100 Mbps rate limit on the real
    bottleneck interface (s1's link to web_serv) -- ONE OVS queue, no
    priority classes -- using ovs-vsctl, the exact same mechanism
    configure_aggregation_qos() uses for the real 8-queue setup, just
    with a single flat queue instead of 8.

    Deliberately does NOT use raw kernel `tc` commands here. OVS expects
    to be the sole owner of an interface's queueing state; mixing a
    manually-added tc qdisc with OVS's own ovs-vsctl-managed QoS on the
    same interface leaves OVS's internal bookkeeping inconsistent once
    the real HTB config replaces this baseline -- which is what caused
    several queues to silently drop ALL traffic in the previous run.
    Keeping everything on the ovs-vsctl side (both here and in the real
    config) means there's only ever one system managing this interface.
    """
    s1 = net.get('s1')
    rate_bps = BASELINE_CAP_MBIT * 1_000_000
    info('*** Applying flat %d Mbit baseline (single OVS queue, no priority) on %s\n'
         % (BASELINE_CAP_MBIT, BOTTLENECK_INTERFACE))
    cmd = (
        'ovs-vsctl '
        '-- --id=@q0 create queue other-config:min-rate={rate} '
        'other-config:max-rate={rate} other-config:priority=0 '
        '-- --id=@newqos create qos type=linux-htb '
        'other-config:max-rate={rate} queues:0=@q0 '
        '-- set port {intf} qos=@newqos'
    ).format(rate=rate_bps, intf=BOTTLENECK_INTERFACE)
    s1.cmd(cmd)


def remove_flat_baseline(net):
    """Fully wipe the baseline QoS/queue records at the OVSDB level
    (not just unset the port's qos pointer) so configure_aggregation_qos()
    starts from a genuinely clean slate on this interface -- no manual
    tc commands involved anywhere in this handoff."""
    s1 = net.get('s1')
    info('*** Clearing flat baseline QoS records from %s before applying real QoS\n'
         % BOTTLENECK_INTERFACE)
    s1.cmd('ovs-vsctl clear port %s qos' % BOTTLENECK_INTERFACE)
    s1.cmd('ovs-vsctl --all destroy qos')
    s1.cmd('ovs-vsctl --all destroy queue')


def verify_qos(net):
    """Print the actual OVSDB + kernel-level QoS state for the
    bottleneck interface, so if something silently fails again there's
    a diagnostic trail in the run log instead of having to guess and
    re-run blind."""
    s1 = net.get('s1')
    info('*** Verifying QoS on %s\n' % BOTTLENECK_INTERFACE)
    info(s1.cmd('ovs-vsctl get port %s qos' % BOTTLENECK_INTERFACE))
    info(s1.cmd('tc -s class show dev %s' % BOTTLENECK_INTERFACE))


# ============================================================
# Parsing helpers
# ============================================================

def parse_throughput(output):
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*Mbits/sec", output)
    return float(matches[-1]) if matches else 0.0


def parse_packet_loss(output):
    matches = re.findall(r"(\d+(?:\.\d+)?)%\s*\)", output)
    if matches:
        return float(matches[-1])
    matches = re.findall(r"\((\d+(?:\.\d+)?)%\)", output)
    return float(matches[-1]) if matches else 0.0


def parse_latency(output):
    match = re.search(r"=\s*[\d.]+/([\d.]+)/[\d.]+/[\d.]+", output)
    return float(match.group(1)) if match else None


# ============================================================
# One full test phase
# ============================================================

def run_phase(net, phase_label):
    """Run the full 8-queue concurrent contention test once, tagging every
    log/temp file with phase_label ('before' / 'after') so the two runs
    never collide."""

    server = net.get('web_serv')

    info('\n====================================================\n')
    info('  QoS PHASE: %s\n' % phase_label.upper())
    info('====================================================\n')

    # ---- start UDP servers (one per queue/port) ----
    info('*** [%s] Starting iperf UDP servers\n' % phase_label)
    for test in TESTS:
        log = '/tmp/iperf_server_%s_%d.log' % (phase_label, test['queue'])
        server.cmd('iperf -s -u -p {port} > {log} 2>&1 &'.format(
            port=test['port'], log=log))
    time.sleep(2)

    # ---- deterministic ARP/MAC-learning warm-up ----
    # A short FOREGROUND ping (not backgrounded) from every test host,
    # before any UDP traffic starts, guarantees s1 has already learned
    # web_serv's MAC and installed a real out_port for each department --
    # otherwise the first UDP packets of a run could still be mid-flood
    # while MAC learning is settling, quietly skewing the first second
    # of results (and differently between phases, which would confound
    # the comparison).
    info('*** [%s] Warming up ARP / MAC learning\n' % phase_label)
    for test in TESTS:
        host = net.get(test['host'])
        host.cmd('ping -c 2 -W 1 %s > /dev/null 2>&1' % server.IP())

    # ---- background latency measurement, full test duration ----
    info('*** [%s] Starting latency measurements\n' % phase_label)
    for test in TESTS:
        host = net.get(test['host'])
        log = '/tmp/qos_ping_%s_%d.log' % (phase_label, test['queue'])
        host.cmd('ping -c 40 -i 0.5 %s > %s 2>&1 &' % (server.IP(), log))

    # ---- start ALL UDP flows simultaneously ----
    info('\n*** [%s] Starting ALL QoS flows simultaneously\n' % phase_label)
    for test in TESTS:
        host = net.get(test['host'])
        log = '/tmp/qos_client_%s_%d.log' % (phase_label, test['queue'])
        cmd = (
            'iperf -c {server} -u -p {port} -b {rate}M -l 1470 -t {dur} '
            '> {log} 2>&1 &'
        ).format(server=server.IP(), port=test['port'], rate=OFFERED_RATE,
                  dur=TEST_DURATION, log=log)
        host.cmd(cmd)
        info('*** [%s] Queue %d (%s): %s -> web_serv @ %dMbps\n' % (
            phase_label, test['queue'], test['name'], test['host'], OFFERED_RATE))

    info('\n*** [%s] All traffic now competing for the link.\n' % phase_label)
    time.sleep(TEST_DURATION + 3)

    # ---- stop everything ----
    info('\n*** [%s] Stopping iperf processes\n' % phase_label)
    server.cmd("pkill -f 'iperf -s -u -p'")
    for test in TESTS:
        net.get(test['host']).cmd("pkill -f 'iperf -c'")
    time.sleep(1)

    # ---- collect results ----
    info('\n*** [%s] Collecting results\n' % phase_label)
    results = []
    for test in TESTS:
        queue = test['queue']
        host = net.get(test['host'])

        server_output = server.cmd(
            'cat /tmp/iperf_server_%s_%d.log' % (phase_label, queue))
        ping_output = host.cmd(
            'cat /tmp/qos_ping_%s_%d.log' % (phase_label, queue))

        throughput = parse_throughput(server_output)
        loss = parse_packet_loss(server_output)
        latency = parse_latency(ping_output)

        if throughput == 0.0:
            info('*** [%s] WARNING: queue %d (%s) parsed 0 throughput -- '
                 'raw server log for debugging:\n%s\n' % (
                     phase_label, queue, test['name'], server_output[:400]))

        results.append({
            'phase': phase_label,
            'queue_id': queue,
            'queue_name': test['name'],
            'source_host': test['host'],
            'destination_host': server.name,
            'minimum_rate_mbps': MINIMUM_RATES[queue],
            'offered_rate_mbps': OFFERED_RATE,
            'throughput_mbps': throughput,
            'packet_loss_percent': loss,
            'latency_ms': latency if latency is not None else '',
        })

        info('  Queue %d - %s: throughput=%.2f Mbps loss=%.2f%% latency=%s\n' % (
            queue, test['name'], throughput, loss,
            ('%.2fms' % latency) if latency is not None else 'N/A'))

    return results


def save_csv(results, filename):
    with open(filename, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(results)
    info('\n*** Saved %s\n' % filename)


# ============================================================
# Main experiment
# ============================================================

def main():
    setLogLevel('info')

    info('*** Building network -- QoS NOT yet configured\n')
    net = build_network()
    net.build()
    net.start()

    # VLANs are needed regardless of the QoS comparison (Upper/Lower
    # Management classification depends on them) -- only the HTB queue
    # provisioning is what's deliberately deferred to isolate that one
    # variable.
    configure_management_vlans(net)

    # ---------------- Flat baseline: same 100Mbit cap, no priority ----------------
    apply_flat_baseline(net)

    # ---------------- PHASE 1: BEFORE ----------------
    before_results = run_phase(net, 'before')
    save_csv(before_results, 'qos_results_before.csv')

    # ---------------- Swap flat baseline for real QoS, same live network --------
    remove_flat_baseline(net)
    info('\n*** Applying QoS (configure_aggregation_qos) -- '
         'same running network, same total capacity, nothing else changes\n')
    configure_aggregation_qos(net)
    time.sleep(2)
    verify_qos(net)

    # ---------------- PHASE 2: AFTER ----------------
    after_results = run_phase(net, 'after')
    save_csv(after_results, 'qos_results_after.csv')

    info('\n*** Experiment complete. '
         'Run plot_qos_comparison.py to generate graphs.\n')

    net.stop()


if __name__ == '__main__':
    sys.exit(main())