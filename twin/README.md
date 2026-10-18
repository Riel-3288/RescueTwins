# RescueTwin minimal local twin

Real latest readings, accessed through Mobius IN-CSE → ACME MN-CSE → node-01.
Python 3.12 standard library only; no npm or new Python packages required.

## Start

Keep Docker/Mobius and ACME MN running first. From the RescueTwins repository root:

```powershell
# First run only: register an independent AE on Mobius and add read-only permission.
.\.venv\Scripts\python.exe .\twin\server.py --setup

# Every use: keep this terminal open.
.\.venv\Scripts\python.exe .\twin\server.py
```

Open http://127.0.0.1:8080 . Stop with Ctrl+C. If 8080 is in use, add `--port 8081`.
Use the URL served by Python; do not double-click index.html or use Live Server.

## Behavior

- Polls latest temperature, humidity, pressure, presence, smoke_raw and co_raw every 5 seconds.
- No generated sensor data, no fallback to MN when Mobius fails, no direct browser CSE requests.
- Empty records, invalid measurements, and errors are shown separately.
- Data received over 60 seconds ago is labeled historical. Freshness uses CSE record creation time,
  because sensor clocks may be missing or incorrect. The device's observed_at remains visible in details.
- Source `simulation` is explicitly labeled. `physical` is only what the producer reports, not independently verified.
- Shows a node schematic, not a real floor plan. No danger thresholds or rescue advice are inferred.
- The webpage AE is `rescuetwin-web` on Mobius (`CRescueTwinWeb`). Its cross-CSE originator is
  `/Mobius4/CRescueTwinWeb`. A separate `twin-reader-v1` ACP grants RETRIEVE only on six containers.
- Existing sensor upload policies are preserved. Setup validates that the webpage cannot update a container.
- A full public deployment and application authentication are outside this local prototype.

## Configuration

Defaults target the existing local setup. Optional process environment variables:

```powershell
$env:TWIN_MOBIUS_URL = 'http://127.0.0.1:7599'
$env:TWIN_MN_URL = 'http://127.0.0.1:8001/~/id-mn-zone-a/mn-zone-a'
```

`TWIN_MN_ADMIN` can change the setup-only MN admin identity (default `CAdmin`). Do not commit credentials.
Current CSE paths, identity prefix and node are intentionally fixed to the project's Zone A architecture.
No Mobius admin credential is read or stored by this application.

## Troubleshooting

- Run `--setup` only with both CSEs reachable. On a partial setup, correct the cause then rerun safely.
- Connection error: confirm Docker, MN's HTTP listener, CSR pointOfAccess and host.docker.internal connectivity.
- Permission error: rerun setup and inspect its output; do not switch the runtime to administrator credentials.
- HTTP 404: container may be empty or missing; it is not interpreted as an online device.
- A registered AE and successful GET prove a read route, not that the ESP32 is currently connected.

## Checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\twin -p 'test_*.py'
```
