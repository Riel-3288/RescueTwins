# Read Zone A temperature through MQTT

Read the latest CSE temperature record. This performs a read-only operation and requires broker connectivity, without starting Simulation, Dashboard or Sandbox.

## 1. Install dependencies

Open PowerShell in the project's `mec-app/deploy` directory:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Skip the first command if `.venv` already exists.

## 2. Enter connection parameters

Copy the configuration file once:

```powershell
Copy-Item config.mqtt-read.example.json config.mqtt-read.local.json
```

Open **`mec-app/deploy/config.mqtt-read.local.json`** in VS Code and fill in:

| Field | Value |
|---|---|
| `host` | Reachable broker IP or domain, without `http://` |
| `port` | Broker port, `1883` in the template. Adjust for the actual broker. |
| `username` | MQTT account with read access |
| `originator` | oneM2M read identity for this connection, separate from the MQTT username |
| `cse_id` | Target CSE ID, `id-mn-zone-a` in the template |
| `target` | Full temperature path, `/id-mn-zone-a/mn-zone-a/node-01/temperature/la` in the template |
| `rvi` | oneM2M release supported by the CSE, `4` in the template |
| `password_env` | Keep `MEC_READ_MQTT_PASSWORD` |
| `timeout_s` | Response timeout in seconds, default `10` |

Replace all `REPLACE_...` placeholders. Check the template's CSE ID and temperature path against the server.

The MQTT account needs request-topic publish and response-topic subscribe permissions. The Originator needs read access to the target resource.
Upload permission alone does not guarantee read permission.

The program generates topics from `originator` and `cse_id` automatically:

```text
/oneM2M/req/<originator>/<cse_id>/json
/oneM2M/resp/<originator>/<cse_id>/json
```

Use an `originator` without slashes here. Adjust the code if the server uses a different topic format.

## 3. Enter the password and read

Enter the password in the same PowerShell terminal. Input is hidden:

```powershell
$brokerPassword = Read-Host 'Broker password' -AsSecureString
$env:MEC_READ_MQTT_PASSWORD = [System.Net.NetworkCredential]::new('', $brokerPassword).Password
```

First validate the configuration. This step does not connect to the broker:

```powershell
.\.venv\Scripts\python.exe -m rescuetwin.native_read --config config.mqtt-read.local.json
```

Then read one record:

```powershell
.\.venv\Scripts\python.exe -m rescuetwin.native_read --config config.mqtt-read.local.json --read
```

## 4. Inspect the result

The result appears in the terminal and is saved to **`mec-app/deploy/runtime/mqtt-read-latest.json`**.

- `complete: true`, `rsc: 2000`: successful read.
- `content`: returned data.
- `cin`: original record returned by the CSE.
- `detail`: failure reason.

A configuration-only check returns `complete: false`, as expected. Each run overwrites the result file.
The latest stored record may be old. Read its sampling timestamp. `received_at` records the current retrieval time.
Read results are not currently displayed automatically in the dashboard.

## Ubuntu commands

Run from `mec-app/deploy`, with Python 3.13 and venv installed:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config.mqtt-read.example.json config.mqtt-read.local.json
```

After editing the local configuration using the table above, run:

```bash
read -r -s -p 'Broker password: ' MEC_READ_MQTT_PASSWORD
export MEC_READ_MQTT_PASSWORD
.venv/bin/python -m rescuetwin.native_read --config config.mqtt-read.local.json
.venv/bin/python -m rescuetwin.native_read --config config.mqtt-read.local.json --read
```

Skip creation steps for environments or local configurations that already exist.

## Common errors

| Symptom | Check |
|---|---|
| Connection timeout or refusal | Broker address, port, listener, firewall, campus network or VPN |
| Connection rejected | MQTT account, password and TLS settings |
| Subscription rejected | Subscribe permission for the response topic |
| CSE response timeout | Request-topic publish permission, CSE MQTT connection, response topic and `rvi` |
| `rsc=4103` | Originator read permission (ACP) |
| `rsc=4004` | Resource path and availability of a temperature record |

If the broker requires TLS, add `ca_file` to the local configuration and update the port. Add `cert_file` and `key_file` if client certificates are required.

Keep passwords out of JSON. Git ignores local `config.*.local.json` files and `runtime/` results.
