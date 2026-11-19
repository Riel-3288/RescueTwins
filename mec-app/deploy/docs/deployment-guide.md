# MQTT, MEC and Sandbox deployment guide

This guide covers operational interfaces, TLS, Sandbox and shared-host deployment. For the first local demo, start with the
[Simulator + MQTTX guide](simulator-mqttx-guide.md). Branch workflows are in the [team collaboration guide](team-collaboration.md).

## Environment and deployment prerequisites

- Development: Python 3.13. start-demo.ps1 can launch the local demo without Docker.
- Compose: Docker Desktop's Linux engine is required. Avoid running another quick demo on the same ports.
- Shared VM / Kubernetes: organizers must provide access, nodes, registry, MEC URLs and a certificate scheme.
- Multiple processes on one computer support local integration. Deployment across two MEC hosts still requires acceptance in the actual environment.
- The CSE teammate supplies deployed MN/IN configuration. Verify ACME-Mobius interoperability through testing.

## Data flow

```text
ESP32 / simulation
  └─ MQTT QoS 1：rescuetwin/{zone}/{device}/{sensor}
       └─ bridge → SQLite durable outbox → HTTP(S) oneM2M CIN → MN-CSE A / B

ETSI MEC013 Location ─┐
ETSI MEC012 RNIS ─────┴─ MEC-IPE → oneM2M CIN
oneM2M requests CIN → MEC-IPE → MEC REST query → oneM2M responses CIN

agent position → factory geofence → prepare target AE → target position CIN
              → handover event CIN → persist new serving zone
```

`native-publish` also sends request primitives using the TS-0010 native oneM2M MQTT binding.
It provides an alternative to the plain-JSON MQTT + HTTP bridge. Both paths passed local tests.
Deployment templates use the bridge path to integrate with the teammate's current HTTP interfaces.

## Run local tests first

Python 3.13 is required. From the team repository root, enter this module before running subsequent commands:

```powershell
cd mec-app/deploy
```

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Tests temporarily start a real MQTT broker, two ACME MN-CSEs and one ACME IN-CSE, then stop them automatically.
Location and RNIS use local services matching the API contracts. Online ETSI services are outside these tests.
TLS tests use a temporary private CA. Test certificates are excluded from deliverables.

## Continuous local operation

Start Docker Desktop's Linux engine, then run:

```powershell
docker compose -f compose.local.yaml up -d --build
.\.venv\Scripts\python.exe -m rescuetwin provision
```

Run this in a second terminal:

```powershell
.\.venv\Scripts\python.exe -m rescuetwin bridge
```

Publish a simulated reading from a third terminal:

```powershell
.\.venv\Scripts\python.exe -m rescuetwin publish sample-temperature.json --now
.\.venv\Scripts\python.exe -m rescuetwin status
```

`status` should show `delivered`. MQTT PUBACK confirms broker reception. Read the latest CSE record to confirm storage.
Read back from `<A_BASE_URL>/node-01/temperature/la` using `X-M2M-Origin: CNode01`,
a fresh `X-M2M-RI`, `X-M2M-RVI: 4` and `Accept: application/json`.

Local Compose binds unencrypted ports to `127.0.0.1`. ESP32 devices cannot reach that loopback interface directly.
Hardware integration uses the reachable TLS deployment address below. The hardware teammate places each node's own client certificate and key in its firmware.

## MQTT and hardware contract

Example topic: `rescuetwin/A/node-01/temperature`, QoS `1`, retain `false`.

```json
{
  "device_id": "node-01",
  "zone_id": "A",
  "sensor_type": "temperature",
  "value": 28.6,
  "unit": "degC",
  "source": "physical",
  "quality": "valid",
  "observed_at": "2026-10-15T08:30:00Z"
}
```

| sensor_type | unit | value type |
|---|---|---|
| temperature | degC | Finite number |
| humidity | percent | 0..100 |
| pressure | hPa | Positive number |
| smoke_raw / co_raw | mV | Finite, nonnegative integer or decimal |
| presence | boolean | JSON `true` / `false` |

`zone_id` is A/B, `source` is physical/simulation and `quality` is valid/invalid.
Failed sensors may send `quality: invalid, value: null`. Edge decisions should exclude invalid data.
Numeric-range validation alone does not establish fire or CO concentration. mV values require separate interpretation from ppm.
Field names, device/zone in the topic and device mappings in configuration must agree. Extra fields are also rejected.

Each reading becomes:

```json
{"m2m:cin":{"rn":"cin-<stable-record-hash>","cnf":"application/json:0","con":"<eight-field-JSON-string>"}}
```

POST to `<BASE_URL>/<device_id>/<sensor_type>` using `application/json;ty=4`.
Originator comes from configuration, defaulting to `CNode01` for node-01. Generate a new `X-M2M-RI` for each HTTP request.
Verify the actual `aei` returned by registration, without deriving it from the AE name alone.
`provision` stops on a different AE identity and preserves existing resources and the teammate's ACPs.

## Connect to public try and ESTIMED shared environments

1. Sign in to the [public MEC Sandbox](https://try-mec.etsi.org/) and select a network scenario.
2. Select Location and RNIS in the API console and copy each **Try-it in your MEC Application** URL.
   The URL must include your sandbox instance and MEC platform. Obtain API addresses from the console.
3. Copy `config.example.json` to `config.local.json` and set `mec.location_url` and `mec.rnis_url`.
   Enter the API bases. The code appends `/queries/users` or `/queries/rab_info`.
4. Update the `agents` mapping with actual `address` values from Location queries. Default `10.10.0.1` applies only to our custom scenario.
   The factory origin must also match scenario coordinates. Unmapped users do not affect handover.
5. For MEC011 READY, set `app_support_url` and invoke it with an app instance ID created by the organizers or Sandbox API:

```powershell
.\.venv\Scripts\python.exe -m rescuetwin --config config.local.json confirm-ready YOUR_APP_INSTANCE_ID
.\.venv\Scripts\python.exe -m rescuetwin --config config.local.json ipe
```

Public try provides simulated MEC APIs. Signing in or creating an app instance ID leaves actual Docker-image deployment to a MEC host as a separate step.
Organizers still need to provide SSH/Kubernetes access, node names, an image registry and MEC service URLs for the shared environment.
MEC011 termination subscriptions and real MEC021 application-state transfer also need further integration testing.

## Custom factory and network tests

`scenario/factory.yaml` is an AdvantEDGE 1.11 scenario with a 100m x 60m factory, zones A/B,
two logical EDGE hosts, ten mobile UEs and a cloud DC.
`scenario/factory-map.geojson` supplies the twin map. Only explicitly mapped agents enter MEC-IPE. Other UEs are scenario demo devices.
The factory boundary is x=50m, with a default 2m buffer and confirmation from two consecutive fresh positions before switching.
Stale or duplicate timestamps, missing coordinates and out-of-factory positions do not trigger handover.

Regenerate the scenario:

```powershell
.\.venv\Scripts\python.exe tools/scenario.py
```

For local/shared environments with Sandbox Control access:

```powershell
.\.venv\Scripts\python.exe tools/sandbox.py --base-url https://YOUR_HOST/YOUR_SANDBOX/sandbox-ctrl/v1 import scenario/factory.yaml
.\.venv\Scripts\python.exe tools/sandbox.py --base-url https://YOUR_HOST/YOUR_SANDBOX/sandbox-ctrl/v1 activate rescuetwin-factory
.\.venv\Scripts\python.exe tools/sandbox.py --base-url https://YOUR_HOST/YOUR_SANDBOX/sandbox-ctrl/v1 network in-host --type DC --latency-ms 80 --jitter-ms 10 --loss-percent 2
```

Restore the network by setting all three parameters to zero only if those were the baseline values. Save the original configuration first and restore its actual values.
`packetLoss` is a percentage, so 2 means 2%. Confirm that actual application traffic traverses the emulated network.
Scenario impairment has no effect on requests that directly access localhost or external URLs outside that path.
Suggested cases are 0/20/80/150ms latency and 0/1/5% loss. Test edge and IN paths and record success rates and P50/P95.

## Bidirectional MEC and oneM2M

MEC to oneM2M: IPE polls Location/RNIS and stores original responses in `mec-ipe/location` and `mec-ipe/rnis`.
oneM2M to MEC: create a `mec-ipe/requests` CIN. IPE queries the relevant service and writes `mec-ipe/responses`.

```powershell
.\.venv\Scripts\python.exe -m rescuetwin --config config.local.json command get_location --address 10.10.0.1
.\.venv\Scripts\python.exe -m rescuetwin --config config.local.json command get_rnis
```

Each request has a `command_id`. IPE discovers all retained request CINs, supporting multiple queued requests beyond `/la`.
Correlate responses by `command_id`. Location/RNIS queries are currently supported, demonstrating bidirectional data calls.
Handover uses application-layer re-registration while preserving history in the old CSE. MEC021 context migration, subscription migration, old-registration deletion and robot-command migration remain unimplemented.

## Latency measurement

```powershell
.\.venv\Scripts\python.exe -m rescuetwin --config config.local.json benchmark --count 100 --output runtime/storage-rtt.csv
```

Without `VIA_IN`, this command compares direct CIN create-and-read round-trip times for A and IN.
After MN-IN registration, add:

```json
"VIA_IN": {
  "base_url": "http://IN_HOST:18000/~/id-mn-a/cse-mn-a",
  "originator": "/id-mn-a/CBenchmark"
}
```

This compares identical workloads going directly to MN-A and through IN-CSE forwarding to the same MN-A.
Use HTTPS URLs, appropriate CAs and authentication in the shared environment. The CSE teammate supplies TLS Mcc configuration.
Local tests verified ACME-to-ACME Mcc forwarding. ACME-to-Mobius interoperability still needs testing in this integration environment.
`tools/acme_config.py` leaves remote registration disabled by default. Set `CSE_REMOTE=true` and correct `REGISTRAR_HOST`/`REGISTRAR_PORT`
for local unencrypted federation testing. Shared deployments require their own appropriate settings.
Storage RTT measures create/read requests. The ESP32-reading-to-MEC-decision-to-agent-reroute latency requires separate measurement.
For final application metrics, hardware/algorithm teammates must supply shared event IDs and timestamp locations for observations and reroutes.

## TLS deployment on two MEC hosts

`k8s/template.yaml` needs an image address. The A/B nodeSelectors respectively use
`rescuetwin-mec-zone=A/B`. Bridge, MEC-IPE and broker are placed in A.
`CSE_REMOTE` defaults to false. The template IN is an ACME validation instance. Replace it with Mobius configuration from Zihan and then enable Mcc.
At the time of these deployment notes, this directory contained MEC-IPE and bridge as deployable edge integration applications. The swarm algorithm was outside that scope.

Build and push to an organizer-approved registry, then generate:

```powershell
docker build -f Dockerfile -t YOUR_REGISTRY/rescuetwin:2026-11-08 .
docker push YOUR_REGISTRY/rescuetwin:2026-11-08
.\.venv\Scripts\python.exe tools/k8s.py --image YOUR_REGISTRY/rescuetwin:2026-11-08 --config config.production.example.json
```

Set production-config MEC URLs and agent mappings first. Run `tools/certificates.py --output certs --hostname ACTUAL_HOSTNAME`
to generate a demo CA, server certificates, separate node client certificates and random CSE tokens.
Keep the CA key on the offline development computer. node-01 receives only `ca.crt`, `node-01.crt` and `node-01.key`.
HTTPS validates the CA and hostname. MQTT uses mTLS with ACLs based on certificate CNs.
HTTP uses a bearer token with existing oneM2M ACPs. Tokens are limited to trusted bridge/IPE or CSE operators. ESP32 uses its own MQTT identity.
Production deployments use certificates and authentication managed by the organizers/team.

After organizers confirm both nodes, the operator runs:

```powershell
kubectl create namespace rescuetwin
kubectl label node ACTUAL_NODE_A rescuetwin-mec-zone=A
kubectl label node ACTUAL_NODE_B rescuetwin-mec-zone=B
kubectl -n rescuetwin create secret generic cse-tokens --from-file=a=certs/a.token --from-file=b=certs/b.token --from-file=in=certs/in.token
kubectl -n rescuetwin create secret generic cse-a-certs --from-file=tls.crt=certs/cse-a.crt --from-file=tls.key=certs/cse-a.key
kubectl -n rescuetwin create secret generic cse-b-certs --from-file=tls.crt=certs/cse-b.crt --from-file=tls.key=certs/cse-b.key
kubectl -n rescuetwin create secret generic cse-in-certs --from-file=tls.crt=certs/cse-in.crt --from-file=tls.key=certs/cse-in.key
kubectl -n rescuetwin create secret generic bridge-certs --from-file=ca.crt=certs/ca.crt --from-file=bridge.crt=certs/bridge.crt --from-file=bridge.key=certs/bridge.key
kubectl -n rescuetwin create secret generic mqtt-certs --from-file=ca.crt=certs/ca.crt --from-file=broker.crt=certs/broker.crt --from-file=broker.key=certs/broker.key
kubectl apply -f k8s/template.yaml
kubectl -n rescuetwin rollout status deployment/cse-a
kubectl -n rescuetwin rollout status deployment/cse-b
kubectl -n rescuetwin exec deployment/bridge -- python -m rescuetwin --config /config/config.json provision
kubectl -n rescuetwin get pods -o wide
```

If a MEC service needs a token, create the `token` key of a `mec-access` secret from a local token file.
The broker Service defaults to ClusterIP. Before hardware access, organizers need to provide a suitable LoadBalancer/NodePort/private-network path
and place its actual DNS name in the broker certificate SAN. Obtain public ports from the deployment configuration.
Preserve pod placement, HTTPS read-back, device mTLS, original Location/RNIS responses and handover evidence during deployment acceptance.
Generated YAML alone provides no evidence of completed deployment.


## Integration acceptance

Save evidence in this order: MQTT messages, A/B CSE CIN read-back, actual MEC Location/RNIS responses,
placement on separate hosts, boundary crossing and target registration, bidirectional requests/responses, TLS acceptance/rejection, network-emulation parameters and measurement CSVs.

Local tests cover real MQTT/ACME, retries/deduplication, TLS and ACME Mcc. MEC API inputs use local test services.
The simulator uses a simplified hazard model and sample A*. Real fire prediction, the teammate's swarm algorithm and MEC021 migration require their own implementations and validation.

## Official references


- [oneM2M TR-0077：deployment options](https://specifications.onem2m.org/tr/tr-0077/latest/5.1/)
- [TR-0077：handover scenarios and gaps](https://specifications.onem2m.org/tr/tr-0077/latest/7.2/)
- [ESTIMED developer materials](https://estimed.etsi.org/dev_material/)
- [ESTIMED reports and specifications](https://estimed.etsi.org/reports_specifications/)
- [TS-0010：MQTT primitive mapping](https://specifications.onem2m.org/ts/ts-0010/latest/6.5/)
- [ACME HTTP configuration](https://acmecse.net/setup/Configuration-http/)
- [ACME MQTT configuration](https://acmecse.net/setup/Configuration-mqtt/)

Authenticated ETSI Labs access confirmed that
[TR-0086：Developer Guide oneM2M and MEC deployment](https://labs.etsi.org/rep/estimed/wp6/tr-0086/-/blob/50473eb37a47a3307097c543c8618be1947a171f/TR-0086-Developer_Guide_oneM2M_and_MEC_deployment.md)
exists. This version is marked v0.0.0, dated 2026-06-05, and still contains draft material.
Follow section 5.2 to start/register the platform, 5.3 to connect devices and 5.4 to deploy the MEC application, then verify handover in 5.5.
Official entry points and actual environment-access steps are in the [Simulator + MQTTX guide](simulator-mqttx-guide.md).


## Recorded inputs and deployment acceptance (2026-11-07)

Scope is mec-app/deploy in this repository. simulation/sample_data.json and the online schedule are read-only inputs.

## Data inputs

Default input is simulation/sample_data.json at the repository root, containing 25 records. Twenty-four match the current eight-field contract. One missing observed_at is quarantined. smoke_raw/co_raw use team-agreed mV units. Original source, values, units and historical UTC timestamps are preserved. A file's physical label provides provenance, without establishing current device connectivity. mV is retained as raw voltage. Hazard thresholds still need confirmation for this unit.

Run from PowerShell in mec-app/deploy:

```powershell
.\.venv\Scripts\python.exe -m rescuetwin samples
.\.venv\Scripts\python.exe -m rescuetwin replay-samples --skip-invalid
```

samples writes a report without connecting to a CSE. replay-samples rejects erroneous batches by default. --skip-invalid explicitly selects compatible rows and reports rejected rows too. Stable resource names and exact CIN read-back deduplicate reruns. Partial failures set complete=false. Fix the issue and rerun the same file.

start-demo.ps1 starts the existing map/agent model, but sensor readings default to a one-time file input sent after MQTT becomes ready. Timestamps are preserved, the file is not polled, and automatic temperature/smoke generation is disabled. Missing/incompatible rows appear in sensor_input in the dashboard status response. Generated sensors are available only for explicitly selected --generated-sensors illustrative runs.

## Week 4 acceptance scope

| Checklist task | Code in this round | Actual-environment acceptance |
|---|---|---|
| Bidirectional MEC/oneM2M | configure_mobility in a requests CIN performs PUT on the configured AMS registration, then writes a responses CIN. It accepts mapped UEs only and prevents commands from changing target app/service IDs. | Actual app_instance_id, mobility_service_id, ams_url and permissions are pending. Controlled API tests leave online AMS acceptance outstanding. |
| Network latency/loss | network-experiment reads the active scenario's unique netChar, saves the baseline, applies/measures each case and restores in finally. Failed restoration retains restored=false. | Requires Sandbox Control access and proof that actual traffic crosses the emulated network. traffic_path_verified defaults to false. Localhost RTT cannot establish network-emulation effects. |
| IN-CSE latency comparison (Must) | benchmark-samples uses the original sample, alternating direct edge A and true VIA_IN forwarding to MN-A for CIN creation and exact read-back. Each case retains failures and median/p95. Independent IN storage cannot substitute for VIA_IN. | Requires actual Mcc registration, routing and cross-CSE originators. Three local ACME processes establish local integration only. |
| CSE+MQTT TLS | Reuses and tests HTTPS CSEs, tokens, MQTT mutual certificates and rejection of incorrect CAs/missing identities. | Requires actual host SANs, certificates, tokens/ACPs and ports. Lab tests leave two-host MEC deployment acceptance outstanding. |
| 8 November freeze | Guides, tests and reproducible inputs were added within Selina's files. | Keep the milestone open until actual-environment evidence is available. |

## Test identical data through IN forwarding

Explicitly configure private config.local.json. Verify example identities against actual registrations:

```json
"VIA_IN": {
  "base_url": "http://127.0.0.1:18000/~/id-mn-a/cse-mn-a",
  "originators": {"node-01": "/id-mn-a/CNode01"}
}
```

Keep require_tls=true in production. VIA_IN also uses HTTPS and a trusted ca_file. Preserve certificate validation during checks.

```powershell
.\.venv\Scripts\python.exe -m rescuetwin --config config.local.json benchmark-samples --skip-invalid --count 3
```

This measures equal-workload storage-request RTT for create/read-back. ESP32-to-alert end-to-end latency requires separate measurement. Smoke/CO mV readings need thresholds in the same units, with ADC-count thresholds and ppm interpretations excluded.

## Bidirectional AMS configuration

Under mec, configure ams_url, registered mobility_service_id and app_instance_id. agents must already map the UE address. Then submit:

```powershell
.\.venv\Scripts\python.exe -m rescuetwin --config config.local.json command configure_mobility --address <MAPPED_UE>
.\.venv\Scripts\python.exe -m rescuetwin --config config.local.json ipe --once
```

Update tracked UEs only for the configured registration. GET first to verify its app ownership. Preserve existing tracked UEs and migration states. Set contextTransferState=0 only for new UEs, without reporting completed migration. command_id and persisted results provide deduplication. HTTP errors produce failure responses. Investigate and retry with a new command_id. Real AMS still needs READY/registration first.

## Authorized network experiments

Set mec.sandbox_control_url to the actual instance's sandbox-ctrl/v1. Before changes, the tool reads /active and proceeds only if it can identify a unique name+netChar for the matching element. Use the current scenario baseline.

```powershell
.\.venv\Scripts\python.exe -m rescuetwin --config config.local.json network-experiment --element <ACTUAL_ELEMENT_NAME> --element-type EDGE --case 0,0 --case 20,1 --case 80,5 --skip-invalid
```

The report includes original netChar, each benchmark and restoration results. Restoration is attempted after exceptions. Process termination or power failure can prevent finally, requiring manual restoration from the saved baseline. Avoid resetting values blindly to zero.

## Verification

Uses ACME 2026.5.3 and amqtt 0.11.3 pinned in requirements-test.txt, preserving the team's versions.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Results are described below. The deployment checklist still contains pending acceptance items.

This round recorded 46 full-regression passes, five final Week 4 passes after AMS merge updates, and six Week 4 + TLS passes using the Desktop project's own environment after copying back. SHA-256 checks matched all eleven teammate/original sample files. Tests used real local broker/CSE/Mcc. MEC writes and network restoration used controlled APIs. The online platform was untouched, and commit/push were pending at the time of this record.


## Full MEC platform administration (2026-11-11)


Hosted Sandbox allows scenario selection, simulated-UE control and API access. Underlying host administration requires separate access. Running the full platform means managing the Ubuntu host, Kubernetes, OAuth, administrator roles, scenario catalog and platform services, beyond application containers.

If full platform administration is later needed:

1. Prepare an administrable Ubuntu physical machine or VM with a stable reachable address. Use console/SSH for a local VM. The administrator supplies SSH and sudo access for a remote host.
2. Install the full platform following official STF_685 deploy/pyinfra/README.md and TR-0086 Annex B. Configure your own OAuth Client ID/Secret, platform administrators and scenarios. A personal Hosted Sandbox account grants a different access scope.
3. After scenario deployment, verify MEC011, Location/RNIS, your application instance, CSE/IPE and notification URLs. Query actual cluster namespaces from the platform.
4. For daily checks, run kubectl get nodes and kubectl get pods -A. Inspect services, events and logs in the relevant namespaces. Maintain scenarios, certificates, registrations, data and platform versions.

Docker Desktop Kubernetes can test your application manifests. A full ETSI MEC Sandbox is a separate installation. The current file demo can run before that installation. Follow official documentation for exact setup:
https://labs.etsi.org/rep/mec/etsi-mec-sandbox/-/tree/STF_685/deploy/pyinfra

Docker port-publishing reference: https://docs.docker.com/get-started/docker-concepts/running-containers/publishing-ports/
