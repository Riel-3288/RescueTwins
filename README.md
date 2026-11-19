# RescueTwins
ESTIMED Hackathon #2 (2026 oneM2M International Hackathon)

RescueTwin is an IoT and digital-twin project for emergency rescue scenarios.

The project connects sensor nodes, a oneM2M platform, edge-processing applications,
robot simulations, and a digital-twin web application.

**[Full project article on Hackster.io](https://www.hackster.io/eexiangoh/rescuetwin-rescue-swarm-digital-twin-on-onem2m-mec-13fbf8)**: problem, hardware, oneM2M resource design, CSE handover, security, MEC integration and results.

**[Read the RescueTwins project article](docs/rescuetwins-project.md)** for the system overview, simulation results, and demo.

## Project Structure

| Directory | Purpose |
|---|---|
| firmware | ESP32 firmware |
| simulation | Sensor and robot simulation programs |
| mec-app | Edge-processing applications and MEC integration |
| twin | Digital-twin web application |
| cse-config | CSE configuration examples and deployment notes |
| docs | Interface specifications, architecture, and integration documentation |
| script | Local test and resource-initialization scripts |

## Current Progress

- An early local test used ACME as the IN-CSE. The team setup now uses Mobius4 as the IN-CSE, with two ACME MN-CSEs at the edge.
- HTTP creation and retrieval of simulated temperature data has been verified.
- The AE and sensor containers for `node-01` have been created.
- Basic access-control permissions for `node-01` have been configured.
- Physical ESP32 sensor uploads are verified: `node-01` publishes to the Zone A MN-CSE over the oneM2M MQTT binding (see [ESP32 sensor node](#esp32-sensor-node-node-01)). Multi-device network integration is pending.
- A local MQTT/MEC integration prototype is available in [`mec-app/deploy/`](mec-app/deploy/README.md), including dual ACME MN-CSE tests, IN-CSE transit, MEC-IPE, and TLS.
- The [sensor Dashboard](twin/README.md) includes a read-only Mobius integration.
- The [RescueTwins MEC and simulation Dashboard](mec-app/deploy/README_RUN.md) includes a 50×30 two-drone experiment, an 80×40 LiveSwarm demo, and recorded mission playback. Both maps use zones A and B.
- Deployment to the team MEC host, live CSE integration for MEC-IPE, and full MEC 021 handover still require end-to-end verification.
- Team integration setup: two ACME MN-CSEs (`mn-zone-a`, `mn-zone-b`) registered with a Mobius4 IN-CSE. Reads through Mobius were verified for both zones, and edge vs cloud arrival latency was measured on hardware (102 readings: edge 116.7 ms average, cloud polling path 678.1 ms average).
- The [oneM2M fleet layer](#onem2m-fleet-layer) (agent AEs, per-agent ACPs, Group recall, SDT flexContainers, discovery, automated CSE handover) was verified on the team MN-CSEs on 19 November 2026, with 0 lost reports during handover.

## Folder Ownership

| Directory | Owner | Contents |
|---|---|---|
| `firmware/` | Sariel | ESP32 sensor node code|
| `simulation/` | Sariel | 50x30 A/B drone simulation with independent MEC map-fusion client, evaluation |
| `mec-app/deploy/rescuetwin/fleet.py` | Sariel | oneM2M fleet layer: agent and MEC app AEs, per-agent ACPs, Group recall, SDT flexContainers, discovery, CSE handover |
| `mec-app/deploy/evidence/` | Sariel | Evidence files from runs on the team CSEs |
| `mec-app/deploy/rescuetwin/swarm_logic.py`, `swarm_service.py` | Selina | Edge swarm logic: frontier auction, collision reservations, map-fusion service |
| `mec-app/deploy/` | Selina | MEC Sandbox / k8s deployment files, MEC-IPE location, MEC 021 |
| `cse-config/` | Zihan | ACME / Mobius configs for MN-CSE A, MN-CSE B, IN-CSE; ACPs |
| `twin/` | Zihan | Digital twin web app |
| `script/` | Zihan | Local test and resource-initialisation scripts, plot scripts |
| `docs/` | Everyone | Specs, architecture diagrams, article drafts |
| `README.md` | Selina | Build + deploy guide + everyone adds their section|

## Team Rules

- Edit only your own folders; ask the owner before changing theirs.

## Start the Local ACME Environment

Open a PowerShell terminal in the project root directory and run:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m acmecse --base-directory .\acme-local
```

## MQTT and MEC Integration

The [`mec-app/deploy` guide](mec-app/deploy/README.md) covers the telemetry contract,
local Docker Compose setup, MEC Location/RNIS integration, factory scenario,
TLS configuration, and shared-host deployment templates.

From this repository root, run the integration tests with Python 3.13:

```powershell
cd mec-app/deploy
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest -q
```

The test suite starts temporary real MQTT and ACME CSE instances. MEC API inputs
come from a local contract fixture. See the [deployment guide](mec-app/deploy/docs/deployment-guide.md)
for integration boundaries and live deployment acceptance checks.

## Independent MEC swarm app

The 50x30 two-zone drone simulator now supports a separate HTTP map-fusion application. See [run and deployment guide](mec-app/deploy/README_RUN.md). This is locally verified; actual MEC-host deployment still needs a reachable host.

## ESP32 sensor node (node-01)

Code: [`firmware/node01/node01.ino`](firmware/node01/node01.ino)

| Sensor | Connection | Container |
|---|---|---|
| BME280 | I2C, SDA GPIO21, SCL GPIO22 | `temperature`, `humidity`, `pressure` |
| MQ-2 | AO → 10k/20k divider → GPIO34 | `smoke_raw` (mV) |
| MQ-7 | AO → 10k/20k divider → GPIO35 | `co_raw` (mV) |
| HLK-LD2410 | UART2, RX GPIO16, TX GPIO17, 256000 baud | `presence` |

The node reads every 5 s and publishes oneM2M Create requests over MQTT
(`/oneM2M/req/CNode01/id-mn-zone-a/json`, responses on `/oneM2M/resp/CNode01/id-mn-zone-a/json`).
Power: 4.5 V battery pack; USB only for the serial monitor.

To build: open the sketch in the Arduino IDE (ESP32 core, Adafruit BME280, PubSubClient, ArduinoJson,
`ld2410` library), replace the `YOUR_...` and `<...>` placeholders with your WiFi,
CSE and broker settings, and flash. Run `script/setup_node01.py` first so the AE and
containers exist. The MQ readings are raw divider voltages, not calibrated gas concentrations.

## oneM2M fleet layer

[`mec-app/deploy/rescuetwin/fleet.py`](mec-app/deploy/rescuetwin/fleet.py) adds oneM2M
resources for the simulated agents and the MEC application:

| Feature | oneM2M resources used |
|---|---|
| Agents and MEC app registered | `<AE>` per drone / robot, `rescue-mec` `<AE>` on each MN-CSE |
| Least-privilege access | one `<ACP>` per agent: agent (all), MEC app (create, read, discover), twin (read, discover) |
| Recall a whole zone with one request | `<group>` `grp-agents`, fan-out to every agent `cmd` container |
| Agents receive commands | `<subscription>` on `cmd` with an expiration time; commands expire after 10 minutes |
| Sensor state the twin can discover | TS-0023 SDT `<flexContainer>`: `temperature`, `relativeHumidity`, `smokeSensor`, `motionSensor` |
| Finding resources | filter queries by label and type, e.g. `?fu=1&ty=2&lbl=drone@zone-A` |
| CSE handover | on a zone change the agent's AE, ACP, containers and subscription are created on the target MN-CSE, the last state is copied, Group membership moves, and interruption and lost reports are stored in `rescue-mec/handover` |

Run every feature once (both MN-CSEs must be running), from `mec-app/deploy`:

```powershell
python tools\fleet_demo.py --config config.example.json
```

Add a `fleet` block to the config (see [`config.zihan.json`](mec-app/deploy/config.zihan.json)).
When the CSEs run on another machine, set `fleet.notify_host` to this machine's LAN IP and
`fleet.notify_port` to a fixed port, and allow that port through the firewall: the CSE
verifies each subscription by sending a request to that address.

Result on the team MN-CSEs (19 November 2026, run from a second laptop over the campus
network): handover of `agent-01` from Zone A to Zone B succeeded, 0 lost reports,
procedure 845.9 ms, interruption 1,046.1 ms; Group recall reached 3 of 3 agents in Zone A
and 2 of 2 in Zone B after the handover. Evidence:
[`mec-app/deploy/evidence/`](mec-app/deploy/evidence/).
The handover is an application-layer re-registration, not MEC 021 context transfer.

Python 3.14: `amqtt` pins `PyYAML==6.0.2`, which has no 3.14 wheel, so the requirements
files install only on Python 3.12/3.13. On 3.14, run
`python -m pip install --only-binary=:all: PyYAML==6.0.3`, then
`python -m pip install --no-deps amqtt==0.11.3` and amqtt's other dependencies
(dacite, passlib, psutil, transitions, typer, websockets).

## oneM2M resources

- [oneM2M](https://www.onem2m.org/) and its [published specifications](https://www.onem2m.org/technical/published-specifications) (TS-0001 architecture, TS-0004 protocol, TS-0010 MQTT binding, TS-0023 SDT)
- [oneM2M recipes](https://recipes.onem2m.org/)
- [ACME CSE](https://acmecse.net) and [Mobius4](https://github.com/iotketi/mobius4)
- [ETSI MEC Sandbox](https://try-mec.etsi.org/)

## Team

Goh Ee Xian (Sariel), Tan Xiang Huey (Selina), Ai Zihan.
Advisors: Asst. Prof. Ir. Dr. Lian Wen Xun and Dr. Burra.