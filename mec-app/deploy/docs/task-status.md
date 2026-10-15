# Task status and evidence

Date: 2026-10-15. Feature freeze target: 2026-11-08.

| Task | Current result | Remaining evidence / input |
|---|---|---|
| Access public try + ESTIMED shared | Public Sandbox login and authenticated ETSI Labs access to all three supplied projects confirmed | Actual runtime access, shared host deployment rights and service endpoints still require verification |
| Read TR-0077 + TR-0086 | TR-0077 reviewed; authenticated access confirms TR-0086 Developer Guide and its platform/device/application/handover flow | Check the official scenarios and draft guide against the assigned runtime |
| Factory map, two zones, agent tracks | Generated AdvantEDGE scenario, GeoJSON polygons and 10 UE tracks | Import and capture the running scenario in assigned sandbox |
| Public Discord question | Prepared `discord-question.md` | User posts to the appropriate public organizer channel |
| Deploy MN-CSE A on host A | CSE renderer, container and K8s host selector provided; local ACME MN-A tested | Shared node, image registry, persistent volume and deployment evidence |
| Deploy edge app | Deployable bridge and MEC-IPE integration apps provided | Deploy to shared host; swarm logic remains teammate-owned |
| Deploy MN-CSE B on host B | Separate local ACME MN-B tested; host B selector in manifest | Actual different-host placement evidence |
| MEC-IPE Location → CSE | Working with contract-compatible MEC013 responses and real CSEs | ETSI live URL, actual UE address mappings and response captures |
| Geofence handover | App-layer A→B re-registration and target position write tested | Live Sandbox crossing; MEC021 context transfer/continuity is pending |
| Non-location MEC service | MEC012 RNIS mapped into CSE; reverse RNIS query supported | Actual shared/public RNIS query evidence |
| Bidirectional MEC ↔ oneM2M | Commands queue → MEC queries → correlated CSE responses tested | Live MEC evidence and final ACPs for consuming applications |
| Latency/packet-loss emulation | Shipped-schema Sandbox Control client and test parameters supplied | Apply to actual emulated traffic; measure physical link behavior |
| IN-CSE path latency test | Real ACME↔ACME Mcc registration and transit tested; matched RTT probes | Mobius interoperability, shared TLS Mcc and complete hazard/reroute trace |
| TLS on CSEs + MQTT | HTTPS + bearer auth and MQTT mutual TLS tested with generated demo CA | Shared certificates/addresses; Mosquitto ACL runtime and hardware provisioning validation |
| Feature freeze | Checklist and dated build/deploy steps provided | Execute end-to-end acceptance by 2026-11-08 |
| MEC/Sandbox/deployment writing | Article draft supplied | Replace pending descriptions with measured evidence after live tests |
| README build + deploy guide | Provided with local, hardware contract, TLS and shared deployment steps | Integrated into `mec-app/deploy/`; review and merge the feature branch into main |

Local integration tests run actual ACME 2026.5.3 and AMQTT 0.11.3. MEC REST responses use a local fixture. The AMQTT TLS test enforces client certificates; the production broker template uses Mosquitto. This does not certify a live ETSI deployment, ACME–Mobius interoperability or Mosquitto ACL behavior.

Before marking the demo complete, save: (1) MEC API responses, (2) device-to-CSE read-back, (3) distinct host placement, (4) boundary crossing and target registration, (5) per-command responses, (6) TLS acceptance/rejection, (7) impairment settings, and (8) CSV traces with workload and timing definitions.
