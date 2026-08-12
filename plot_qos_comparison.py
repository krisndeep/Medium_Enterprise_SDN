#!/usr/bin/env python3
"""
plot_qos_comparison.py

Reads qos_results_before.csv and qos_results_after.csv (produced by
qos_before_after.py) and generates three comparison graphs:

  1. qos_throughput_comparison.png -- throughput per department,
     before vs after, with each queue's configured minimum-rate floor
     marked so you can see how much better "after" honors the
     guarantee.
  2. qos_packet_loss_comparison.png -- packet loss %, before vs after.
  3. qos_latency_comparison.png -- ping latency (ms), before vs after.

Run this OUTSIDE Mininet (e.g. in a normal terminal on the same VM),
after both CSVs exist in the current directory:

    python3 plot_qos_comparison.py

Requires matplotlib + pandas. If missing:

    pip install matplotlib pandas --break-system-packages
"""

import sys

try:
    import pandas as pd
    import matplotlib
    matplotlib.use('Agg')  # no display needed, just save PNGs
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError as e:
    sys.exit(
        "Missing dependency: %s\n"
        "Install with: pip install matplotlib pandas --break-system-packages"
        % e
    )


def load_and_align():
    try:
        before = pd.read_csv('qos_results_before.csv')
        after = pd.read_csv('qos_results_after.csv')
    except FileNotFoundError as e:
        sys.exit(
            "Could not find %s -- run qos_before_after.py first "
            "(inside Mininet) to generate both CSVs." % e.filename
        )

    before = before.sort_values('queue_id').reset_index(drop=True)
    after = after.sort_values('queue_id').reset_index(drop=True)

    if not (before['queue_id'] == after['queue_id']).all():
        sys.exit("before/after CSVs have mismatched queue ordering -- "
                  "did they come from the same experiment run?")

    return before, after


def _grouped_bar(ax, labels, before_vals, after_vals, ylabel, title,
                  extra_marker=None, extra_label=None):
    x = np.arange(len(labels))
    width = 0.35

    ax.bar(x - width / 2, before_vals, width, label='Before QoS', color='#c0392b')
    ax.bar(x + width / 2, after_vals, width, label='After QoS', color='#27ae60')

    if extra_marker is not None:
        ax.scatter(x + width / 2, extra_marker, marker='_', s=400,
                   color='black', zorder=5, label=extra_label)

    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha='right')
    ax.legend()
    ax.grid(axis='y', alpha=0.3)


def plot_throughput(before, after):
    fig, ax = plt.subplots(figsize=(12, 6))
    _grouped_bar(
        ax, before['queue_name'],
        before['throughput_mbps'], after['throughput_mbps'],
        ylabel='Throughput (Mbps)',
        title='Throughput per Department: Before vs After QoS',
        extra_marker=before['minimum_rate_mbps'],
        extra_label='Configured min-rate (guarantee)',
    )
    fig.tight_layout()
    fig.savefig('qos_throughput_comparison.png', dpi=150)
    print('Saved qos_throughput_comparison.png')


def plot_packet_loss(before, after):
    fig, ax = plt.subplots(figsize=(12, 6))
    _grouped_bar(
        ax, before['queue_name'],
        before['packet_loss_percent'], after['packet_loss_percent'],
        ylabel='Packet Loss (%)',
        title='Packet Loss per Department: Before vs After QoS',
    )
    fig.tight_layout()
    fig.savefig('qos_packet_loss_comparison.png', dpi=150)
    print('Saved qos_packet_loss_comparison.png')


def plot_latency(before, after):
    b_lat = before['latency_ms'].fillna(0)
    a_lat = after['latency_ms'].fillna(0)

    fig, ax = plt.subplots(figsize=(12, 6))
    _grouped_bar(
        ax, before['queue_name'], b_lat, a_lat,
        ylabel='Latency (ms)',
        title='Latency per Department: Before vs After QoS',
    )
    fig.tight_layout()
    fig.savefig('qos_latency_comparison.png', dpi=150)
    print('Saved qos_latency_comparison.png')


def print_summary(before, after):
    print('\nSummary (Before -> After):')
    for _, row in before.iterrows():
        after_row = after[after['queue_id'] == row['queue_id']].iloc[0]
        print(
            '  Queue %d %-24s throughput %6.2f -> %6.2f Mbps '
            '(min guarantee %d Mbps) | loss %5.2f%% -> %5.2f%%' % (
                row['queue_id'], row['queue_name'],
                row['throughput_mbps'], after_row['throughput_mbps'],
                row['minimum_rate_mbps'],
                row['packet_loss_percent'], after_row['packet_loss_percent'],
            )
        )


def main():
    before, after = load_and_align()
    plot_throughput(before, after)
    plot_packet_loss(before, after)
    plot_latency(before, after)
    print_summary(before, after)


if __name__ == '__main__':
    main()
