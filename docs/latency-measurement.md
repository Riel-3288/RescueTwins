# Zone A latency measurement

Start Docker Desktop, the Zone A MQTT broker, ACME MN-A, and the Mobius IN-CSE.
Run the following from the RescueTwins repository root:

```powershell
.\.venv\Scripts\python.exe -X utf8 .\script\latency_recorder.py --preflight
.\.venv\Scripts\python.exe -X utf8 .\script\latency_recorder.py --pairs 100 --duration 180
```

Preflight publishes one synthetic MQTT CIN to `node-01/_connection_test` using
the existing ignored local device credentials file. It checks that both paths
observe the same CIN. The normal run waits for real Zone A `node-01` data.
Wait for `READY` before the sender starts a test run. Check the PC and ESP32 clock
sync and compare a real `ts_ms` with UTC before measuring. Have the sender log
each `seq`, `sensor_type`, `ts_ms`, and CREATE result. At six readings per five
seconds, 20 rounds provide 120 possible pairs. Keep the sender running until the
collector has 100 valid pairs or reaches its recording time limit.

- Edge uses ACME child-creation notifications delivered to a loopback callback.
- Cloud retrieves latest CINs through Mobius using the same computer's clock.
  The default target polling sweep interval is 0.5 seconds. The cloud number
  includes polling wait and request round-trip time. Mobius currently acknowledges
  incoming notifications without forwarding them to a local AE callback, so
  report this as a notification-versus-polling comparison.
- The receiver pairs by CIN resource ID, which avoids collisions after ESP32
  restarts and deduplicates retries of the same notification. Only the first
  observation on each path counts. Latest-CIN polling can miss intermediate
  records if more than one arrives between successful reads of a container.
- Temporary subscriptions are removed at the end and carry a short expiration
  time. No device firmware or existing sensor data is modified by the recorder.

Results are under the ignored local `acme-zone-a/latency` directory. Each run
creates `samples.csv`, `pairs.csv`, and `summary.json`. Negative or implausibly
large latency samples are retained in the CSV and counted separately from the
valid statistics. Unmatched counts measure receiver coverage. Use the sender log
to distinguish missed receipts, failed creates, and skipped polls. The synthetic
preflight shows connectivity only and should not be used as an evaluation result.

For a separate handover run, keep a sender log with a stable run ID and monotonic
sequence across the switch. Log `handover_start` and `handover_end` on the receiver
clock. Report the switch duration, the interval between the last pre-switch and
first post-switch successful arrivals, the normal send interval, and sent sequence
numbers absent from the receiver log after allowing time for late delivery.
