import csv
import matplotlib.pyplot as plt


# ------------------------------------------------------------
# Read CSV
# ------------------------------------------------------------

filename = "qos_results.csv"

data = []

with open(filename, "r") as f:
    reader = csv.DictReader(f)

    for row in reader:
        data.append(row)


# ------------------------------------------------------------
# Extract values
# ------------------------------------------------------------

queue_id = [
    int(row["queue_id"])
    for row in data
]

queue_name = [
    row["queue_name"]
    for row in data
]

minimum_rate = [
    float(row["minimum_rate_mbps"])
    for row in data
]

throughput = [
    float(row["throughput_mbps"])
    for row in data
]

packet_loss = [
    float(row["packet_loss_percent"])
    for row in data
]

latency = [
    float(row["latency_ms"])
    for row in data
]


# ------------------------------------------------------------
# 1. Throughput
# ------------------------------------------------------------

plt.figure(figsize=(10, 6))

plt.bar(queue_name, throughput)

plt.xlabel("QoS Queue")
plt.ylabel("Throughput (Mbps)")
plt.title("Throughput per QoS Queue")

plt.xticks(rotation=45, ha="right")

plt.tight_layout()
plt.savefig("qos_throughput.png", dpi=300)

plt.show()


# ------------------------------------------------------------
# 2. Minimum Rate vs Measured Throughput
# ------------------------------------------------------------

plt.figure(figsize=(10, 6))

x = list(range(len(queue_id)))

plt.bar(
    [i - 0.2 for i in x],
    minimum_rate,
    width=0.4,
    label="Configured Minimum"
)

plt.bar(
    [i + 0.2 for i in x],
    throughput,
    width=0.4,
    label="Measured Throughput"
)

plt.xlabel("QoS Queue")
plt.ylabel("Bandwidth (Mbps)")
plt.title("Configured Minimum vs Measured Throughput")

plt.xticks(
    x,
    queue_name,
    rotation=45,
    ha="right"
)

plt.legend()

plt.tight_layout()
plt.savefig("qos_minimum_vs_actual.png", dpi=300)

plt.show()


# ------------------------------------------------------------
# 3. Packet Loss
# ------------------------------------------------------------

plt.figure(figsize=(10, 6))

plt.bar(queue_name, packet_loss)

plt.xlabel("QoS Queue")
plt.ylabel("Packet Loss (%)")
plt.title("Packet Loss per QoS Queue")

plt.xticks(rotation=45, ha="right")

plt.tight_layout()
plt.savefig("qos_packet_loss.png", dpi=300)

plt.show()


# ------------------------------------------------------------
# 4. Latency
# ------------------------------------------------------------

plt.figure(figsize=(10, 6))

plt.bar(queue_name, latency)

plt.xlabel("QoS Queue")
plt.ylabel("Latency (ms)")
plt.title("Latency per QoS Queue")

plt.xticks(rotation=45, ha="right")

plt.tight_layout()
plt.savefig("qos_latency.png", dpi=300)

plt.show()


print("\nGraphs generated successfully:")
print("qos_throughput.png")
print("qos_minimum_vs_actual.png")
print("qos_packet_loss.png")
print("qos_latency.png")