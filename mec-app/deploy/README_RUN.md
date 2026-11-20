# Run the MEC service, simulation, and Dashboard

Install Python 3.13. Run the Windows commands from the repository root in PowerShell.

## LiveSwarm demo

```powershell
powershell -ExecutionPolicy Bypass -File "$PWD\mec-app\deploy\start-demo.ps1" -Mode LiveSwarm
```

Open <http://127.0.0.1:8765/#overview>. The demo starts exploring automatically with eight drones on an 80×40 grid split into zones A and B. Add `-DroneCount 6` to run six drones. LiveSwarm runs locally without an MQTT broker, CSE, or Hosted Sandbox.

- **Overview** shows the fleet size, coverage, shared cells, and map fusion rounds. Select **Exploration** for the shared map or **Temperature** and **Smoke** for the fire model.
- **Run Fire Demo** adds a fire. The drones use local observations to avoid known hazards. Choose **Run Rescue Loop**, or place a fire at a sensor on the map, to see that sensor's values change quickly.
- **Sensors & Messages** samples the current simulation once per second. Temperature and smoke reflect conditions at each sensor's location, so a sensor far from the fire may stay near its baseline. The table identifies these readings as simulation data.
- **Agent Status** shows each drone's target and route. Setting a manual target overrides that drone's autonomous movement until you choose **Resume swarm control**.

The default **Embedded Fusion** runs inside the simulator. To use the separate HTTP Fusion service, start `SwarmService` in one terminal, then run this in another:

```powershell
$env:PYTHONPATH = "$PWD\mec-app\deploy"
& "$PWD\mec-app\deploy\.venv\Scripts\python.exe" -m rescuetwin.demo --live-swarm --swarm-url http://127.0.0.1:8780
```

If the HTTP service fails, the simulation pauses and shows the error. The `live_factory` settings in `mec-app/deploy/config.example.json` define the grid width and height, zone boundary (`zone_x`), cell size, drone count, sensing radius, and service URL. The default cells are 2 m wide, giving a 160 × 80 m map with the A/B boundary at 80 m. Changing the layout or drone list starts a new fusion mission and resets coverage.

## Two-drone experiment and recorded replay

This experiment uses a separate 50×30 map with drones A and B, historical node-01 observations, and simulated node-02 data.

**1. Start the independent swarm service** and leave the first terminal open. It listens on `http://127.0.0.1:8780`.

```powershell
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode SwarmService
```

**2. Run the simulation** in a second terminal. It connects to the swarm service, exports log files, and displays the animation. Use `SwarmHeadless` for logs without animation, or add `-Benchmark` to run the 20-round comparison. If it cannot connect, check that the first terminal is still running.

```powershell
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode Swarm
```

**3. Open the recorded Dashboard** in a third terminal. It can also run alone to view logs you already have. The launcher installs missing dependencies on first use.

```powershell
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode Recorded
```

Open <http://127.0.0.1:8765/#recorded>. After another simulation run, restart the Dashboard or upload the updated `simulation/sim_sensor_data.jsonl` and `simulation/sim_agent_log.jsonl` from the page. To export into a separate directory, pass `--output-dir` to `mec-app/deploy/tools/run_swarm.py`.

## Read the latest node-01 temperature from CSE

Follow the [oneM2M MQTT read guide](docs/mqtt-read-handoff.md). Copy `config.mqtt-read.example.json` to `config.mqtt-read.local.json`. Enter the broker address, account, and oneM2M read identity, and provide the password through an environment variable. This command runs separately from the Dashboard and simulator.

## Private team settings

Copy `config.team.example.json` to `config.team.local.json` and fill in your own CSE, broker, and Sandbox endpoints and IDs. Git ignores the local file. Pass `--config config.team.local.json` to `check-team` or `native-import`. The Windows `Recorded` launcher uses the local file if present. The example config is sufficient for offline playback. Keep passwords and tokens in environment variables.

## Run on Ubuntu

With Python 3.13 and `venv` installed, set up the environment from the repository root:

```bash
python3.13 -m venv mec-app/deploy/.venv
mec-app/deploy/.venv/bin/python -m pip install -r mec-app/deploy/requirements.txt -r mec-app/deploy/requirements-simulation.txt
```

Start Fusion from `mec-app/deploy`:

```bash
cd mec-app/deploy
.venv/bin/python -m rescuetwin.swarm_service
```

Run the two-drone experiment from the repository root in a separate terminal:

```bash
SWARM_MEC_URL=http://127.0.0.1:8780 mec-app/deploy/.venv/bin/python mec-app/deploy/tools/run_swarm.py --quick --no-anim
```

Start recorded playback from `mec-app/deploy`:

```bash
.venv/bin/python -m rescuetwin.demo --config config.team.example.json --recorded-only
```

## Run the swarm service and simulator on separate computers

The simulator must be able to reach port 8780 on the MEC host. On the MEC host, run this from `mec-app/deploy`:

```bash
export SWARM_API_TOKEN="replace-with-your-token"
.venv/bin/python -m rescuetwin.swarm_service --host 0.0.0.0
```

On the Windows computer running the simulator, use the MEC host's private address and the same token:

```powershell
$env:SWARM_MEC_URL = "http://MEC_HOST_PRIVATE_IP:8780"
$env:SWARM_API_TOKEN = "replace-with-the-same-token"
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode Swarm
```

Use HTTP on a trusted private network. For remote access, configure HTTPS. For container deployment, use [Docker Compose](compose.swarm.yaml) or the [Kubernetes manifest](k8s/swarm.yaml) and fill in your host settings. Local tests verified the service and simulator. Deployment to the team's MEC host still needs verification.

## How swarm coordination works

- `simulation/rescue_sim.py` contains the base model, sensing, hazard avoidance, pheromone decay, and log export.
- `rescuetwin/layout.py` defines the geometry used by the demos. Fusion validates each mission's map and drone IDs.
- `rescuetwin/swarm_service.py` merges local observations and checks for duplicate or expired reports. `rescuetwin/swarm_logic.py` assigns tasks using the expected gain in coverage and the cost of the route. It reserves each next position, prevents shared cells and direct swaps, and replans when hazards change.
- `tools/run_swarm.py` connects the base simulator to the service. `Swarm` and `SwarmHeadless` use this adapter. Run `simulation/rescue_sim.py` directly for the base model.

`POST /v1/reports` accepts an optional `position` with `x` and `y` grid coordinates. The `coordination` response includes `assignments`, `actions`, `bid`, `information_gain`, and `path`. The adapter requires a service version that returns these coordination fields.

Pheromone values represent exploration history and decay over simulation ticks. Hazard values come from the model. Hardware sensor calibration, physical drone control, and MEC 021 application handover require separate validation.

## Clean generated runtime caches

From the repository root, preview or remove `pytest-*`, `npm-cache`, and `relocation-temp` under `runtime`. The script keeps databases and reports.

```powershell
# Preview cache targets without changing files.
powershell -ExecutionPolicy Bypass -File "$PWD\mec-app\deploy\tools\clean-runtime.ps1" -WhatIf
# Remove the listed caches.
powershell -ExecutionPolicy Bypass -File "$PWD\mec-app\deploy\tools\clean-runtime.ps1"
```
