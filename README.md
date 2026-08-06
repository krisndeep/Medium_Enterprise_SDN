# Medium_Enterprise_SDN
A Ryu based controller implemenation for a medium scale software development enterprise network.

To run primary controller
```RYU_ROLE=primary ryu-manager --ofp-tcp-listen-port 6653 contr2.py```
To run standby controller
```RYU_ROLE=standby RYU_PEER_IP=127.0.0.1 RYU_PEER_PORT=6653 ryu-manager --ofp-tcp-listen-port 6654 contr.py```
To run mininet topo
```sudo python3 topo2.py```