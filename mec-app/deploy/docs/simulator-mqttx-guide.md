> On 2026-11-11, the version changed to a 50x30 map, zones A/B and drone-A/B, with a separate MEC swarm-fusion service. Follow [README_RUN.md](../README_RUN.md) for current startup and deployment instructions. The earlier three-zone instructions below are retained as historical notes.

# Local fire simulator, Dashboard and MQTTX

This simplified factory model supports integration testing. It has no calibrated fire CFD model. The simulator, dashboard, broker
and local demo CSEs can run on Windows / Python 3.13 without Docker.
The module's demo is located in Selina's directory. Teammates' simulation/, twin/ and cse-config/ files were preserved.

## Run modes

Use Python 3.13 and run the unified launcher from this directory. The default replays team files with the broker, bridge and CSE left off:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-demo.ps1 -Mode Recorded
```

Open http://127.0.0.1:8765/#recorded and upload files or use recordings in the simulation directory. This mode uses requirements.txt and installs missing dependencies on first launch. Ctrl+C stops the web service.

| Mode | Purpose | Dependencies | Scope |
|---|---|---|---|
| Recorded (default) | JSON/JSONL upload, replay, drone planning and robot task suggestion export | requirements.txt | Offline, with no device commands |
| SwarmHeadless | Random/swarm benchmark and JSONL export using Sariel's original script | requirements-simulation.txt | Three drones. Regenerates the two simulation output files. |
| Swarm | Same workflow, followed by a 2D animation | requirements-simulation.txt | Requires a graphical desktop. Runs benchmarks before animation. |
| LocalDemo | Legacy four-agent factory demo and local MQTT | requirements-demo.txt | Standalone example, separate from the team's oneM2M integration |
| LocalCse | Legacy factory demo with temporary local ACME A/B/IN | requirements-demo.txt | Tests an isolated MQTT/HTTP example, separate from the team Mobius environment |

For example, `powershell -ExecutionPolicy Bypass -File .\start-demo.ps1 -Mode SwarmHeadless`. `-CheckOnly` checks/prepares dependencies for the selected mode. `-WithoutCse` supports the older workflow and selects LocalDemo.

Team integration uses oneM2M MQTT binding only. See the handoff notes and native-import. Keep plain rescuetwin topics and bridge/provision commands limited to their separate demo environments.

The MQTTX plain-topic sections below apply only to legacy isolated LocalDemo/LocalCse. Their port is 18883. The team broker is 10.70.80.245:1883, requires an account and uses /oneM2M requests/responses.

## MQTTX configuration

MQTTX is a client. Start the broker described above first. Installing MQTTX alone does not create a broker.

### Terminal success messages and MQTTX connection state

After publishing, `MQTT broker acknowledged publication` reports successful broker acknowledgment
of the message. Updated messages explicitly start with `SUCCESS`. The command uses its own short-lived connection and exits after sending.
MQTTX uses a separate connection. Click **Connect (▶)** in its top-right corner.

If it shows `Client not connected`, select **RescueTwins Local** on the left and confirm
`127.0.0.1:18883`, then click Connect. On success, hovering over the top-right button shows **Disconnect**,
and subscribed messages increase the message count. Saving connection settings, subscriptions or a JSON draft does not establish a connection.

Before sending, select JSON, QoS 1 and Retain off. Update the test timestamp to current UTC. Future timestamps display
`Future timestamp`, a data-freshness warning independent of the broker connection.

1. Click New Connection, usually shown as `+`.
2. Enter these settings and click Connect.

| MQTTX field | Local demo value |
|---|---|
| Name | RescueTwins Local |
| Client ID | mqttx-rescuetwins-selina (unique among connected clients) |
| Host / protocol | mqtt:// |
| Host / address | 127.0.0.1 |
| Port | 18883 (separate from 1883 and web port 8765) |
| MQTT Version | 3.1.1 |
| Username / Password | Leave blank |
| SSL/TLS | Off for the local demo |
| Clean Session | On |
| Keep Alive | 60 seconds |

If Host is one text box, enter `mqtt://127.0.0.1`. If protocol/address are separate fields, use the table above.

3. After connecting, click New Subscription.
4. Set Topic to `rescuetwin/#` and QoS to `1`. After saving, you should see three nodes and six reading types. The simulator sends changes and periodic heartbeats, reducing ordinary reading frequency during backlog.
5. Also subscribe to `rescuetwin-ack/#` for successful CSE storage acknowledgments.
6. For agent positions and zone-crossing events, subscribe to `rescuetwin-sim/#`.

| Topic | Purpose |
|---|---|
| rescuetwin/A/node-01/temperature | Zone A node-01 temperature |
| rescuetwin/A/node-02/smoke_raw | Zone A node-02 smoke in mV |
| rescuetwin/B/node-03/temperature | Zone B node-03 temperature |
| rescuetwin-ack/A/node-01 | Bridge notification that the record was written to the CSE |
| rescuetwin-sim/agents/agent-01 | Local simulated coordinates in metres, separate from MEC013 responses |
| rescuetwin-sim/events | Local geofence boundary-crossing events, with no MEC021 completion claim |

## Send one message manually in MQTTX

In the publish area, set Topic to `rescuetwin/A/node-01/temperature`, QoS to `1`, Retain **off**, and Payload format to JSON:

```json
{
  "device_id": "node-01",
  "zone_id": "A",
  "sensor_type": "temperature",
  "value": 65.5,
  "unit": "degC",
  "source": "simulation",
  "quality": "valid",
  "observed_at": "2026-10-16T08:30:00Z"
}
```

Replace `observed_at` with the UTC send time. Update it for each test to avoid deduplication or stale-data warnings.
After publishing, your own message should appear in the `rescuetwin/#` subscription. The simulator continuously publishes
node-01 data every second, so the next simulated reading will subsequently replace the latest displayed value.

To generate a message with the current timestamp, run this in a second terminal:

```powershell
.\.venv\Scripts\python.exe -m rescuetwin publish sample-temperature.json --now
```

Use `source: physical` only for real device readings. `value` must be a JSON number rather than a string. `presence`
uses true/false. Messages must contain exactly the agreed eight fields. Device/zone/sensor must match the topic.

## Run a complete scenario

1. Click **Run Fire Demo** in the dashboard to reset the map, add a fire in zone A and start the simulation.
2. Observe temperature and smoke changing in adjacent cells around the fire. Walls block diffusion.
3. Switch between **Temperature / Smoke / Layout**. Agents use built-in A* to avoid hazardous cells.
4. Filter node-01 temperature/smoke_raw in MQTTX to inspect readings matching the simulated map.
5. **Readings Stored in CSE** should keep increasing. Verify by reading the CSE directly:

```powershell
$readHeaders = @{
  'X-M2M-Origin' = 'CNode01'
  'X-M2M-RI' = [guid]::NewGuid().ToString('N')
  'X-M2M-RVI' = '4'
  'Accept' = 'application/json'
}
$latestReading = Invoke-RestMethod -Uri 'http://127.0.0.1:18001/~/id-mn-a/cse-mn-a/node-01/temperature/la' -Headers $readHeaders
$latestReading.'m2m:cin'.con | ConvertFrom-Json
```

6. Use **Map Click Action** to add a fire, edit obstacles or set a new goal for the selected agent.
7. After an agent crosses x=50m, a zone event is recorded following a 2m buffer and two consecutive confirmations.
8. Export sensor CSV or state JSON as integration evidence.

## Dashboard pages and map controls

The sidebar and narrow-screen top navigation switch between these three pages:

- **Factory Overview**: live 3D map, 2D Grid, temperature/smoke layers, fire/obstacle editing, trends and events.
- **Sensors & Messages**: filter readings by device, zone, source, sensor, quality and freshness. Inspect opens the original
  eight-field JSON, Copy JSON copies it, and Export Filtered CSV exports filtered latest readings. The message log keeps the latest 500 entries
  in the current browser session. Pause Log pauses this buffer while MQTT and the simulator continue. Reopening the page clears the browser buffer.
- **Agent Status**: filter/select agents and inspect zone, state, current position, goal, distance, replan count,
  local risk and route. Goal coordinates use metres and snap to 2m grid-cell centres. Apply Goal submits the goal. Resume movement if paused.
  Choose Goal on Map returns to the map for selection. Locate in 3D centres the camera on the selected agent.

For 3D mouse controls, drag with the left button to rotate, **hold and drag the middle wheel to pan**, and scroll to zoom. Right-drag also
pans. On touchpads without a middle button, use Shift + left-drag. **Reset View** restores the default camera. A ground click executes
Map Click Action. Camera drags place no fires. Bottom-right controls rotate/zoom. Switch to 2D Grid if WebGL is unavailable.

The 3D building, flames and smoke visualize the same 2D model, without adding 3D fire physics. For web-source editing/building, see
the [module README](../README.md#editing-the-dashboard). An existing service normally needs only a browser refresh. After Python service changes, press
Ctrl+C in the original terminal, then rerun start-demo.ps1 to avoid starting two bridges.

## Dashboard metrics

- Map temperature/smoke come from the model. They do not establish actual factory measurements. Smoke is a normalized simulated intensity.
- MQTT received counts come from real subscription callbacks. PUBACK indicates broker acknowledgment and includes simulator telemetry and positions.
- CSE stored counts come from delivered bridge-outbox state and accumulate across restarts. Resetting the map preserves CSE history.
- physical and simulation are displayed separately. Fresh valid external MQTT alerts create sensor hazard-exclusion areas and inspection tasks. They preserve the physical-model temperature and add no artificial flames.
- The sensor table shows the latest received value of each type. Missing/invalid data is excluded from valid readings, and stale data is marked separately.
- The Overview historical CSV retains up to 10,000 valid telemetry records from the current run. Sensors CSV exports the currently filtered
  latest readings. Trends retain 180 simulated seconds of samples.
- Pause stops environmental evolution while change/heartbeat telemetry continues. 1x/2x/4x affect simulation time only.

## Model and integration scope

The 50x30 grid uses 2m cells, fixed random seed 7 and a fixed 0.25s step. Heat/smoke diffuse discretely and decay
between non-wall four-neighbour cells. Sources inject continuously and neighbouring cells ignite using simplified rules. Fuel depletion, ventilation, combustion chemistry,
building thermodynamics and real CO-concentration models are absent. mV values are illustrative and cannot be converted directly to ppm.
Route planning combines the simulated map with hazard-exclusion areas from simple sensor thresholds. Sensor ranges are assumed. The example planner is separate from the teammate's swarm algorithm.

This version neither injects UE positions into the public Sandbox nor automatically maps Monaco UEs to factory agents, and it does not invoke
MEC021. MEC-IPE will integrate actual MEC services, followed by the team's algorithm and dashboard/resource interfaces.

## Suggested next steps

1. **Stabilize the data path**: run continuously and watch CSE pending growth, distinguishing reception and write rates. Measure
   observation-to-MQTT-to-CIN-to-route-update timing. Handle retries, backlog, deduplication and retention. SQLite has state indexes,
   but CSE throughput still needs verification. Preserve the outbox when investigating backlog.
2. **Integrate real hardware and algorithms**: connect physical ESP32 readings and map nodes to coordinates. Agree with teammates
   on hazard-map and agent command/feedback interfaces to close the alert, dispatch, replan and completion loop. Built-in A*
   already uses threshold-based exclusion areas and can trigger local inspection tasks. Real ESP32 and teammate-swarm integration remain pending.
3. **Complete actual MEC/oneM2M integration**: obtain access details for the shared VM/two MEC hosts, deploy MN-A/B
   and applications, and verify real Location/RNIS, bidirectional MEC-IPE, MN-to-IN registration and routing. Accept zone events, CSE switching,
   subscription continuity and MEC021 application-state migration separately. Local boundary-crossing animation alone provides insufficient evidence.
4. **Collect demo evidence**: fix the scenario and seed. Compare fire/no fire, normal/impaired network and direct-MN/via-IN paths.
   Export P50/P95 latency, success rate, missing records, replan count and arrival times. Add alert acknowledgment, recovery and event IDs.
5. **Finish deployment and presentation**: validate CSE HTTPS, MQTT mTLS/device ACLs and restart recovery on actual hosts. Add remote-dashboard
   login/access control. Then add scenario save/load, replay, floors and device models, prioritizing functionality before decorative 3D effects.

## Troubleshooting

- Client not connected: MQTTX itself is disconnected. Click Connect at the top right. Connection errors require connectivity fixes rather than changes to the eight-field JSON.
- JSON SyntaxError: press Ctrl+A in the payload editor and paste the complete JSON. Check for extra closing braces or other characters.
- New messages missing: clear the Topic/Message search filters and scroll to the bottom. Older messages remain in the list.
- Connection refused: the broker is stopped, its terminal was closed, or the port was entered as 1883.
- Port already in use: a demo/Compose instance is already running. Reuse it or stop it with Ctrl+C in its original terminal.
- MQTT received but CSE pending grows: check A/B CSEs and AE identities, beyond the MQTTX connection indicator.
- CSE quarantined grows: usually a format/permission issue. Inspect runtime/demo/bridge.sqlite and terminal output.
- No messages: subscribe to `rescuetwin/#`, check the spelling, and keep the launcher terminal open.
- External device cannot reach 127.0.0.1: that address refers to the current computer. Hardware access needs a reachable, authenticated broker configuration.

Official MQTTX documentation: https://mqttx.app/docs


## CSE backlog recovery and rescue loop

Restart the existing launcher once to load the new Python service. Stop the old terminal with Ctrl+C first,
then run `powershell -ExecutionPolicy Bypass -File .\start-demo.ps1`. Do not run two copies.
The outbox is migrated in place; historical records are kept. No queue or CSE history is deleted.
The old queue takes time to replay. The Overview now shows **CSE Delivery Health**: waiting/in-flight records,
oldest wait, incoming and stored records/s, and the recent delivery error. A positive net drain indicates
that delivery is outpacing ingestion; rates need a ten-second warm-up. The on-disk `delivered` total is cumulative.

Changes in this version:

- MQTT ingestion commits batches with SQLite WAL and synchronous FULL before acknowledging QoS 1.
- One delivery worker per MN-CSE isolates A/B failures. Claims and completion updates are batched; an OS lock
  prevents two bridges from owning the same outbox. Interrupted claims are replayed on restart.
- CIN names remain deterministic for safe HTTP retries. Existing eight-field telemetry containers are unchanged.
- Simulator values are published on meaningful changes and a five-second heartbeat. With 200+ queued records,
  ordinary changes are limited to once per five seconds and the heartbeat becomes fifteen seconds. Alarm/clear
  threshold crossings bypass that delay. External physical publishers are never sampled or discarded.
- Rescue events have delivery priority over telemetry backfill and are stored under
  `<MN_BASE_URL>/rescue-control/events` using originator `CRescueControl`.

### Run the loop

1. Open Overview and click **Run Rescue Loop**. This starts a repeatable simulated fire at node-01.
2. In **Rescue Operations**, follow `incident opened → task dispatched → task accepted → task moving → task completed`.
3. A red exclusion area is added near the reporting sensor. The local A* executor routes to a safe inspection
   staging cell outside that area. If there is no safe route, the system reports blocked status.
4. Click **Acknowledge** to record operator acknowledgment. **Dispatch again** requests a fresh inspection.
5. Clear Fires and let sensor readings cool below the recovery thresholds. Each triggered channel needs two fresh
   clear samples; the incident then resolves and its exclusion area is removed. Inspection completion alone
   does not resolve the incident. Reset closes simulated incidents explicitly; it preserves physical alerts.

For actual hardware, retain the agreed eight-field MQTT contract and set `source` to `physical` only for real
measurements. `rescue.sensor_positions` maps each device to factory coordinates in metres. Defaults use
60/50 degC for temperature trigger/clear, 2500/2000 mV for smoke, and 2300/1800 mV for CO. These are
uncalibrated demo thresholds. Invalid, duplicate, older, future (>5s), or stale (>35s) observations cannot start
or clear an alert. A stale active alert stays active until fresh recovery data arrives.

MQTTX can inspect these additional topics (QoS 1, retain off):

| Topic | Meaning |
|---|---|
| `rescuetwin-control/commands/+` | Inspection command, with command_id/task_id/incident_id/agent_id/goal |
| `rescuetwin-control/feedback/+` | Accepted, moving, blocked, completed or failed receipt with the same IDs |
| `rescuetwin-control/events` | Incident and task timeline |

The controller state and timeline persist in `runtime/demo/rescue.sqlite`. After service restart, unfinished
local tasks are cancelled because the simulator resets its agents. Use Dispatch again for a still-active alert.
Commands retry until acknowledged (30s timeout). Execution has a 120 simulated-second timeout; pausing the
simulation pauses that execution timer. A manual goal cancels that agent's current automated task.

The active executor is **local_simulation**. This implements MQTT command/receipt integration and CSE audit
storage; it does not control an ESP32 actuator or replace the team's swarm algorithm. Replace `LocalExecutor`
with the teammate/robot adapter, preserving the command and feedback IDs, before claiming real robot control.
The loop currently uses live MQTT observations for prompt reactions while CSE writes proceed asynchronously.
During historical replay, CSE `/la` can lag; inspect the reading's `observed_at` before using it for decisions.
The local MQTT broker permits these topics; production mTLS/ACL roles for the controller/executor still need
separate deployment configuration and acceptance testing.

### Local CSE startup and shutdown

Use Ctrl+C in the demo terminal for a normal stop. The launcher asks each local CSE to flush its database
before exiting. `stop-demo.ps1` remains a forced recovery option for a frozen process.

If a local CSE fails to start, inspect both `runtime/demo/<zone>.log` and
`runtime/demo/<zone>/logs/`. ACME errors are now saved even in headless mode. Do not delete the queue or
the CSE data folders to work around a JSON database error; preserve the files before attempting recovery.


## Recorded JSON integration and drone missions

Use this mode for the team files currently uploaded by hand. In `mec-app/deploy`, run:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m rescuetwin.demo --config config.team.example.json --recorded-only
```

Open http://127.0.0.1:8765/#recorded. This starts the local dashboard without a broker or bridge. It does not connect to the team broker or write to Zihan's CSEs. The original Overview is a separate illustrative 50x30 model; **Recorded Mission uses Sariel's actual 30x20 grid** and does not mix their coordinates.

1. The page initially reads `simulation/sim_agent_log.jsonl` and `simulation/sim_sensor_data.jsonl` without editing them. Upload replacement sensor or agent files using the two file selectors. JSON objects, arrays, JSONL and a `m2m:cin.con` sensor wrapper are accepted, up to 4 MiB. A malformed agent file leaves the previous dataset intact. Data stays in memory for this session; export the mission before restarting.
2. Move the replay slider. Only positions and victim events at/before that step are visible to the planner. Original UTC times and `physical`/`simulation` provenance stay intact; historical physical data is not labelled live.
3. Select a drone and optionally enter a target in **grid cells**. With no target, choose an already reported victim or a patrol waypoint. The planner checks walls/configured hazard cells, keeps a return route, and reserves 10 simulated energy units (one per travelled cell). Insufficient energy produces return-home or blocked status. This is a route preview with no altitude/flight dynamics or real battery model.
4. Simulate the round trip and export Mission JSON. This never sends flight commands or CSE registration requests. Robot tasks are suggestions with no assigned robot; a robot task/acknowledgment adapter is still required. Replay, upload, or a new plan invalidates the previous execution reference.

### Current file compatibility

- `sim_agent_log.jsonl`: 280 entries: 3 nodes, 267 drone positions, 3 victim reports, 7 recorded zone handovers. The log has `drone-A`, `drone-B`, `drone-C`; no separately identified ground robot. These are recorded movements, not proof of CSE or MEC021 handover.
- `sim_sensor_data.jsonl`: 267 smoke entries in `mV`, covering A/B/C. All can be inspected as recorded data, but 178 A/B records match the updated mV sensor contract; the 89 zone-C records still have no CSE mapping. Do not silently send C to MN-B.
- `node01_sample.jsonl`: 216 historical physical readings, all 216 now match the agreed mV fields. Compatible rows may be inspected, but uploads are not a claim of current device connectivity.
- `sample_data.json`: existing 25-row file remains supported. The agreed mV contract accepts 24 rows; one missing timestamp remains rejected.
- Smoke mV has no agreed hazard threshold. It remains unclassified by the drone planner. Only explicit unit-matched thresholds are used. Do not interpret a wall-clear route as a certified fire-safe flight.

### Team CSE access and independent identities

`config.team.example.json` records the supplied endpoints (IP `10.70.80.245`; MN-A 8001, MN-B 8002, Mobius 7599) with explicit no-HTTP-auth configuration. It keeps the existing TLS production template unchanged. The team profile disables HTTP writes, provisioning and the raw MQTT-to-HTTP bridge. Explicit native-import --send permits only the configured node-01 sensor upload through oneM2M binding. Existing originators are used only in the two documented read-only checks.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m rescuetwin --config config.team.example.json check-team
```

This writes a local result to `runtime/team-check.json`, returns a failing exit code for incomplete checks, and sends only GET requests. On 2026-11-10, the Location check succeeded; both Mobius transit reads timed out from this computer. Direct MN-A/MN-B checks also timed out. Recheck current IP, same-LAN/VPN routing, service bind address, Windows firewall and running services with Zihan. His localhost verification is separate from connectivity on Selina's computer.

The team broker uses **oneM2M MQTT binding** at `10.70.80.245:1883`. The `native_mqtt` section documents the two supplied accounts and password environment variable names; no passwords are stored. The local dashboard broker remains separate. It is not a bridge into those CSEs. Request/response topics must follow the supplied `/oneM2M/...` structure, with a unique MQTT Client ID and matching `rqi`/successful `rsc`. Independent drone/MEC AEs, their ACPs and a dedicated MQTT account/ACL must be agreed before enabling writes. Do not use the physical node's account for another application. No topic ACL isolation or TLS is asserted for the team's current broker.

### MEC endpoint and IDs

Live service discovery and GET checks on 2026-11-10 confirmed Location at `https://82.112.253.59/sbx42843tk/mep1/location/v3/queries/users` (200); v2 returned 404. The verified v3 root is saved in the team profile. HTTPS validation remains enabled; `system_trust` uses OS trusted roots. Discovered addresses were `10.10.0.1`, `10.1.0.1`, `10.100.0.1`. No address is automatically mapped to a drone or to grid zone A/B/C.

`298b2c0c-7efa-45d3-8b47-8ab3c009b845` belongs to the **Location provider**; `f8a0d2ab-9977-4d71-8c4f-354b4d59fb32` is its service ID. The RescueTwins consumer `app_instance_id` remains empty. Obtain it by creating your own application instance. MEC021 registration, externally reachable callback, two-instance migration and live CSE writes remain pending. JSON upload/replay itself needs no public callback.

Still agree with the team: zone C's CSE routing, mV calibration or unit-specific thresholds, per-drone AE/ACP and broker permissions, UE-to-agent mapping and a coordinate transform, and the real robot's task/feedback interface. Keep these explicit; changing only a zone label does not implement handover.


### mV and run-mode verification (2026-11-10)

- Full Python regression: 58 passed with three local ACME startup/HTTP timeouts. After stopping heavier parallel tasks, the three failures and native-import tests were rerun together, yielding eight passes without timeout changes or skipped assertions.
- A new local test passed for direct oneM2M MQTT upload of an mV record followed by exact HTTP read-back. It made no writes to the team broker. One-shot native clients use unique Client IDs and temporary sessions, leaving no unused subscriptions after disconnecting.
- Seven frontend model tests passed. Edge browser checks covered uploads, timeline, drone planning/round trips, JSON download, older pages and mobile layout.
- Sariel's original code ran all twenty random/swarm benchmark rounds in a temporary copy, taking about 58 seconds and exporting 267 sensor and 280 agent entries. Plotting and the first five actual animation callbacks passed. Teammates still need to open the graphical desktop window locally.
- Recorded, SwarmHeadless and LocalCse dependency entry points passed. Recorded's standalone web startup passed, as did LocalDemo MQTT messaging. LocalCse real ACME/MQTT exchange passed in regression, independently of team Mobius connectivity.
- Checksums of fourteen original teammate files were preserved. Smoke/CO use mV. Valid voltages support nonnegative decimals without ADC-count limits. Raw quality=invalid readings retain the existing handling and remain invalid measurements.
- Hosted Location/RNIS/MEC011 GETs succeeded. Team-Mobius reads still timed out. MQTT passwords and independent drone/robot permissions still required configuration in the actual environment.


## 2026-11-11 local mapping and robot updates

Demo UE mapping: drone-A=10.10.0.1, drone-B=10.1.0.1, drone-C=10.100.0.1. See mec_recorded_mapping in config.team.example.json. This demo convention has no verified correspondence to physical drones.

Recorded Mission's Read MEC positions reads Location and projects it onto a 30x20 grid. Demo bounds are south=43.7314, north=43.7514, west=7.4202, east=7.4402, with X east and Y south. Out-of-bounds positions are rejected. The linear projection lacks site calibration and preserves historical trajectories/CSE routing. It is excluded from automatic dispatch. Projected positions can fall inside simulated walls. A real site needs map control-point calibration.

Zone C comes from Sariel's simulation/rescue_sim.py, including lines 22, 44 and 46. Only MN-A/B are currently available. Zone C remains in the local simulation with simulation_cse_routing.C=null, without silently mapping it to B.

Robot Task Suggestions adds Simulate local robot. Local robot-01 validates routes and generates accepted/moving/completed feedback and a route. It sends no hardware commands and preserves Sariel's executor. Feedback uses POST /api/control, action=robot_feedback. The feedback object includes task_id, command_id, robot_id, executor=local_robot_simulation, increasing sequence, status and position. Incorrect identities, stale/conflicting receipts and incorrect completion positions are rejected. Replay changes, uploads and restarts cancel unfinished tasks. The service binds to loopback only.

Twenty-seven coordinate/feedback/original-replay tests passed. Browser checks confirmed local robot completion feedback. The latest real Location GET on 2026-11-11 returned 404 after earlier 200 responses. Check the personal Sandbox's active scenario and Try-it URL. Retain the demo configuration and retry when the service recovers. Live network-position integration remains unverified.

File replay and Hosted API integration currently need no VM. Docker Desktop can verify application containers and local Kubernetes manifests. Full ETSI MEC platform administration is a separate deployment. If platform management or underlying network emulation is needed, follow STF_685 on an administrable Ubuntu machine or VM. A shared VM is required only when selected as the deployment target or explicitly required for acceptance.

Zihan's troubleshooting order: confirm the wired IP, check that 8001/8002/7599/1883 listen on a reachable interface, perform a GET with oneM2M headers against the laptop's LAN IP, check source-limited inbound firewall rules, and confirm routable connectivity or VPN between both computers. Establish TCP reachability with Test-NetConnection before inspecting Originator/ACP permissions. ACP changes cannot fix a TCP timeout.
