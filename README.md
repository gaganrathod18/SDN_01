# SDN Assignment 01 — Simple Load Balancer (POX / OpenFlow)

A POX controller (`SimpleLoadBalancer.py`) that turns a single OpenFlow switch into a
transparent load balancer between 4 clients and 4 backend servers, fronted by a virtual
service IP, tested with Mininet on Fedora 43.

## Where to look

- **Want to know how it's built / how to set it up and run it?** → [`IMPL.md`](IMPL.md)
  Design rationale, function-by-function implementation notes, full environment setup
  (Fedora 43 + Podman + Python 2.7, since POX `carp` doesn't run on the host's
  Python 3), step-by-step run instructions with expected output, and a troubleshooting
  table.

- **Want to know if it actually works / see the test results?** → [`report.md`](report.md)
  ([styled version: `report.html`](report.html)) — the final test report, every step
  actually run, with real terminal screenshots (`public/`), real ping/flow-table/mapping
  data, and the issues hit during testing and how they were resolved.

- **Just want the commands to re-run the tests yourself?** → [`commands.md`](commands.md)
  Every command for every test, in order, with no narration — copy-paste and go.

## Files

| File | What it is |
|---|---|
| `SimpleLoadBalancer.py` | the controller implementation (submit this) |
| `IMPL.md` | design + setup/run guide |
| `report.md` | test report with screenshots and results |
| `report.html` | same report, designed/styled standalone page (open directly in a browser) |
| `commands.md` | plain copy-pasteable command list for re-running every test |
| `public/` | terminal screenshots referenced by `report.md` |
| `exercise1_problem.pdf` | assignment spec |
| `exercise1_reading.pdf` | POX/Mininet/OpenFlow tutorial (background reading) |

## TL;DR

```bash
# Terminal 1 — controller
cd ~/Desktop/7sem/SDN/pox
podman run --rm -it --network host \
  -v ~/Desktop/7sem/SDN/pox:/pox:Z python:2.7 \
  bash -c 'cd /pox && python ./pox.py log.level --DEBUG SimpleLoadBalancer --ip=10.1.2.3 --servers=10.0.0.5,10.0.0.6,10.0.0.7,10.0.0.8'

# Terminal 2 — network
sudo mn --topo single,8 --controller remote,ip=127.0.0.1,port=6633 --mac --switch ovsk
mininet> h1 ping -c4 10.1.2.3
```
Full details, every step, and why: see `IMPL.md`. Results of actually running it:
see `report.md`.
