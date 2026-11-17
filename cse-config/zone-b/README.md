# Zone B MN-CSE (same-computer development deployment)

This is a separate ACME MN-CSE, not the announcement lab and not a second physical MEC host.

| Setting | Value |
|---|---|
| CSE-ID | `/id-mn-zone-b` |
| CSE name | `mn-zone-b` |
| HTTP port | `8002` |
| Registrar | Mobius IN-CSE `/Mobius4`, name `Mobius`, HTTP `7599` |
| Provider ID | `//acme.example.com` |
| Database | Independent TinyDB under `acme-zone-b` |
| MQTT | Enabled after running the MQTT setup below; shared broker on port 1883 |
| TLS | Not enabled |

From the repository root, with Docker Desktop and Mobius running:

```powershell
.\.venv\Scripts\python.exe -X utf8 .\script\setup_zone_b.py --start
```

This generates a local secret, starts MN-B in the background if port 8002 is free,
and verifies a test record directly and through Mobius. Re-running checks existing
identities/policies without replacing them. It writes only the isolated
`zone-b-connection-test/_connection_test` container, retaining 20 records.
The process keeps running after the setup command exits. Logs: `acme-zone-b/runtime.log`.

Alternatively run ACME in a visible VS Code terminal (only if port 8002 is free):

```powershell
.\.venv\Scripts\python.exe -X utf8 -m acmecse --base-directory .\acme-zone-b
```

Web UI: `http://127.0.0.1:8002/webui`, Base RI `id-mn-zone-b`, Originator `CAdmin`
(local administration only).

Cross-CSE test request:

```http
GET http://127.0.0.1:7599/~/id-mn-zone-b/mn-zone-b/zone-b-connection-test/_connection_test/la
X-M2M-Origin: /id-mn-zone-b/CZoneBConnectionTest
X-M2M-RI: <fresh-uuid>
X-M2M-RVI: 4
Accept: application/json
```

External clients replace `127.0.0.1` with a reachable Windows LAN IP; HTTP 8002
requires a suitable firewall rule and peer connectivity test before claiming LAN access.
`host.docker.internal:8002` is advertised for Docker-to-Windows routing, not for ESP32 clients.

Future work: provision real Zone B agents/devices and role-specific policies;
integrate the twin;
test subscriptions and application handover between zones. Starting two MNs
does not implement handover or map synchronization.

## MQTT setup and verification

```powershell
.\.venv\Scripts\python.exe -X utf8 .\script\setup_zone_b_mqtt.py
```

This configures the existing shared broker with a separate MN-B service account
`acme-zone-b` and diagnostic client account `zone-b-test`, restarts the broker and
MN-B, and checks MQTT CREATE/RETRIEVE plus exact-record retrieval through Mobius.
It preserves unrelated broker account hashes. The broker restart briefly disconnects
all its MQTT clients, so this is a setup/repair command, not the everyday launcher.
Use `setup_zone_b.py --start` for routine starts after configuration.

- Broker: reachable Windows LAN IP, port `1883`; TLS currently disabled.
- Test AE Originator: `CZoneBConnectionTest`.
- Request topic: `/oneM2M/req/CZoneBConnectionTest/id-mn-zone-b/json`.
- Response topic: `/oneM2M/resp/CZoneBConnectionTest/id-mn-zone-b/json`.
- Test CREATE target: `/id-mn-zone-b/mn-zone-b/zone-b-connection-test/_connection_test`.
- Use a unique MQTT client ID for every simultaneous client.
- Generated credentials: `.mqtt-local/zone-b-credentials.json`, ignored by Git.
  Do not share the service account; provide test credentials privately only if needed.
- Results: `docs/zone-b-mqtt-test-report.json`.

The test account/AE is for diagnostics, not a real sensor node. MQTT broker login
and oneM2M ACP are separate checks. Broker topic ACL isolation is not configured
by this setup; separate usernames alone do not restrict access to topic names.
