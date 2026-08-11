#!/usr/bin/env python3

import csv
import re
import time

from mininet.log import info


# ============================================================
# QoS configuration
# ============================================================

TEST_DURATION = 30

# Every flow offers 50 Mbps.
#
# 8 flows × 50 Mbps = 400 Mbps offered
#
# The server-side aggregation link is 100 Mbps.
# Therefore the queues MUST compete for bandwidth.
#
OFFERED_RATE = 100


# Queue:
# 0 = HR
# 1 = Lower Management
# 2 = Upper Management
# 3 = Business
# 4 = SDE
# 5 = Call Support
# 6 = Infrastructure
# 7 = Upper Management VoIP
# 8 = Servers

TESTS = [
    {
        "queue": 0,
        "name": "HR",
        "host": "hr1",
        "port": 5000,
        "type": "normal"
    },

    {
        "queue": 1,
        "name": "Lower Management",
        "host": "manager1",
        "port": 5001,
        "type": "normal"
    },

    {
        "queue": 2,
        "name": "Upper Management",
        "host": "ceo",
        "port": 5002,
        "type": "normal"
    },

    {
        "queue": 3,
        "name": "Business",
        "host": "bus1",
        "port": 5003,
        "type": "normal"
    },

    {
        "queue": 4,
        "name": "SDE",
        "host": "dev1",
        "port": 5004,
        "type": "normal"
    },

    {
        "queue": 5,
        "name": "Call Support",
        "host": "support1",
        "port": 5005,
        "type": "normal"
    },

    {
        "queue": 6,
        "name": "Infrastructure",
        "host": "infra1",
        "port": 5006,
        "type": "normal"
    },

    {
        "queue": 7,
        "name": "Upper Management VoIP",
        "host": "ceo",
        "port": 20000,
        "type": "voip"
    },
    {
        # db_serv is in the SERVERS department, same as web_serv (the
        # destination for all other test flows). This is the only test
        # flow that actually exercises QUEUE_SERVERS end to end.
        "queue": 8,
        "name": "Servers",
        "host": "db_serv",
        "port": 5007,
        "type": "normal"
    }

]


# ============================================================
# Configured minimum rates
# ============================================================

MINIMUM_RATES = {
    0: 5,
    1: 8,
    2: 10,
    3: 10,
    4: 10,
    5: 12,
    6: 15,
    7: 20,
    8: 5
}


# ============================================================
# Start iperf servers
# ============================================================

def start_servers(server):

    info("\n*** Starting iperf UDP servers\n")

    for test in TESTS:

        port = test["port"]

        command = (
            "iperf -s -u -e -p {port} "
            "> /tmp/iperf_server_{queue}.log 2>&1 &"
        ).format(
            port=port,
            queue=test["queue"]
        )

        server.cmd(command)

        info(
            "*** Queue %d: server UDP/%d started\n"
            % (
                test["queue"],
                port
            )
        )

    time.sleep(2)


# ============================================================
# Start ping processes
# ============================================================

def start_ping_tests(server):

    info("\n*** Starting latency measurements\n")

    for test in TESTS:

        host_name = test["host"]

        # Use the host object later from net.
        host = NET.get(host_name)

        output_file = (
            "/tmp/qos_ping_%d.log"
            % test["queue"]
        )

        command = (
            "ping -c 40 -i 0.5 {destination} "
            "> {output} 2>&1 &"
        ).format(
            destination=server.IP(),
            output=output_file
        )

        host.cmd(command)


# ============================================================
# Start ALL UDP flows simultaneously
# ============================================================

def start_clients(server):

    info("\n")
    info("====================================================\n")
    info("Starting ALL QoS flows simultaneously\n")
    info("====================================================\n")

    for test in TESTS:

        host = NET.get(test["host"])

        queue = test["queue"]
        port = test["port"]

        output_file = (
            "/tmp/qos_client_%d.log"
            % queue
        )

        command = (
            "iperf -c {server} "
            "-u "
            "-p {port} "
            "-b {rate}M "
            "-l 1470 "
            "-t {duration} "
            "> {output} 2>&1 &"
        ).format(
            server=server.IP(),
            port=port,
            rate=OFFERED_RATE,
            duration=TEST_DURATION,
            output=output_file
        )

        host.cmd(command)

        info(
            "*** Queue %d (%s) started: %s -> web_serv "
            "at %d Mbps\n"
            % (
                queue,
                test["name"],
                test["host"],
                OFFERED_RATE
            )
        )


# ============================================================
# Stop iperf processes
# ============================================================

def stop_iperf(server):

    info("\n*** Stopping iperf processes\n")

    server.cmd("pkill -f 'iperf -s -u'")

    for test in TESTS:

        host = NET.get(test["host"])

        host.cmd(
            "pkill -f 'iperf -c'"
        )


# ============================================================
# Parse throughput
# ============================================================

def parse_throughput(output):

    # Find all Mbits/sec values.
    matches = re.findall(
        r"(\d+(?:\.\d+)?)\s*Mbits/sec",
        output
    )

    if not matches:
        return 0.0

    # Last value is normally the final interval/result.
    return float(matches[-1])


# ============================================================
# Parse packet loss
# ============================================================

def parse_packet_loss(output):

    # Typical iperf2 UDP output:
    #
    # 0.00-30.00 sec ...
    # 123/456 (27%)
    #

    matches = re.findall(
        r"(\d+(?:\.\d+)?)%\s*\)",
        output
    )

    if matches:
        return float(matches[-1])

    # Alternative format
    matches = re.findall(
        r"\((\d+(?:\.\d+)?)%\)",
        output
    )

    if matches:
        return float(matches[-1])

    return 0.0


# ============================================================
# Parse ping latency
# ============================================================

def parse_latency(output):

    # Example:
    #
    # rtt min/avg/max/mdev =
    # 0.123/1.234/2.345/0.456 ms
    #

    match = re.search(
        r"=\s*[\d.]+/([\d.]+)/[\d.]+/[\d.]+",
        output
    )

    if match:
        return float(match.group(1))

    # No match means ping produced no valid summary line -- almost
    # always because every packet was lost, not because latency was
    # actually zero. Signal this explicitly instead of faking a 0.
    return None

# ============================================================
# Read results
# ============================================================

def collect_results(server):

    results = []

    info("\n")
    info("====================================================\n")
    info("Collecting QoS results\n")
    info("====================================================\n")

    for test in TESTS:

        queue = test["queue"]

        host = NET.get(
            test["host"]
        )

        # ----------------------------------------------------
        # Read iperf output
        # ----------------------------------------------------

        # Client log still useful for debugging/offered-rate sanity checks.
        client_output = host.cmd(
            "cat /tmp/qos_client_%d.log"
            % queue
        )

        # Server-side log reflects what was ACTUALLY delivered through
        # the bottleneck -- this is what we should report as measured
        # throughput/loss, not what the client attempted to send.
        server_output = server.cmd(
            "cat /tmp/iperf_server_%d.log"
            % queue
        )

        throughput = parse_throughput(
            server_output
        )

        packet_loss = parse_packet_loss(
            server_output
        )

        # Sanity check: a flow can never deliver more than it sent.
        if throughput > OFFERED_RATE * 1.05:
            info(
                "*** WARNING: Queue %d throughput %.2f Mbps exceeds "
                "offered rate %d Mbps -- check parsing/log for this queue\n"
                % (queue, throughput, OFFERED_RATE)
            )

        # ----------------------------------------------------
        # Read ping output
        # ----------------------------------------------------

        ping_output = host.cmd(
            "cat /tmp/qos_ping_%d.log"
            % queue
        )

        latency = parse_latency(
            ping_output
        )

        # ----------------------------------------------------
        # Store result
        # ----------------------------------------------------

        result = {
            "queue_id": queue,
            "queue_name": test["name"],
            "source_host": test["host"],
            "destination_host": server.name,
            "minimum_rate_mbps": MINIMUM_RATES[queue],
            "offered_rate_mbps": OFFERED_RATE,
            "throughput_mbps": throughput,
            "packet_loss_percent": packet_loss,
            "latency_ms": latency,
            "latency_valid": latency is not None
        }

        results.append(result)

        info(
            "\nQueue %d - %s\n"
            % (
                queue,
                test["name"]
            )
        )

        info(
            "  Minimum rate : %d Mbps\n"
            % MINIMUM_RATES[queue]
        )

        info(
            "  Offered rate : %d Mbps\n"
            % OFFERED_RATE
        )

        info(
            "  Throughput   : %.2f Mbps\n"
            % throughput
        )

        info(
            "  Packet loss  : %.2f %%\n"
            % packet_loss
        )

        if latency is not None:
            info(
                "  Latency      : %.2f ms\n"
                % latency
            )
        else:
            info(
                "  Latency      : NO DATA (ping got no replies)\n"
            )
    return results


# ============================================================
# Save CSV
# ============================================================
def save_csv(results):

    filename = "qos_results.csv"

    with open(
        filename,
        "w",
        newline=""
    ) as file:

        writer = csv.writer(file)

        writer.writerow([
            "queue_id",
            "queue_name",
            "source_host",
            "destination_host",
            "minimum_rate_mbps",
            "offered_rate_mbps",
            "throughput_mbps",
            "packet_loss_percent",
            "latency_ms"
        ])

        for result in results:

            writer.writerow([
                result["queue_id"],
                result["queue_name"],
                result["source_host"],
                result["destination_host"],
                result["minimum_rate_mbps"],
                result["offered_rate_mbps"],
                result["throughput_mbps"],
                result["packet_loss_percent"],
                result["latency_ms"] if result["latency_ms"] is not None else "N/A"
            ])

    info(
        "\n*** CSV saved as %s\n"
        % filename
    )
# ============================================================
# Main experiment
# ============================================================

def run_qos_evaluation(net):

    global NET

    NET = net

    server = net.get("web_serv")

    info("\n")
    info("====================================================\n")
    info("          CONCURRENT QoS EVALUATION\n")
    info("====================================================\n")

    info(
        "*** Number of flows: %d\n"
        % len(TESTS)
    )

    info(
        "*** Offered rate per flow: %d Mbps\n"
        % OFFERED_RATE
    )

    info(
        "*** Total offered traffic: %d Mbps\n"
        % (
            len(TESTS) * OFFERED_RATE
        )
    )

    info(
        "*** Aggregation egress bottleneck: 100 Mbps\n"
    )

    info(
        "*** Test duration: %d seconds\n"
        % TEST_DURATION
    )

    # --------------------------------------------------------
    # Start servers
    # --------------------------------------------------------

    start_servers(server)

    # --------------------------------------------------------
    # Start latency measurements
    # --------------------------------------------------------

    start_ping_tests(server)

    # --------------------------------------------------------
    # Start ALL traffic simultaneously
    # --------------------------------------------------------

    start_clients(server)

    info("\n")
    info("*** All traffic is now competing for the "
         "100 Mbps server-side link.\n")

    # --------------------------------------------------------
    # Wait for experiment
    # --------------------------------------------------------

    time.sleep(
        TEST_DURATION + 3
    )

    # --------------------------------------------------------
    # Stop iperf
    # --------------------------------------------------------

    stop_iperf(server)

    # --------------------------------------------------------
    # Collect
    # --------------------------------------------------------

    results = collect_results(
        server
    )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    save_csv(
        results
    )

    info("\n")
    info("====================================================\n")
    info("QoS evaluation completed\n")
    info("====================================================\n")