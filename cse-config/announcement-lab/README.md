# Isolated ACME announcement lab

This lab verifies standard resource announcement on **ACME to ACME**. It does not
replace the main Zone A MN, Mobius IN, existing dashboard or MQTT broker.

| Role | CSE-ID | Name | HTTP port |
|---|---|---|---|
| Test IN | /id-annc-lab-in | annc-lab-in | 8010 |
| Test MN | /id-annc-lab-mn | annc-lab-mn | 8011 |

Both bind only to Windows loopback. MQTT is disabled in this lab. Configuration
templates carry no real secret; the launcher generates local secrets and runtime
files in `acme-announcement-lab/`, protected by a local ignore-all `.gitignore`.

## Reproduce

From the RescueTwins repository root, using the existing virtual environment:

```powershell
# Start both test CSEs, run the integration test and stop only these owned CSEs.
.\.venv\Scripts\python.exe .\script\run_announcement_lab.py --check
```

The launcher refuses to use occupied ports and does not stop other processes.
See each lab runtime directory's runtime.log if a CSE fails to start.

For interactive inspection instead:

```powershell
.\.venv\Scripts\python.exe .\script\run_announcement_lab.py
```

Keep that terminal open. Open `http://127.0.0.1:8010/webui` with Base RI
`id-annc-lab-in`, or port 8011 with Base RI `id-annc-lab-mn`; both use CAdmin.
In another terminal run:

```powershell
.\.venv\Scripts\python.exe .\script\acme_announcement_smoke_test.py
```

Ctrl+C in the launcher stops only its test pair. Standard deregistration can remove
announced resources from IN at shutdown, so inspect the IN while the pair is running.
The JSON report preserves observations and resource representations from the test.

## What is tested

1. MN registration into this test IN.
2. A container with at pointing to the test IN becomes a real containerAnnc (10003).
3. Label-filtered discovery on IN finds it and its lnk points back to the source.
4. Updating the source's announced label replaces that attribute on IN.
5. Two immutable CIN records, each explicitly configured for announcement, appear as
   contentInstanceAnnc (10004), with matching serialized map-summary data.

The payloads are synthetic, explicitly labelled simulation and scoped to the lab.
Container announcement alone is not interpreted as automatic announcement of all its
data. CIN content is immutable; phase 2 creates a new record, not an update of phase 1.
Each run uses new resource names, so it cannot overwrite an earlier test or production
resource. Test data remains in the local lab DB; use the disposable lab only for checks.

Report: `docs/acme-announcement-test-report.json`. A passed report supports the claim
“ACME-to-ACME announcement verified in an isolated lab.” It does not support the claim
“Announcement into Mobius works” or “The production Twin uses announced data.”
