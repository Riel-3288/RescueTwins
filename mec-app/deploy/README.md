> Live swarm demo: `start-demo.ps1 -Mode LiveSwarm`, with an 80x40 grid, eight drones and zones A/B by default. See the [run guide](README_RUN.md).

> On 2026-11-11, the version changed to a 50x30 map, zones A/B and drone-A/B, with a separate MEC swarm-fusion service. Follow [README_RUN.md](README_RUN.md) for current startup and deployment instructions. The earlier three-zone instructions below are retained as historical notes.

> For a simple startup workflow, see the [Simulation / Dashboard quick start](README_RUN.md).

> See the [deployment guide](docs/deployment-guide.md) for recorded inputs and acceptance checks. Sensor input defaults to the repository's `simulation/sample_data.json`, preserving the original timestamps and source. Incompatible records are reported separately.

# RescueTwins MQTT / MEC Integration

This module provides team oneM2M MQTT file uploads, MEC integration, drone-recording replay and route previews, and an isolated local factory demo.
Sensor messages use the eight fields agreed by the team.

The dashboard has four pages: Recorded Mission, Factory Overview, Sensors & Messages, and Agent Status. They support
sensor filters, JSON inspection, CSV export, and agent route and goal editing. The 3D map visualizes the same 2D simulation data.

## Local startup without Docker

Run the following in PowerShell from this directory:

~~~powershell
powershell -ExecutionPolicy Bypass -File .\start-demo.ps1
~~~

Open http://127.0.0.1:8765/#recorded. The default starts the file-replay web page only, with the MQTT broker, bridge and CSE left off.

The `-Mode Recorded|Swarm|SwarmHeadless|LocalDemo|LocalCse` options select file replay, Sariel's animation, the headless benchmark, the legacy local demo, and the legacy local CSE demo. Commands, dependencies, API requests/responses and pending work are listed in the [team handoff](docs/team-collaboration.md#2026-11-10-selina-handoff).

The team integration uses oneM2M MQTT binding directly. Plain rescuetwin topics and the bridge remain available for the legacy isolated demo. smoke_raw/co_raw use mV throughout, preserving their raw values.

## Guides

- [Simulator + MQTTX: startup, maps, test messages and troubleshooting](docs/simulator-mqttx-guide.md)
- [Deployment: MQTT contract, CSE, MEC/Sandbox, TLS, Kubernetes and acceptance](docs/deployment-guide.md)
- [Team collaboration: branches, updates and interface handoff](docs/team-collaboration.md)

## Tests

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest -q
~~~

Local tests use real MQTT/ACME instances and MEC API fixtures. The shared environment, physical ESP32 integration,
ACME-Mobius interoperability and full MEC021 workflow still require integration testing.

## Editing the dashboard

Running the demo requires no Node.js installation because the repository includes the built `dashboard/app.js`. After editing `dashboard/src/`,
run these commands in `dashboard/` and refresh the browser. Edit the source files rather than the generated `app.js`:

~~~powershell
npm ci
npm test
npm run build
~~~

Three.js is bundled locally, with no runtime CDN dependency. Third-party licenses are in `dashboard/THIRD-PARTY-LICENSES.txt`.

## Directory layout

~~~text
rescuetwin/          MQTT bridge, oneM2M client, MEC-IPE, simulator and HTTP services
dashboard/          English-language web interface
broker/             Mosquitto configuration and ACLs
k8s/                MEC host deployment templates
scenario/           Factory scenario and map
tools/              CSE, certificate, scenario and deployment tools
tests/              MQTT, CSE, TLS and simulator tests
docs/               Three operating guides
start-demo.ps1      Windows local startup entry point
compose.local.yaml  Docker Compose development environment
~~~


## Rescue loop and CSE recovery

Click **Run Rescue Loop** on Overview to observe a sensor alert, automatic safe inspection assignment,
MQTT command acknowledgment, movement, completion, and sensor recovery. Rescue Operations shows the
incident/task timeline; CSE Delivery Health shows replay throughput and queue age. The executor is local
simulation; physical robot and teammate swarm adapters still need integration.

The bridge batches durable MQTT ingestion and completion commits, runs independent A/B delivery workers,
prioritizes rescue events, and replays the existing outbox without deleting records. Simulator telemetry uses
change detection and adaptive heartbeats to reduce repeated writes. See the simulator guide for restart,
MQTT topics, thresholds, recovery, and current integration limits.

If an older demo freezes during task dispatch, open a new PowerShell terminal and run `stop-demo.ps1`,
then start the demo again. The helper checks command-line ownership of the demo ports before stopping
anything and keeps all database files. Use `-WhatIf` to preview its targets. MQTT publication now runs
on a separate worker so broker callbacks cannot deadlock the simulation lock.


## Team recordings and drone integration

Use [the recorded mission workflow](docs/simulator-mqttx-guide.md#recorded-json-integration-and-drone-missions) for uploaded sensor JSON/JSONL and the current `simulation/sim_agent_log.jsonl`. It preserves the original three-zone grid and timestamps, adds offline drone inspection/patrol routing, round-trip/energy checks, and exports robot task suggestions. Launch with `python -m rescuetwin.demo --config config.team.example.json --recorded-only`, then open `http://127.0.0.1:8765/#recorded`.

`python -m rescuetwin --config config.team.example.json check-team` checks the supplied Mobius transit reads and MEC013 without registration or writes. Team CSE access from this computer is still unverified (timeouts on 2026-11-10); MEC Location v3 was reachable. Smoke/CO mV units are aligned; hazard thresholds, zone C routing, independent AE/ACP permissions, robot feedback and UE mapping remain explicit integration gaps. This feature does not claim real-time flight or live migration.


## Swarm cooperative control

Swarm exploration bidding and collision reservations are implemented in `rescuetwin/swarm_logic.py`, served by `rescuetwin/swarm_service.py` and consumed through `tools/run_swarm.py`. The existing Swarm launcher uses this adapter; the team-owned simulator remains unchanged. See [run guide](README_RUN.md#swarm-logic-implementation-and-scope).
