# Platform core: agents, role policies, group commands and notifications

This is a reproducible **platform mechanism test**, not a demonstration of physical
robot behaviour, a running swarm algorithm, real zone handover or MEC API integration.
It does not modify node-01, the existing sensor policies or the dashboard identity.

## Run

Start the existing ACME Zone A MN-CSE first, then from the repository root:

```powershell
.\.venv\Scripts\python.exe .\script\platform_core_smoke_test.py
```

The script uses requests, already installed in the project environment. Its default
report is `docs/platform-core-test-report.json`. The report reflects the actual run,
including failures. Keep it with the logbook as evidence; do not label an unrun test completed.

## Isolated resources

```text
mn-zone-a
├─ platform-test-edge (CPlatformTestEdge)
│  ├─ map-policy, group-policy, per-agent policies
│  ├─ map (CNT → CIN)
│  └─ team (GRP, members are both commands containers)
├─ platform-test-agent-01 (CPlatformTestAgent01)
│  ├─ observations
│  ├─ bids
│  └─ commands
├─ platform-test-agent-02 (CPlatformTestAgent02)
│  ├─ observations
│  ├─ bids
│  └─ commands
└─ platform-test-viewer (CPlatformTestViewer)
```

The existing test resources are reused only if they carry the expected test label.
Unexpected policies or members cause the script to stop without replacing them.
Test records are retained with mni=20 per container. All records identify the run.

## Verified operations when the report passes

- Each agent uses its own AE identity to publish observations and bids.
- The edge role publishes shared map records; agents and the viewer read them.
- An agent cannot write the map, another agent's observations or its own commands.
- The viewer cannot write or reconfigure the map, or issue group commands.
- An unknown identity cannot read the map.
- A single CREATE through the team's `/fopt` writes a CIN to both command containers.
- Each member response must be 2001; the adapters must read back the exact command.
- Subscription callbacks must deliver both matching notifications for recall and regroup.

Subscriptions use loopback callbacks on a free local port; they are created by the
administrator for the test and removed on exit. They also expire after 15 minutes if
the process crashes. Do not interpret this as a deployed persistent callback service.

These policies restrict operations at container scope. They do not implement device
authentication, TLS, broker topic ACLs, or attribute-level command validation.

## Integrate the real simulator later

`script/agent_http_adapter.py` supplies AgentHTTPAdapter with:

```python
from agent_http_adapter import AgentHTTPAdapter

agent = AgentHTTPAdapter(
    base_url='http://127.0.0.1:8001/~/id-mn-zone-a/mn-zone-a',
    ae_name='platform-test-agent-01',
    originator='CPlatformTestAgent01',
    map_path='/platform-test-edge/map',
)
agent.post_observation({'agent_id': 'test-agent-01', 'cell': [1, 2], 'source': 'simulation'})
agent.post_bid({'task_id': 'example', 'cost': 3})
command = agent.latest_command()
map_snapshot = agent.latest_map()
agent.close()
```

Provisioning is handled by the platform test script, not by each adapter call.
The simulator must supply the real schema and map logic, execute commands, record
acknowledgements and deduplicate command_id. The minimal adapter polls; subscriptions
are demonstrated separately by the test callback. Real production identities and
resource paths should be agreed before replacing any simulator integration.

## Remaining work

Actual simulation integration; production role provisioning; persistent notifications;
resource announcement; Zone B/handover; MEC APIs; ESP32 MQTT LAN integration; map UI;
latency experiments and reproducible deployment documentation.
