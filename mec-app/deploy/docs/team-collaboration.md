> On 2026-11-11, the version changed to a 50x30 map, zones A/B and drone-A/B, with a separate MEC swarm-fusion service. Follow [README_RUN.md](../README_RUN.md) for current startup and deployment instructions. The earlier three-zone instructions below are retained as historical notes.

# Team collaboration and integration

Team repository: `https://github.com/Riel-3288/RescueTwins`.
Module path: `mec-app/deploy/`. Feature branch: `feature/mqtt-mec-integration`.

## Get the code

Before the PR is merged, teammates can inspect the feature branch in their own clones:

```powershell
git fetch origin
git switch --track origin/feature/mqtt-mec-integration
```

If the branch already exists locally, use `git switch feature/mqtt-mec-integration`.
After merging, update main in a working tree with no uncommitted changes:

```powershell
git switch main
git pull --ff-only origin main
```

Preserve your uncommitted work before switching branches. Create subsequent branches from the updated main and merge through PRs.

## Run the module

Start at the repository root with Python 3.13:

```powershell
cd mec-app/deploy
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest -q
```

For the first local demo, run start-demo.ps1 following the [Simulator + MQTTX guide](simulator-mqttx-guide.md). Docker is optional for this workflow.
For containers or a shared environment, follow the [deployment guide](deployment-guide.md).
The new directory's `.gitignore` and `.dockerignore` exclude virtual environments, runtime data, private keys, tokens and local configuration.

## Interface handoff for the current team integration

| Component | Required information | Current interface |
|---|---|---|
| Hardware | Broker address, credentials, Originator, target CSE and container paths | oneM2M MQTT binding. Subscribe to responses before publishing requests. Serialize the eight-field reading into m2m:cin.con and wait for a matching rqi and rsc=2001. |
| CSE | A/B/IN URLs, actual registered aei, authentication and ACPs | Team uploads use oneM2M MQTT directly. HTTP supports the existing read-only cross-CSE checks, with no extra bridge required. |
| Swarm / twin | CSE resource paths, event schema, update or subscription method | IPE resources `mec-ipe/location`, `rnis`, `requests`, `responses`, `handover`, and agent `position` |
| MEC | Personal API base, UE address and application instance ID | MEC013 Location, MEC012 RNIS and MEC011 confirm_ready |

The maintainer of `cse-config/` remains responsible for the team's deployed CSE configuration. This module's `tools/acme_config.py` supplies separate testing/deployment examples.
Verify oneM2M MQTT to MN-A, responses/read-back and Mobius cross-CSE reads first. Then integrate independent drone AEs and MEC positions, followed by MN-B/migration acceptance. Legacy plain-topic examples are limited to isolated LocalDemo/LocalCse runs.

## Current verification scope

Tests cover real MQTT and ACME CSEs, Mcc forwarding, HTTPS and MQTT client certificate validation.
MEC REST inputs in automated regressions come from local test services. Real Hosted Location/RNIS/MEC011 endpoints were also checked read-only. Shared VM deployment, Mosquitto ACLs, independent application integration and ACME-Mobius
interoperability, MEC021 context transfer and full subscription migration still require acceptance evidence from their respective environments.


## 2026-11-10 Selina handoff

At the time of this handoff, origin was https://github.com/Riel-3288/RescueTwins.git on feature/mqtt-mec-integration, with committed baseline 9300fc5. The mV, drone replay, unified launcher and native-import updates were still local and unpushed. A git pull alone would not retrieve them. Use the handoff ZIP at the repository root, or pull after the changes are committed. The ZIP excludes passwords, virtual environments and runtime databases.

### File locations and dependencies

- Sariel's original simulation: simulation/rescue_sim.py, reading node01_sample.jsonl and exporting sim_sensor_data.jsonl and sim_agent_log.jsonl. This round preserved the source, inputs and existing outputs.
- Selina's replay/planning: mec-app/deploy/rescuetwin/recordings.py. Pages are under dashboard, direct MQTT file uploads under rescuetwin/native.py, and team configuration in config.team.example.json.
- Python 3.13. Replay uses requirements.txt, the original simulation uses requirements-simulation.txt (numpy, matplotlib), and the legacy local CSE demo uses requirements-demo.txt (also including aMQTT and ACME). The generated web app.js is included, so teammates can start without installing Node.js.

### Startup

Open PowerShell at the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode Recorded
```

Open http://127.0.0.1:8765/#recorded. It reads existing sensor/agent logs and supports uploads, a timeline, drone inspection/patrol, obstacle avoidance, simplified energy/return-home checks and JSON export. Team-computer, broker and VM connections are unnecessary for this mode.

To rerun Sariel's three-drone random/swarm benchmark and export JSONL:

```powershell
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode SwarmHeadless
```

To display a 2D animation after benchmarking and JSONL export, use `-Mode Swarm`. The original script entry point is `python simulation/rescue_sim.py --no-anim`, or omit `--no-anim` for animation. The older rescuetwin_sim.py name in the file header is outdated.

Use `-Mode LocalDemo` for the legacy four-agent factory demo, or `-Mode LocalCse` to also start isolated local ACME A/B/IN instances. These historical standalone examples provide no evidence of connectivity to the team's Mobius/oneM2M MQTT setup.

### Robot and drone support

Sariel's code currently instantiates drone-A/B/C and has no separate ground-robot class or robot command receiver. Recorded Mission supports logs from these three drones. Its robot_task_suggestions are proposed tasks, with execution unconfirmed. Ground robots still need agreed coordinates, agent IDs, task reception and feedback interfaces. Showing agents in the interface does not establish integration of both robots and drones.

### mV fields and file checks

smoke_raw and co_raw use finite, nonnegative mV values, including decimals, replacing the 0..4095 integer ADC-count restriction. temperature/degC, humidity/percent, pressure/hPa and presence/boolean retain their units. This update changes only Selina's module and preserves original value/source/observed_at fields.

Of 25 sample_data.json records, 24 match the contract and one lacks a timestamp. All 216 node01_sample.jsonl records match. sim_sensor_data.jsonl has 178 compatible A/B records and 89 zone-C records with no CSE mapping. Format compatibility alone does not authorize writes. The team still needs to confirm mV hazard thresholds. Older local-factory thresholds are illustrative.

### Team oneM2M MQTT binding

Broker 10.70.80.245:1883 currently has TLS disabled. Configure the node-01 password privately. MQTT username=node-01, AE originator=CNode01 and target=/id-mn-zone-a are separate parameters. Each connection generates a unique Client ID automatically.

- Subscribe to /oneM2M/resp/CNode01/id-mn-zone-a/json first and wait for SUBACK before sending requests.
- Publish requests to /oneM2M/req/CNode01/id-mn-zone-a/json.
- Generate a new rqi for each request. con contains the serialized eight-field JSON string. Keep the oneM2M topic format.
- Record a successful create only when rqi matches and rsc=2001. Broker PUBACK alone does not confirm CSE success.

Preview the file from mec-app/deploy first. Sending is disabled by default:

```powershell
.\.venv\Scripts\python.exe -m rescuetwin --config config.team.example.json native-import --zone A --file ..\..\simulation\node01_sample.jsonl
```

To upload, set TEAM_NODE01_MQTT_PASSWORD privately in your terminal and append `--send` to the same command. Only node-01/A sensor data bound to that account is allowed. Independent drone/MEC identities need their own authorized identities. A format error blocks the entire batch by default. Explicit `--skip-invalid` skips incompatible rows. Logs capture partial success. Repeating --send creates new CINs, with no global deduplication guarantee.

### MEC platform and APIs

Hosted page: https://82.112.253.59/ . Current personal Sandbox=sbx42843tk, MEP=mep1. Read-only checks on 2026-11-10 succeeded for the endpoints below. Hosted access provides no shared-VM SSH/Kubernetes authorization, and no shared-VM credentials were available.

| Purpose | Request URL | Current use |
|---|---|---|
| MEC013 Location | GET https://82.112.253.59/sbx42843tk/mep1/location/v3/queries/users | Retrieve UE positions. Supports ?address=10.10.0.1 |
| MEC012 RNIS | GET https://82.112.253.59/sbx42843tk/mep1/rni/v2/queries/rab_info | Retrieve UE/cell/RAB information |
| MEC011 timing | GET https://82.112.253.59/sbx42843tk/mep1/mec_app_support/v2/timing/current_time | Health check |
| MEC011 service discovery | GET https://82.112.253.59/sbx42843tk/mep1/mec_service_mgmt/v1/services | Retrieve actual service versions and endpoints |
| MEC021 AMS | https://82.112.253.59/sbx42843tk/mep1/amsi/v1 | Advertised by service discovery. Application registration/migration acceptance remains pending. |

The old Location v2 Try-it URL returned 404. Use v3 and the endpoints advertised by service discovery. Requests currently send Accept: application/json with HTTPS certificate validation enabled. These checks used no Bearer token, without making assumptions about authentication on other platforms.

Example request:

```http
GET /sbx42843tk/mep1/location/v3/queries/users?address=10.10.0.1 HTTP/1.1
Host: 82.112.253.59
Accept: application/json
```

The first UE from the actual list response is shown below. Other UEs and extra fields were omitted while preserving these values. This is a captured sample with time-dependent coordinates:

```json
{
  "userList": {
    "user": [
      {
        "address": "10.10.0.1",
        "accessPointId": "4g-macro-cell-6",
        "zoneId": "zone03",
        "timeStamp": {
          "nanoSeconds": 0,
          "seconds": 1794324352
        },
        "locationInfo": {
          "latitude": [
            43.735054
          ],
          "longitude": [
            7.422338
          ],
          "shape": 2
        }
      }
    ]
  }
}
```

RNIS response excerpt:

```json
{
  "cellUserInfo": [
    {
      "ecgi": {
        "cellId": "6060606",
        "plmn": {
          "mcc": "001",
          "mnc": "001"
        }
      },
      "ueInfo": [
        {
          "associateId": [
            {
              "type": 1,
              "value": "10.10.0.1"
            }
          ],
          "erabInfo": [
            {
              "erabId": 243,
              "erabQosParameters": {
                "qci": 80
              }
            }
          ]
        },
        {
          "associateId": [
            {
              "type": 1,
              "value": "10.1.0.1"
            }
          ],
          "erabInfo": [
            {
              "erabId": 2,
              "erabQosParameters": {
                "qci": 80
              }
            }
          ]
        }
      ]
    }
  ],
  "timeStamp": {
    "nanoSeconds": 0,
    "seconds": 1794324352
  }
}
```

Location provider appInstanceId=298b2c0c-7efa-45d3-8b47-8ab3c009b845, serviceId=f8a0d2ab-9977-4d71-8c4f-354b4d59fb32. The former identifies the service provider. The RescueTwins consumer needs its own application ID. UE addresses and drone IDs had not yet been mapped at this stage. Monaco latitude/longitude requires transformation before use as grid coordinates. Replay currently executes routes independently of MEC network positions.

### Pending integration

Reads of both MNs through Mobius still timed out from Selina's computer. Zihan needs to confirm the current IP, routing, firewall and listen address. The runtime lacked an MQTT password and had made no real uploads to team servers. Independent drone/MEC AEs/ACPs, zone C access, mV hazard thresholds, UE/coordinate mapping, robot execution feedback, the application's MEC021 instance/callback/target, and actual shared-VM/Kubernetes deployment remained pending.


On other operating systems, create a Python 3.13 environment in mec-app/deploy, install requirements.txt and run `python -m rescuetwin.demo --config config.team.example.json --recorded-only`. To run the original simulation, also install requirements-simulation.txt and run `python simulation/rescue_sim.py --no-anim` from the repository root. Omitting that flag requires a graphical desktop.


## 2026-11-11 local mapping and robot updates

Demo UE mapping: drone-A=10.10.0.1, drone-B=10.1.0.1, drone-C=10.100.0.1. See mec_recorded_mapping in config.team.example.json. This demo convention has no verified correspondence to physical drones.

Recorded Mission's Read MEC positions reads Location and projects it onto a 30x20 grid. Demo bounds are south=43.7314, north=43.7514, west=7.4202, east=7.4402, with X east and Y south. Out-of-bounds positions are rejected. The linear projection lacks site calibration and preserves historical trajectories/CSE routing. It is excluded from automatic dispatch. Projected positions can fall inside simulated walls. A real site needs map control-point calibration.

Zone C comes from Sariel's simulation/rescue_sim.py, including lines 22, 44 and 46. Only MN-A/B are currently available. Zone C remains in the local simulation with simulation_cse_routing.C=null, without silently mapping it to B.

Robot Task Suggestions adds Simulate local robot. Local robot-01 validates routes and generates accepted/moving/completed feedback and a route. It sends no hardware commands and preserves Sariel's executor. Feedback uses POST /api/control, action=robot_feedback. The feedback object includes task_id, command_id, robot_id, executor=local_robot_simulation, increasing sequence, status and position. Incorrect identities, stale/conflicting receipts and incorrect completion positions are rejected. Replay changes, uploads and restarts cancel unfinished tasks. The service binds to loopback only.

Twenty-seven coordinate/feedback/original-replay tests passed. Browser checks confirmed local robot completion feedback. The latest real Location GET on 2026-11-11 returned 404 after earlier 200 responses. Check the personal Sandbox's active scenario and Try-it URL. Retain the demo configuration and retry when the service recovers. Live network-position integration remains unverified.

File replay and Hosted API integration currently need no VM. Docker Desktop can verify application containers and local Kubernetes manifests. Full ETSI MEC platform administration is a separate deployment. If platform management or underlying network emulation is needed, follow STF_685 on an administrable Ubuntu machine or VM. A shared VM is required only when selected as the deployment target or explicitly required for acceptance.

Zihan's troubleshooting order: confirm the wired IP, check that 8001/8002/7599/1883 listen on a reachable interface, perform a GET with oneM2M headers against the laptop's LAN IP, check source-limited inbound firewall rules, and confirm routable connectivity or VPN between both computers. Establish TCP reachability with Test-NetConnection before inspecting Originator/ACP permissions. ACP changes cannot fix a TCP timeout.
