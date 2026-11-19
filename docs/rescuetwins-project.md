# RescueTwins: A digital twin for factory search and rescue

During a factory emergency, responders need to know which areas have been searched and where hazards may block access. RescueTwins is a prototype that brings sensor data, drone exploration, and a map of the factory into one dashboard.

## How it works

Each simulated drone reports the cells it has observed. An edge application combines those reports into a shared exploration and hazard map, then assigns routes so the drones can search different areas. The dashboard shows coverage, drone positions and routes, and temperature and smoke layers. A simulated victim sighting can generate a robot inspection task suggestion.

The project uses oneM2M to store and retrieve sensor readings. A separate MEC-IPE component can also store network location and radio network information from ETSI MEC as oneM2M records. We verified both writes and readbacks against a local ACME CSE using sample MEC responses.

## What the simulation showed

In three runs on the **50 × 30** map, two drones covered all **1,462 walkable cells** in **199 steps** and found both simulated victims. At five simulated seconds per step, that is **16 minutes 35 seconds in the model**. The separate LiveSwarm demo shows eight drones exploring an **80 × 40** map in real time.

## Try the demo

Use the [run guide](../mec-app/deploy/README_RUN.md) to start **LiveSwarm** or replay a recorded mission. The current demo uses simulated or recorded inputs. Live ESP32 integration, deployment to the team's MEC host, and application handover between MEC hosts still need end-to-end verification.
