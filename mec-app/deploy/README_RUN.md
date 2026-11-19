# Running MEC, Simulation and Dashboard

Python 3.13 is required. Run the Windows commands below from the repository root.

## Live Swarm demo for judges (new)

Run this in a VS Code PowerShell terminal at the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File "$PWD\mec-app\deploy\start-demo.ps1" -Mode LiveSwarm
```

Open http://127.0.0.1:8765/#overview. Live exploration starts automatically with an 80x40 grid, eight drones and zones A/B. It runs without JSONL replay, a broker, CSE or Hosted Sandbox. Append `-DroneCount 6` to show six drones.

- Live Swarm Exploration at the top shows the actual fleet, coverage, shared-cell count and fusion rounds.
- Select the Exploration layer to view the shared exploration map. Temperature/Smoke still display the illustrative fire fields.
- Run Fire Demo adds fire to the live map. Drones avoid known hazards using their local observations.
- Agent Status shows the goals/routes of all eight drones. Setting a manual goal temporarily overrides swarm control for that drone. Resume swarm control restores autonomous exploration.
- Fusion is embedded by default. Each drone reports its position and local observations every round, and Factory moves using decisions from the merged map. The interface labels this mode Embedded Fusion, without claiming remote MEC deployment.

To demonstrate separate HTTP Fusion, run SwarmService below in terminal 1, then run this from the repository root in terminal 2:

```powershell
$env:PYTHONPATH = "$PWD\mec-app\deploy"
& "$PWD\mec-app\deploy\.venv\Scripts\python.exe" -m rescuetwin.demo --live-swarm --swarm-url http://127.0.0.1:8780
```

Service errors pause the swarm and display the reason. They do not silently switch to a local map.

Configuration is under live_factory in config.example.json: width, height, zone_x, cell_m, drone_count, radius, swarm_url. Defaults are 80, 40, 40, 2, 8, 2 and an empty string. zone_x uses grid coordinates. The default physical boundary is 80m and the map spans 160x80m. Changing the layout/registry creates a new Fusion mission and recalculates coverage.

Shared geometry is defined in rescuetwin/layout.py. Fusion validates each mission's map configuration and drone_ids, supporting sizes beyond two drones or 50x30. The original simulation/rescue_sim.py and its earlier replay remain 50x30. These are separate maps, and the teammate's file was preserved.

This round verified live exploration with six/eight drones, map fusion, HTTP service, manual goals, pause on disconnection, the existing sensor dispatch loop, and browser 3D/exploration layers. The live demo uses illustrative fire fields and grid motion. Physical flight control and real MEC021 migration remain outside this verification.

## Runtime cleanup

Fourteen pytest/npm/relocation cache directories were removed. Runtime also contains queue databases, demo CSE state and integration reports, which were retained. Tests now use the system temporary directory instead of recreating this batch of runtime/pytest-* directories.

## 1. Start the MEC service (original separate simulation entry point)

Open the first terminal and keep it open after running:

```powershell
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode SwarmService
```

Service URL: `http://127.0.0.1:8780`.

## 2. Run Simulation

Open a second terminal:

```powershell
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode Swarm
```

The simulator connects to the MEC service, exports records and displays an animation.

- For headless operation, replace `Swarm` with `SwarmHeadless`.
- Append `-Benchmark` for a 20-run comparison. This takes longer.
- A failed MEC connection stops the run. Confirm that the service in terminal 1 is running first.

## 3. Open the Dashboard

After Simulation finishes exporting its data, run this in a third terminal:

```powershell
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode Recorded
```

Open <http://127.0.0.1:8765/#recorded> in a browser.

The dashboard replays recorded data. After rerunning Simulation, restart the dashboard or upload these files again through the page:

- `simulation/sim_sensor_data.jsonl`
- `simulation/sim_agent_log.jsonl`

The first launch installs required dependencies automatically. The dashboard can also run independently to inspect existing recordings.

## Read CSE temperature

Follow the [MQTT read guide](docs/mqtt-read-handoff.md).

Enter the broker address, account and read identity in `mec-app/deploy/config.mqtt-read.local.json`.
Create this file by copying `config.mqtt-read.example.json`. Set the password through an environment variable.

MQTT reads run as a separate command. Their results currently remain separate from Simulation and Dashboard inputs.

## Running on Ubuntu

Create the environment and install dependencies from the repository root. Python 3.13 and venv must be installed:

```bash
python3.13 -m venv mec-app/deploy/.venv
mec-app/deploy/.venv/bin/python -m pip install -r mec-app/deploy/requirements.txt -r mec-app/deploy/requirements-simulation.txt
```

In terminal 1, enter `mec-app/deploy` and start MEC:

```bash
cd mec-app/deploy
.venv/bin/python -m rescuetwin.swarm_service
```

In terminal 2, run Simulation from the repository root:

```bash
SWARM_MEC_URL=http://127.0.0.1:8780 mec-app/deploy/.venv/bin/python mec-app/deploy/tools/run_swarm.py --quick --no-anim
```

In terminal 3, start the dashboard from `mec-app/deploy`:

```bash
.venv/bin/python -m rescuetwin.demo --config config.team.example.json --recorded-only
```

## Running on two computers

The MEC service and Simulation require network connectivity. Run this in `mec-app/deploy` on the MEC host:

```bash
export SWARM_API_TOKEN="REPLACE_WITH_YOUR_TOKEN"
.venv/bin/python -m rescuetwin.swarm_service --host 0.0.0.0
```

Allow the Simulation computer to access port 8780 on the MEC host. In PowerShell at the repository root on the Windows Simulation computer, set:

```powershell
$env:SWARM_MEC_URL = "http://MEC_PRIVATE_IP:8780"
$env:SWARM_API_TOKEN = "REPLACE_WITH_THE_SAME_SERVER_TOKEN"
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode Swarm
```

These HTTP commands target a trusted private network. Configure HTTPS for public-network access.
Container deployment uses `compose.swarm.yaml`. Kubernetes configuration is in `k8s/swarm.yaml`. Both need actual deployment parameters.

The current map is 50x30 with zones A/B and drone-A/B. node-01 uses historical sensor records and node-02 uses simulated data.
Local execution was verified. Ubuntu VM deployment still requires testing.


## Swarm logic implementation and scope

- simulation/rescue_sim.py: Sariel's base model, local pheromone/evaporation, obstacle avoidance, sensing and export. The original file is preserved.
- rescuetwin/swarm_service.py: a separate HTTP service merging the local maps/pheromones of two drones and handling duplicate/stale reports.
- rescuetwin/swarm_logic.py: the cooperative controller added in this round. It bids using information gain and route cost, avoids assigning nearby goals to both drones, and reserves the next positions to prevent same-cell and swap collisions. It retains useful goals and replans when hazards change.
- tools/run_swarm.py: the adapter connecting service assignments to the original Simulation. The Swarm/SwarmHeadless launcher modes use it. Running the original rescue_sim.py directly still uses the base algorithm.

Start SwarmService, then Swarm/SwarmHeadless using the same two-terminal workflow. HTTP /v1/reports accepts optional position={x,y}. The coordination response includes assignments, actions, bid, information_gain and path. Older map-fusion clients remain compatible. The updated execution adapter requires the new service and stops if the bidding policy is absent, without silently falling back to the local algorithm.

Pheromones represent exploration history and evaporate per tick. The hazard map uses normalized simulation values, unsuitable as calibrated mV thresholds. The policy performs greedy exploration bidding. Global optimization, physical device control, CSE switching and MEC021 migration have not been established by this work.

This round tested a complete two-drone run against a real HTTP service. With seed=1, it reached 100% map coverage in 183 steps and found two targets, checking wall avoidance, known hazards and inter-drone collisions at each step. These results apply to this simulation scenario, without establishing physical performance or multi-seed performance conclusions.

For a separate output directory, invoke the Python adapter from the repository root with --output-dir, preserving the original inputs. The default launcher still uses the two JSONL outputs under simulation, which the dashboard can replay.

Verification also covered standalone quick/no-anim exports to a temporary directory, stopping on authentication failure, duplicate-report stability and preserving mission state after invalid reports. Deployment configuration includes swarm_logic.py. Actual Docker/Ubuntu/Kubernetes deployment and commit/push were still pending at the time of this record.


### Live verification and cleanup record

- Live Factory with six/eight drones, dynamic dimensions/boundaries, separate HTTP Fusion, collision avoidance, manual takeover/resume, pause on Fusion errors and the existing MQTT/CSE dispatch loop passed.
- Thirty-four legacy protocol/record/field compatibility tests, seven frontend model tests and six final LiveSwarm tests passed. Browser checks covered eight moving drones, increasing coverage, 3D/exploration layers, eight control options and earlier replay.
- LiveSwarm local tasks/receipts require no broker. The interface distinguishes file readings from MQTT reception.
- Checksums of fifteen existing teammate files were preserved. At the time of this record, changes had not been committed/pushed. Container entry points imported successfully, while Docker/VM deployment remained unverified.
- Normal runtime cleanup removed fourteen cache directories. Six historical pytest caches in the older duplicate RescueTwins/mec-app/deploy/runtime remained protected by Windows permissions. Deletion and takeown attempts were denied, so those caches remain for a Windows administrator to handle. Databases and integration reports were retained.


To handle the remaining old caches, run the following from the repository root in administrator PowerShell. The script targets pytest-*, npm-cache and relocation-temp under runtime, excluding databases and reports:

```powershell
# Preview cache targets without changing files.
powershell -ExecutionPolicy Bypass -File "$PWD\mec-app\deploy\tools\clean-runtime.ps1" -WhatIf
# Remove the listed caches.
powershell -ExecutionPolicy Bypass -File "$PWD\mec-app\deploy\tools\clean-runtime.ps1"
```
