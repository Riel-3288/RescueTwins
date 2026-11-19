"""
RescueTwin 2D grid simulation
2 drones (zones A, B) + 1 real sensor node (node-01, replayed from JSON)
+ 1 simulated sensor node (node-02).
Compares a random-walk baseline with a pheromone swarm.

Run:  python simulation/rescue_sim.py            (benchmark + JSON files + animation)
      python simulation/rescue_sim.py --no-anim  (no animation window)
"""
import json
import os
import uuid
from urllib.request import Request, urlopen
import math
import random
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

# ---------------- SETTINGS ----------------
H, W = 30, 50            # grid rows, columns
ZONE_W = 25              # zone A = cols 0-24, B = 25-49
SENSE_R = 2              # drone sensing radius (cells)
HAZARD_BLOCK = 0.6       # hazard above this = drones will not enter
HAZARD_SIGMA = 1.6       # size of the hazard cloud around a node
EVAP = 0.003             # pheromone evaporation per step
MAX_STEPS = 3000
N_RUNS = 20              # runs for the benchmark
SEED = 1
STEP_SECONDS = 5         # one sim step = 5 s (same as READ_MS on the ESP32)
STEPS_PER_SAMPLE = 3     # sim steps per recorded node-01 sample
SAMPLE_FILE = "node01_sample.jsonl"
WRITE_JSON = True
SAVE_GIF = False

HERE = Path(__file__).resolve().parent
DIRS = [(-1, 0), (1, 0), (0, -1), (0, 1)]
RR, CC = np.mgrid[0:H, 0:W]

# virtual positions are (row, col)
NODES = [
    dict(id="node-01", pos=(10, 4),  zone="A", source="physical"),
    dict(id="node-02", pos=(12, 38),  zone="B", source="simulation", period=140, phase=0.0),
]
DRONE_START = {"A": (1, 1), "B": (28, 48)}


def zone_of(col):
    return "AB"[min(col // ZONE_W, 1)]


def build_walls():
    w = np.zeros((H, W), bool)
    w[:, 25] = True
    for r in (4, 5, 20, 21):
        w[r, 25] = False
    w[6, 2:8] = True
    w[18, 32:38] = True
    return w


# ---------------- node-01 recorded data ----------------
def load_series():
    vals = []
    p = HERE / SAMPLE_FILE
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if (d.get("sensor_type") == "smoke_raw" and d.get("quality") == "valid"
                    and isinstance(d.get("value"), (int, float))):
                vals.append(d["value"])
    if len(vals) < 5:
        print(f"[data] {SAMPLE_FILE} not found or empty -> using a built-in smoke profile")
        vals = [630] * 15 + [700, 800, 900, 966, 940, 880, 800, 720, 660] + [630] * 15
    else:
        print(f"[data] node-01 replay: {len(vals)} smoke_raw samples from {SAMPLE_FILE}")
    vmin, vmax = min(vals), max(vals)
    series = [(v - vmin) / (vmax - vmin) for v in vals]
    return vals, series, vmin, vmax


# ---------------- world ----------------
class World:
    def __init__(self, seed, mode, data, logging=False):
        self.mode = mode                       # "random" or "swarm"
        self.vals, self.series, self.vmin, self.vmax = data
        self.walls = build_walls()
        self.rng = random.Random(seed + 1000)  # agent decisions
        lay = random.Random(seed)              # layout (same for both modes)
        self.gauss = {}
        for n in NODES:
            r0, c0 = n["pos"]
            self.gauss[n["id"]] = np.exp(-((RR - r0) ** 2 + (CC - c0) ** 2) / (2 * HAZARD_SIGMA ** 2))
        self.victims = []
        for z in range(2):
            cands = [(r, c) for r in range(H) for c in range(z * ZONE_W, (z + 1) * ZONE_W)
                     if not self.walls[r, c]
                     and all(abs(r - n["pos"][0]) + abs(c - n["pos"][1]) > 4 for n in NODES)
                     and all(abs(r - s[0]) + abs(c - s[1]) > 4 for s in DRONE_START.values())]
            self.victims.append(lay.choice(cands))
        self.found = set()
        self.seen = np.zeros((H, W), bool)
        self.phero = np.zeros((H, W))
        self.agents = [dict(id=f"drone-{z}", pos=DRONE_START[z], zone=z) for z in "AB"]
        self.mec_url = os.environ.get("SWARM_MEC_URL", "").rstrip("/") if mode == "swarm" else ""
        self.mission_id = uuid.uuid4().hex
        self.t = 0
        self.t_cover = None
        self.t_victims = None
        self.handovers = []
        self.logging = logging
        self.log_sensor, self.log_agent = [], []
        self.t0 = datetime.now(timezone.utc).replace(microsecond=0)
        self.update_hazard()
        for a in self.agents:
            self.sense(a)
        if self.mec_url:
            self.sync_mec()
        self.check_done()
        self.record_step()

    # --- hazard from the sensor nodes ---
    def level(self, n):
        if n["source"] == "physical":
            return self.series[(self.t // STEPS_PER_SAMPLE) % len(self.series)]
        x = 0.5 + 0.5 * math.sin(2 * math.pi * self.t / n["period"] + n["phase"])
        return max(0.0, (x - 0.4) / 0.6)

    def raw_mv(self, n):
        if n["source"] == "physical":
            return self.vals[(self.t // STEPS_PER_SAMPLE) % len(self.vals)]
        return int(round(self.vmin + self.level(n) * (self.vmax - self.vmin)))

    def update_hazard(self):
        h = np.zeros((H, W))
        for n in NODES:
            h += self.level(n) * self.gauss[n["id"]]
        self.haz = np.clip(h, 0, 1)
        self.blocked = (self.haz > HAZARD_BLOCK) & ~self.walls

    def sync_mec(self):
        """Send each drone's local observations; use only the returned merged map.

        Network/API errors stop this run. Never silently substitute a local map.
        Grid hazards are normalized demonstration values, not calibrated mV.
        """
        result = None
        for agent in self.agents:
            row, col = agent['pos']
            cells = [dict(x=c, y=r, hazard=float(self.haz[r,c]))
                     for r in range(max(0,row-SENSE_R),min(H,row+SENSE_R+1))
                     for c in range(max(0,col-SENSE_R),min(W,col+SENSE_R+1))
                     if not self.walls[r,c]]
            payload = dict(mission_id=self.mission_id, drone_id=agent['id'],
                           sequence=self.t, tick=self.t, cells=cells)
            headers = {'Content-Type':'application/json'}
            token = os.environ.get('SWARM_API_TOKEN')
            if token: headers['Authorization'] = 'Bearer '+token
            request = Request(self.mec_url+'/v1/reports', data=json.dumps(payload).encode(), headers=headers)
            with urlopen(request, timeout=10) as response:
                result = json.load(response)
        if (result['width'],result['height']) != (W,H) or result['mission_id'] != self.mission_id:
            raise ValueError('MEC returned a different map/mission')
        self.mec_snapshot = result
        self.phero.fill(0)
        self.seen.fill(False)
        self.haz.fill(0)
        for cell in result['pheromone']:
            self.phero[cell['y'],cell['x']] = cell['value']
            self.seen[cell['y'],cell['x']] = True
        for cell in result['hazards']:
            self.haz[cell['y'],cell['x']] = cell['value']
        self.blocked = (self.haz > HAZARD_BLOCK) & ~self.walls

    # --- sensing ---
    def sense(self, a):
        r, c = a["pos"]
        r0, r1 = max(0, r - SENSE_R), min(H, r + SENSE_R + 1)
        c0, c1 = max(0, c - SENSE_R), min(W, c + SENSE_R + 1)
        self.seen[r0:r1, c0:c1] = True
        self.phero[r0:r1, c0:c1] = 1.0
        for v in self.victims:
            if v not in self.found and r0 <= v[0] < r1 and c0 <= v[1] < c1:
                self.found.add(v)
                if self.logging:
                    self.log_agent.append(dict(kind="victim_found", observed_at=self.ts(), step=self.t,
                                               device_id=a["id"], zone_id=zone_of(v[1]), x=v[1], y=v[0]))

    def coverage(self):
        return float(self.seen[~self.walls].mean())

    def check_done(self):
        if self.t_cover is None and self.coverage() >= 1.0:
            self.t_cover = self.t
        if self.t_victims is None and len(self.found) == len(self.victims):
            self.t_victims = self.t

    @property
    def finished(self):
        return self.t_cover is not None

    # --- movement ---
    def useful_cells(self):
        """Cells from which at least one uncovered cell can be sensed."""
        unc = (~self.walls) & ((~self.seen) | (self.phero < 0.2))
        p = np.pad(unc.astype(int), SENSE_R)
        cnt = np.zeros((H, W), int)
        for dr in range(2 * SENSE_R + 1):
            for dc in range(2 * SENSE_R + 1):
                cnt += p[dr:dr + H, dc:dc + W]
        return cnt > 0

    def bfs(self, targets, passable):
        dist = np.full((H, W), -1, int)
        dq = deque()
        for r, c in targets:
            dist[r, c] = 0
            dq.append((r, c))
        while dq:
            r, c = dq.popleft()
            for dr, dc in DIRS:
                nr, nc = r + dr, c + dc
                if 0 <= nr < H and 0 <= nc < W and passable[nr, nc] and dist[nr, nc] < 0:
                    dist[nr, nc] = dist[r, c] + 1
                    dq.append((nr, nc))
        return dist

    def move_agents(self):
        passable = ~self.walls & ~self.blocked
        useful = self.useful_cells() if self.mode == "swarm" else None
        for a in self.agents:
            others = [o["pos"] for o in self.agents if o is not a]
            occupied = set(others)
            r, c = a["pos"]
            new = (r, c)
            if self.mode == "random":
                opts = [(r + dr, c + dc) for dr, dc in DIRS
                        if 0 <= r + dr < H and 0 <= c + dc < W
                        and passable[r + dr, c + dc] and (r + dr, c + dc) not in occupied]
                if opts:
                    new = self.rng.choice(opts)
            else:
                far = np.ones((H, W), bool)
                for orow, ocol in others:
                    far &= (np.abs(RR - orow) + np.abs(CC - ocol)) > 4
                tmask = useful & passable & far
                if not tmask.any():
                    tmask = useful & passable
                dist = self.bfs(np.argwhere(tmask), passable)
                best = None
                for dr, dc in DIRS + [(0, 0)]:
                    nr, nc = r + dr, c + dc
                    if not (0 <= nr < H and 0 <= nc < W) or not passable[nr, nc] or (nr, nc) in occupied:
                        continue
                    d = dist[nr, nc]
                    d = 999 if d < 0 else d
                    repel = sum(1.5 * math.exp(-(abs(nr - orow) + abs(nc - ocol)) / 3.0)
                                for orow, ocol in others)
                    score = d + 3.0 * self.haz[nr, nc] + repel \
                        + (0.5 if (dr, dc) == (0, 0) else 0.0) + self.rng.random() * 0.1
                    if best is None or score < best[0]:
                        best = (score, (nr, nc))
                if best:
                    new = best[1]
            a["pos"] = new
            z = zone_of(new[1])
            if z != a["zone"]:
                self.handovers.append(dict(step=self.t, drone=a["id"], src=a["zone"], dst=z))
                if self.logging:
                    self.log_agent.append(dict(kind="handover", observed_at=self.ts(), step=self.t,
                                               device_id=a["id"], from_zone=a["zone"], to_zone=z,
                                               note="re-register with the MN-CSE of the new zone"))
                a["zone"] = z

    def step(self):
        self.t += 1
        self.update_hazard()
        if self.mec_url:
            self.sync_mec()
        self.move_agents()
        for a in self.agents:
            self.sense(a)
        self.phero *= (1 - EVAP)
        self.check_done()
        self.record_step()

    # --- JSON logging ---
    def ts(self):
        return (self.t0 + timedelta(seconds=self.t * STEP_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def record_step(self):
        if not self.logging:
            return
        if self.t == 0:
            for n in NODES:
                self.log_agent.append(dict(kind="node", device_id=n["id"], zone_id=n["zone"],
                                           source=n["source"], x=n["pos"][1], y=n["pos"][0]))
        for n in NODES:
            self.log_sensor.append(dict(device_id=n["id"], zone_id=n["zone"], sensor_type="smoke_raw",
                                        value=self.raw_mv(n), unit="mV", source=n["source"],
                                        quality="valid", observed_at=self.ts()))
        for a in self.agents:
            self.log_agent.append(dict(kind="position", observed_at=self.ts(), step=self.t,
                                       device_id=a["id"], zone_id=zone_of(a["pos"][1]),
                                       x=a["pos"][1], y=a["pos"][0], source="simulation"))


# ---------------- runs ----------------
def run(seed, mode, data, logging=False):
    w = World(seed, mode, data, logging)
    while not w.finished and w.t < MAX_STEPS:
        w.step()
    return w


def benchmark(data):
    res = {"random": [], "swarm": []}
    for mode in res:
        for s in range(SEED, SEED + N_RUNS):
            res[mode].append(run(s, mode, data))
    print(f"\nBenchmark: {N_RUNS} runs, 2 drones, {SENSE_R}-cell sensing, cap {MAX_STEPS} steps")
    print(f"{'':16}{'steps to 100% map':>20}{'steps to find 3 victims':>26}{'zone handovers':>16}")
    for mode, label in (("random", "Random walk"), ("swarm", "Pheromone swarm")):
        ws = res[mode]
        cov = [w.t_cover if w.t_cover is not None else MAX_STEPS for w in ws]
        vic = [w.t_victims if w.t_victims is not None else MAX_STEPS for w in ws]
        ho = [len(w.handovers) for w in ws]
        unfinished = sum(w.t_cover is None for w in ws)
        note = f"  ({unfinished} hit the cap)" if unfinished else ""
        print(f"{label:16}{np.mean(cov):>14.0f} +/-{np.std(cov):>4.0f}"
              f"{np.mean(vic):>18.0f} +/-{np.std(vic):>4.0f}{np.mean(ho):>16.1f}{note}")
    m_r = np.mean([w.t_cover or MAX_STEPS for w in res["random"]])
    m_s = np.mean([w.t_cover or MAX_STEPS for w in res["swarm"]])
    print(f"Swarm covers the map about {m_r / m_s:.1f}x faster than the random walk.\n")


def export_json(data):
    w = run(SEED, "swarm", data, logging=True)
    sp, ap = HERE / "sim_sensor_data.jsonl", HERE / "sim_agent_log.jsonl"
    with open(sp, "w", encoding="utf-8") as f:
        for d in w.log_sensor:
            f.write(json.dumps(d) + "\n")
    with open(ap, "w", encoding="utf-8") as f:
        for d in w.log_agent:
            f.write(json.dumps(d) + "\n")
    print(f"[json] wrote {sp.name} ({len(w.log_sensor)} lines) and {ap.name} ({len(w.log_agent)} lines)")
    print(f"[json] zone handovers in this run: {len(w.handovers)}")
    for h in w.handovers:
        print(f"       step {h['step']}: {h['drone']} {h['src']} -> {h['dst']}")


# ---------------- animation ----------------
def draw(ax, w, title):
    img = np.empty((H, W, 3))
    img[:] = (0.93, 0.93, 0.93)
    p = np.clip(w.phero, 0, 1)
    img[w.seen] = np.stack([0.80 - 0.25 * p, 0.93 - 0.08 * p, 0.80 - 0.25 * p], axis=-1)[w.seen]
    a = (0.75 * w.haz)[..., None]
    img = (1 - a) * img + a * np.array([0.85, 0.10, 0.10])
    img[w.walls] = (0.20, 0.20, 0.20)
    ax.clear()
    ax.imshow(img, origin="upper", interpolation="nearest")
    for x in (24.5,):
        ax.axvline(x, color="black", lw=0.8, ls="--")
    for i, z in enumerate("AB"):
        ax.text(i * ZONE_W + 4.5, -0.9, f"Zone {z}", ha="center", fontsize=9)
    for n in NODES:
        r, c = n["pos"]
        if n["source"] == "physical":
            ax.plot(c, r, "*", color="gold", mec="black", ms=14)
        else:
            ax.plot(c, r, "^", color="orange", mec="black", ms=9)
    for v in w.found:
        ax.plot(v[1], v[0], "x", color="red", ms=10, mew=3)
    for ag in w.agents:
        r, c = ag["pos"]
        ax.plot(c, r, "o", color="royalblue", mec="white", ms=11)
        ax.text(c, r, ag["id"][-1], color="white", ha="center", va="center", fontsize=7, weight="bold")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"{title}\nstep {w.t}   coverage {w.coverage():.0%}   victims {len(w.found)}/{len(w.victims)}",
                 fontsize=10, pad=18)


def animate(data):
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation
    wr, ws = World(SEED, "random", data), World(SEED, "swarm", data)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.2))
    fig.suptitle("RescueTwin  |  star = node-01 (physical)   triangle = simulated node   "
                 "red = hazard   X = victim found", fontsize=10)

    def frames():
        while not ws.finished and ws.t < MAX_STEPS:
            yield ws.t

    def update(_):
        wr.step()
        ws.step()
        draw(ax1, wr, "Random walk (baseline)")
        draw(ax2, ws, "Pheromone swarm")

    draw(ax1, wr, "Random walk (baseline)")
    draw(ax2, ws, "Pheromone swarm")
    anim = FuncAnimation(fig, update, frames=frames, interval=150, repeat=False,
                         save_count=MAX_STEPS, cache_frame_data=False)
    if SAVE_GIF:
        anim.save(str(HERE / "rescuetwin_sim.gif"), writer="pillow", fps=15)
        print("[gif] saved rescuetwin_sim.gif")
    plt.show()


def main():
    data = load_series()
    if "--quick" not in sys.argv:
        benchmark(data)
    if WRITE_JSON:
        export_json(data)
    if "--no-anim" not in sys.argv:
        animate(data)


if __name__ == "__main__":
    main()