# RescueTwins
Estimted Hackthon#2

RescueTwin is an IoT and digital-twin project for emergency rescue scenarios.

The project connects sensor nodes, a oneM2M platform, edge-processing applications,
robot simulations, and a digital-twin web application.

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

- A local ACME test environment is running as an IN-CSE.
- HTTP creation and retrieval of simulated temperature data has been verified.
- The AE and sensor containers for `node-01` have been created.
- Basic access-control permissions for `node-01` have been configured.
- Physical ESP32 sensor uploads and multi-device network integration are pending.
- MN-CSE, Mobius IN-CSE, MQTT, and the digital-twin web application are pending.

## Folder Ownership

| Directory | Owner | Contents |
|---|---|---|
| `firmware/` | Sariel | ESP32 sensor node code|
| `simulation/` | Sariel | 2D grid swarm simulation, live + offline run modes, evaluation |
| `simulation/onem2m_adapter/` | Zihan | Agent AE adapter (register, post, notifications over HTTP) |
| `simulation/results/` | Sariel | Evaluation CSVs and trajectory logs (optional) |
| `mec-app/swarm/` | Sariel | Edge app logic: pheromone map, evaporation, task bidding, sanity checks |
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