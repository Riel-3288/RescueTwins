# Verification record

Verified on 2026-10-15 (Asia/Kuala_Lumpur).

- Python 3.13, ACME CSE 2026.5.3, AMQTT 0.11.3, Paho MQTT 2.1.0.
- Latest verification after migration: **23 tests passed in 70.18 seconds**; no skipped tests. Run from the RescueTwins repository root with `python -m pytest mec-app/deploy/tests -q`.
- Real MQTT publication → durable bridge outbox → real ACME MN-CSE A/B → exact JSON read-back.
- Duplicate reading and lost-response retry use a single contentInstance resource.
- CSE connection failure leaves the record pending; restoring the endpoint delivers the original reading.
- Native TS-0010 MQTT CIN create returns oneM2M `2001` and is confirmed through HTTP read-back.
- A different AE originator is denied by the real CSE.
- MEC013 contract fixture drives agent registration on A, then target registration and position storage on B after two confirmed boundary samples.
- Two queued oneM2M commands both query the MEC fixture and receive correlated responses; another poll does not repeat completed commands.
- Real MN-CSE registration with a real IN-CSE is exercised. Matched write/read probes include an actual Mcc transit path to MN-A.
- MQTT with required client certificates → HTTPS CSE with bearer authentication is exercised end to end.
- Wrong CA certificates, missing MQTT client certificates and missing HTTP authentication are rejected.
- Docker Compose local configuration parses successfully after flattening the Dockerfile/Compose layout. The Kubernetes generator successfully renders the updated broker and template paths. Docker engine was unavailable on the workstation, so container builds/startup and Kubernetes execution were not performed.

The MEC API server in these tests is a local fixture. The MQTT mutual-TLS test uses AMQTT; the deploy template uses Mosquitto. No public/shared ETSI API, physical ESP32, Mobius instance, production Mosquitto ACL or live Kubernetes node was involved in this verification.

Tests are reproducible with the README instructions. Temporary CSE/broker processes were stopped after testing. Private keys, generated tokens, virtual environments and test databases are excluded from the deliverable archive.
