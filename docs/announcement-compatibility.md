# Announcement compatibility audit

## Scope

Check whether the deployed Mobius IN-CSE can host a standard announced container
(`containerAnnc`, type 10003). This does not test successful announcement, attribute
synchronization or discovery of an announced resource.

```powershell
.\.venv\Scripts\python.exe .\script\check_announcement_support.py
```

The generated `docs/announcement-support-report.json` contains actual API results.
Exit code 2 means the target does not advertise type 10003 and rejected its creation
with HTTP 400, 405 or 501.
The probe uses a unique test resource name. If creation unexpectedly succeeds, it
deletes only that new probe resource. It never changes node-01, its policies or Twin.

## Source evidence examined

The installed Mobius repository reports version 4.24.5 in `package.json`.
Its `features/support-matrix.md` marks Announcement (ANNC) as unimplemented (0/23).
Its `config/enums.js` has no announced-resource type mappings such as 10002 or 10003.
These are implementation observations, not statements about all Mobius versions.

The deployed instance was probed on 7 November 2026. Its CSEBase did not advertise
type 10003, and CREATE returned HTTP 405, oneM2M 4005, `not allowed API call`.
`cse/hostingCSE.js` returns that response from the default resource-type dispatch
branch. This matches the source support matrix. Announcement into this deployed
Mobius instance is therefore blocked; discovery/update checks cannot be performed.

## Why existing success is insufficient

MN registration creates a remoteCSE, and forwarding allows reads of data that remains
on MN. Neither creates an announced resource on IN. Ordinary container copying by a
Python bridge must be described as application replication, not standard announcement.

## Decision required before changing architecture

1. Keep Mobius as the main IN and record announcement as unsupported in this deployed
   version; confirm roadmap or a compatible build with maintainers.
2. Verify standard announcement in a separate ACME-to-ACME test environment. Clearly
   label that environment and do not claim announcement into Mobius.
3. If the team accepts the proposal's ACME-IN fallback, plan and test an explicit
   architecture change. Preserve existing Mobius and its data until migration is verified.

No changes to Mobius implementation, database schema, CSE targets or dashboard routing
are part of this audit. Do not enable announcement to this unsupported target on real
sensor resources simply to see whether it starts working.
