"""Deterministic, illustrative factory hazard model; not a fire engineering model."""
from __future__ import annotations

import heapq
import math
import random
from collections import deque

from .core import Geofence, UNITS, utcnow


class Factory:
    width, height, cell_m = 50, 30, 2
    sensor_positions = {"node-01": (10, 8), "node-02": (21, 21), "node-03": (38, 14)}

    def __init__(self, seed=7):
        self.seed = seed
        self.geofence = Geofence({"width_m": 100, "height_m": 60, "boundary_x_m": 50,
                                  "hysteresis_m": 2, "confirm_samples": 2})
        self.reset()

    def reset(self):
        self.rng = random.Random(self.seed)
        self.elapsed = 0.0
        self.paused = True
        self.speed = 1
        self.heat = [28.6] * (self.width * self.height)
        self.smoke = [0.0] * len(self.heat)
        self.fires = {}
        self.observed_hazards = []
        self.observed_cells = set()
        self.walls = {(15, y) for y in range(3, 27) if y not in (8, 9, 10, 20, 21, 22)}
        self.walls |= {(34, y) for y in range(3, 27) if y not in (12, 13, 14, 15, 24, 25)}
        self.walls |= {(x, y) for x in range(21, 29) for y in (5, 6, 24, 25)}
        self.agents = []
        for i, (x, y) in enumerate(((4, 8), (7, 21), (21, 14), (29, 21)), 1):
            self.agents.append({"id": f"agent-{i:02}", "x": x, "y": y,
                                "goal": [48, 8 if i % 2 else 22], "path": [],
                                "zone": "A" if x < 25 else "B", "status": "ready",
                                "replans": 0, "travel_m": 0, "_move": 0.0, "_fence": None,
                                "_planned": False})
        self.events = deque(maxlen=80)
        self.history = deque(maxlen=180)
        self._sample_second = -1
        self.boundary_events = 0
        self.event("reset", "Scenario reset. Place a fire or run the demo.")

    def index(self, x, y):
        return y * self.width + x

    def inside(self, x, y):
        return 0 <= x < self.width and 0 <= y < self.height

    def neighbors(self, x, y):
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if self.inside(nx, ny) and (nx, ny) not in self.walls:
                yield nx, ny

    def event(self, kind, message, **extra):
        value = {"kind": kind, "message": message, "sim_seconds": round(self.elapsed, 2),
                 "observed_at": utcnow(), "source": "simulation", **extra}
        self.events.appendleft(value)
        return value

    def ignite(self, x, y, intensity=1.0):
        if not self.inside(x, y) or (x, y) in self.walls:
            raise ValueError("Select a walkable cell inside the factory")
        intensity = float(intensity)
        if not math.isfinite(intensity) or not 0.25 <= intensity <= 2:
            raise ValueError("Fire intensity must be between 0.25 and 2")
        self.fires[x, y] = intensity
        self.heat[self.index(x, y)] = max(90, self.heat[self.index(x, y)])
        self.event("fire", f"Fire placed at ({x * 2}, {y * 2}) m", x=x, y=y)

    def unsafe(self, x, y):
        k = self.index(x, y)
        return self.heat[k] >= 70 or self.smoke[k] >= 0.65 or (x, y) in self.observed_cells

    def set_observed_hazards(self, hazards):
        self.observed_hazards = hazards
        self.observed_cells = {(x, y) for h in hazards
                               for x in range(max(0,h['x']-h['radius']), min(self.width,h['x']+h['radius']+1))
                               for y in range(max(0,h['y']-h['radius']), min(self.height,h['y']+h['radius']+1))
                               if abs(x-h['x'])+abs(y-h['y']) <= h['radius']}

    def route(self, start, goal):
        if goal in self.walls or self.unsafe(*goal):
            return []
        frontier = [(0, start)]
        costs, previous = {start: 0}, {}
        while frontier:
            _, current = heapq.heappop(frontier)
            if current == goal:
                path = [goal]
                while path[-1] != start:
                    path.append(previous[path[-1]])
                return list(reversed(path))
            for cell in self.neighbors(*current):
                if self.unsafe(*cell):
                    continue
                k = self.index(*cell)
                new_cost = costs[current] + 1 + self.smoke[k] * 12 + max(0, self.heat[k] - 30) / 12
                if new_cost < costs.get(cell, float("inf")):
                    costs[cell], previous[cell] = new_cost, current
                    h = abs(cell[0] - goal[0]) + abs(cell[1] - goal[1])
                    heapq.heappush(frontier, (new_cost + h, cell))
        return []

    def command(self, data):
        action = data.get("action")
        if action == "reset":
            self.reset()
        elif action in ("pause", "resume"):
            self.paused = action == "pause"
            self.event(action, "Simulation paused" if self.paused else "Simulation resumed")
        elif action == "speed":
            if data.get("value") not in (1, 2, 4):
                raise ValueError("Speed must be 1, 2 or 4")
            self.speed = data["value"]
        elif action in ('demo', 'rescue_demo'):
            self.reset()
            self.ignite(10 if action == 'rescue_demo' else 13, 8 if action == 'rescue_demo' else 9, 1)
            self.paused = False
        elif action in ("ignite", "obstacle", "goal"):
            x, y = data.get("x"), data.get("y")
            if type(x) is not int or type(y) is not int or not self.inside(x, y):
                raise ValueError("Coordinates must be integers inside the grid")
            if action == "ignite":
                self.ignite(x, y, data.get("intensity", 1))
            elif action == "obstacle":
                occupied = set(self.sensor_positions.values()) | {(a["x"], a["y"]) for a in self.agents}
                occupied |= {tuple(a["goal"]) for a in self.agents}
                if (x, y) in occupied or (x, y) in self.fires:
                    raise ValueError("Cell occupied by a sensor, agent, goal or fire")
                if (x, y) in self.walls:
                    self.walls.remove((x, y))
                else:
                    self.walls.add((x, y))
                    self.heat[self.index(x, y)], self.smoke[self.index(x, y)] = 28.6, 0
                self.event("obstacle", "Obstacle layout updated")
            else:
                if (x, y) in self.walls or self.unsafe(x, y):
                    raise ValueError("Goal is not walkable")
                agent = next((a for a in self.agents if a["id"] == data.get("agent_id")), None)
                if agent is None:
                    raise ValueError("Agent not found")
                agent["goal"] = [x, y]
                agent["path"] = []
                agent["status"] = "ready"
                agent["_planned"] = False
                self.event("goal", f"Goal updated for {agent['id']}", agent_id=agent["id"])
        elif action == "extinguish":
            self.fires.clear()
            self.event("extinguish", "Fires cleared. Heat and smoke will dissipate.")
        else:
            raise ValueError("Unknown action")

    def step(self, dt=0.25):
        if self.paused:
            return []
        if not 0 < dt <= 0.25:
            raise ValueError("Model timestep must be between 0 and 0.25 seconds")
        self.elapsed += dt
        heat, smoke = self.heat[:], self.smoke[:]
        for y in range(self.height):
            for x in range(self.width):
                if (x, y) in self.walls:
                    continue
                k = self.index(x, y)
                adjacent = [self.index(*p) for p in self.neighbors(x, y)]
                heat[k] += dt * (0.45 * sum(self.heat[j] - self.heat[k] for j in adjacent)
                                 - 0.025 * (self.heat[k] - 28.6))
                smoke[k] += dt * (0.65 * sum(self.smoke[j] - self.smoke[k] for j in adjacent)
                                  - 0.018 * self.smoke[k])
                strength = self.fires.get((x, y), 0)
                heat[k] = min(450, max(28.6, heat[k] + strength * 65 * dt))
                smoke[k] = min(1, max(0, smoke[k] + strength * 0.65 * dt))
        # Scriptable stochastic spread with a fixed seed, not combustion physics.
        new_fires = {}
        for cell, strength in list(self.fires.items()):
            for p in self.neighbors(*cell):
                if p not in self.fires and heat[self.index(*p)] > 55 and self.rng.random() < 0.035 * dt:
                    new_fires[p] = strength * 0.85
        self.fires.update(new_fires)
        self.heat, self.smoke = heat, smoke
        crossings = []
        for agent in self.agents:
            agent["_move"] += dt
            if agent["_move"] < 0.75:
                continue
            agent["_move"] -= 0.75
            start, goal = (agent["x"], agent["y"]), tuple(agent["goal"])
            path = self.route(start, goal)
            old_path = [tuple(p) for p in agent["path"]]
            if agent["_planned"] and path and old_path and path != old_path:
                agent["replans"] += 1
            agent["_planned"] = True
            agent["path"] = path
            if start == goal:
                agent["status"] = "arrived"
            elif len(path) > 1:
                agent["x"], agent["y"] = path[1]
                agent["travel_m"] += self.cell_m
                agent["path"] = path[1:]
                agent["status"] = "moving"
            else:
                agent["status"] = "blocked"
            state, crossing = self.geofence.update(agent["_fence"], (agent["x"] + 0.5) * 2,
                                                     (agent["y"] + 0.5) * 2, utcnow())
            agent["_fence"], agent["zone"] = state, state["zone"]
            if crossing:
                self.boundary_events += 1
                crossings.append(self.event("zone_crossing", f"{agent['id']}: {crossing['from_zone']} → {crossing['to_zone']}",
                                            agent_id=agent["id"], **crossing))
        second = int(self.elapsed)
        if second != self._sample_second:
            self._sample_second = second
            self.history.append({"t": second, "temperature": round(max(self.heat), 1),
                                 "smoke": round(max(self.smoke) * 100, 1), "fires": len(self.fires)})
        return crossings

    def readings(self):
        result = []
        observed = utcnow()
        for device, (x, y) in self.sensor_positions.items():
            k = self.index(x, y)
            values = {"temperature": round(self.heat[k], 1),
                      "humidity": round(max(5, 55 - (self.heat[k] - 28.6) * 0.15), 1),
                      "pressure": 1013.2, "smoke_raw": min(4095, int(180 + self.smoke[k] * 3700)),
                      "co_raw": min(4095, int(90 + self.smoke[k] * 1900)),
                      "presence": any(abs(a["x"] - x) + abs(a["y"] - y) <= 3 for a in self.agents)}
            for sensor, value in values.items():
                result.append({"device_id": device, "zone_id": "A" if x < 25 else "B",
                               "sensor_type": sensor, "value": value, "unit": UNITS[sensor],
                               "source": "simulation", "quality": "valid", "observed_at": observed})
        return result

    def snapshot(self):
        return {"width": self.width, "height": self.height, "cell_m": self.cell_m,
                "sim_seconds": round(self.elapsed, 2), "paused": self.paused, "speed": self.speed,
                "heat": [round(v, 1) for v in self.heat], "smoke": [round(v, 3) for v in self.smoke],
                "walls": sorted(self.walls), "fires": [[x, y, v] for (x, y), v in self.fires.items()],
                "sensors": [{"id": name, "x": p[0], "y": p[1], "zone": "A" if p[0] < 25 else "B"}
                            for name, p in self.sensor_positions.items()],
                "observed_hazards": self.observed_hazards,
                "observed_hazard_cells": sorted(self.observed_cells),
                "agents": [{k: v for k, v in a.items() if not k.startswith("_")} for a in self.agents],
                "events": list(self.events), "history": list(self.history),
                "metrics": {"peak_temperature": round(max(self.heat), 1), "fire_cells": len(self.fires),
                            "hazard_cells": sum(t >= 70 or s >= 0.65 for t, s in zip(self.heat, self.smoke)),
                            "boundary_events": self.boundary_events,
                            "arrived": sum(a["status"] == "arrived" for a in self.agents)}}
