"""Deterministic, illustrative factory hazard model; not a fire engineering model."""
from __future__ import annotations

import heapq
import math
import random
from collections import deque

from .core import Geofence, UNITS, utcnow
from .layout import live_spec,LIVE_WIDTH,LIVE_HEIGHT,CELL_M


class Factory:
    width, height, cell_m = LIVE_WIDTH, LIVE_HEIGHT, CELL_M
    sensor_positions = {"node-01": (10, 8), "node-02": (21, 21), "node-03": (38, 14)}

    def __init__(self, seed=7, config=None):
        self.seed=seed;self.settings=dict(config or {});self.spec=live_spec(self.settings)
        self.width,self.height,self.cell_m=self.spec.width,self.spec.height,self.spec.cell_m
        self.zone_x=self.spec.zone_x;self.swarm_url=self.settings.get('swarm_url','').rstrip('/')
        self.geofence=Geofence(dict(width_m=self.width*self.cell_m,height_m=self.height*self.cell_m,
            boundary_x_m=self.zone_x*self.cell_m,hysteresis_m=self.cell_m,confirm_samples=2))
        self.sensor_positions={'node-01':(min(10,self.zone_x-2),min(8,self.height-2)),
            'node-02':(min(21,self.zone_x-2),min(21,self.height-2)),
            'node-03':(min(self.width-2,max(self.zone_x+2,int(self.width*.78))),min(14,self.height-2))}
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
        self.walls=set(self.spec.walls)
        self.agents=[];occupied=set()
        count=len(self.spec.drone_ids);half=(count+1)//2
        for i,name in enumerate(self.spec.drone_ids):
            left=i<half;lo,hi=(0,self.zone_x) if left else (self.zone_x,self.width)
            preferred=(lo+max(1,(hi-lo)//4)+(i%2)*max(1,(hi-lo)//3),int((i%half+1)*self.height/(half+1)))
            if i==0:preferred=(min(4,hi-2),min(8,self.height-2))
            candidates=[(x,y) for x in range(lo+1,hi-1) for y in range(1,self.height-1) if (x,y) not in self.walls|occupied]
            if not candidates:raise ValueError("Not enough walkable spawn cells in each zone")
            x,y=min(candidates,key=lambda p:(abs(p[0]-preferred[0])+abs(p[1]-preferred[1]),p))
            occupied.add((x,y))
            self.agents.append(dict(id=name,x=x,y=y,goal=[x,y],path=[],zone=self.spec.zone(x),status='ready',
                mode='swarm',replans=0,travel_m=0,_move=0.0,_fence=None,_planned=False,_manual=False))
        self._map_signature=None;self._swarm_elapsed=0;self._swarm_tick=0
        self.swarm_snapshot={};self.swarm_error='';self.explored=set();self.observation_counts={}
        self.pheromone=[0.0]*(self.width*self.height)
        self.events = deque(maxlen=80)
        self.history = deque(maxlen=180)
        self._sample_second = -1
        self.boundary_events = 0
        self.event("reset", "Scenario reset. Place a fire or run the demo.")

    def index(self, x, y):
        return y * self.width + x

    def inside(self, x, y):
        return type(x) is int and type(y) is int and 0 <= x < self.width and 0 <= y < self.height

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
        self.event("fire", f"Fire placed at ({x * self.cell_m}, {y * self.cell_m}) m", x=x, y=y)

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
            if action == "resume" and self.swarm_error:self._map_signature=None;self.swarm_error=""
            self.event(action, "Simulation paused" if self.paused else "Simulation resumed")
        elif action == 'resume_swarm':
            for a in self.agents:
                if not data.get('agent_id') or a['id']==data['agent_id']:a['_manual']=False
            self.event('swarm','Autonomous exploration resumed')
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
                agent["_manual"] = True
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
        crossings=[];self._swarm_elapsed+=dt
        if self._swarm_elapsed>=.75:
            self._swarm_elapsed-=.75;crossings=self.swarm_step()
        second = int(self.elapsed)
        if second != self._sample_second:
            self._sample_second = second
            self.history.append({"t": second, "temperature": round(max(self.heat), 1),
                                 "smoke": round(max(self.smoke) * 100, 1), "fires": len(self.fires)})
        return crossings

    def _sync_swarm(self):
        from dataclasses import replace
        from .swarm_service import Fusion
        from .core import digest
        from urllib.request import Request, urlopen
        import json, os, uuid
        spec=replace(self.spec,drone_ids=tuple(a['id'] for a in self.agents),walls=frozenset(self.walls))
        payload=spec.payload();signature=digest(payload)
        if signature!=self._map_signature:
            self._map_signature=signature;self._mission=uuid.uuid4().hex;self._swarm_tick=0
            self.fusion=Fusion();self.pheromone=[0.0]*(self.width*self.height);self.explored=set()
        result=None;observations={}
        for a in self.agents:
            cells=[];r=spec.radius
            for x in range(max(0,a['x']-r),min(self.width,a['x']+r+1)):
                for y in range(max(0,a['y']-r),min(self.height,a['y']+r+1)):
                    if (x,y) in self.walls:continue
                    k=self.index(x,y)
                    hazard=1.0 if self.unsafe(x,y) else min(.59,max(self.smoke[k],max(0,self.heat[k]-28.6)/70))
                    cells.append(dict(x=x,y=y,hazard=hazard))
            report=dict(mission_id=self._mission,drone_id=a['id'],sequence=self._swarm_tick,tick=self._swarm_tick,
                        map=payload,position=dict(x=a['x'],y=a['y']),cells=cells)
            if a.get('_manual'):report['goal']=dict(x=a['goal'][0],y=a['goal'][1])
            if self.swarm_url:
                headers={'Content-Type':'application/json'}
                if os.getenv('SWARM_API_TOKEN'):headers['Authorization']='Bearer '+os.environ['SWARM_API_TOKEN']
                with urlopen(Request(self.swarm_url+'/v1/reports',data=json.dumps(report).encode(),headers=headers),timeout=5) as response:result=json.load(response)
            else:result=self.fusion.report(report)
            observations[a['id']]=len(cells)
        if (result.get('width'),result.get('height'),result.get('mission_id'))!=(self.width,self.height,self._mission):raise ValueError('Fusion returned a different map/mission')
        actions=result.get('coordination',{}).get('actions',{})
        if set(actions)!=set(a['id'] for a in self.agents):raise ValueError('Fusion has not coordinated the complete live fleet')
        self.pheromone=[0.0]*(self.width*self.height)
        for cell in result['pheromone']:
            if not spec.inside(cell['x'],cell['y']):raise ValueError('Fusion cell outside live map')
            self.pheromone[self.index(cell['x'],cell['y'])]=cell['value']
        self.explored={(c['x'],c['y']) for c in result['pheromone']}
        self.swarm_snapshot=result;self.observation_counts=observations;self._swarm_tick+=1
        return actions

    def swarm_step(self):
        try:
            actions=self._sync_swarm()
            old={(a['x'],a['y']) for a in self.agents};destinations=[]
            for a in self.agents:
                x,y=actions[a['id']]['position']
                if not self.inside(x,y) or abs(x-a['x'])+abs(y-a['y'])>1 or (x,y) in self.walls or self.unsafe(x,y):raise ValueError('Fusion returned an unsafe move')
                if (x,y) in old and (x,y)!=(a['x'],a['y']):raise ValueError('Fusion move enters an occupied cell')
                destinations.append((x,y))
            if len(set(destinations))!=len(destinations):raise ValueError('Fusion reservations collide')
        except Exception as exc:
            self.swarm_error=f'{type(exc).__name__}: {exc}';self.paused=True
            self.event('swarm_error','Swarm paused: '+self.swarm_error)
            return []
        self.swarm_error='';crossings=[]
        for a,nxt in zip(self.agents,destinations):
            action=actions[a['id']];task=action.get('assignment');previous=(a['x'],a['y'])
            goal=list(task['goal']) if task else [a['x'],a['y']]
            if a['goal']!=goal:a['replans']+=1
            a['goal']=goal;a['path']=[tuple(p) for p in action['path']]
            a['mode']='manual' if a.get('_manual') else 'swarm'
            a['x'],a['y']=nxt;a['travel_m']+=self.cell_m if nxt!=previous else 0
            if a['path'] and nxt!=previous:a['path']=a['path'][1:]
            a['status']='arrived' if (a.get('_manual') and list(nxt)==goal) or (not task and self.swarm_snapshot['coordination']['unknown_cells']==0) else 'moving' if nxt!=previous else 'blocked'
            state,crossing=self.geofence.update(a['_fence'],(a['x']+.5)*self.cell_m,(a['y']+.5)*self.cell_m,utcnow())
            a['_fence'],a['zone']=state,state['zone']
            if crossing:
                self.boundary_events+=1;crossings.append(self.event('zone_crossing',f"{a['id']}: {crossing['from_zone']} → {crossing['to_zone']}",agent_id=a['id'],**crossing))
        return crossings

    def readings(self):
        result = []
        observed = utcnow()
        for device, (x, y) in self.sensor_positions.items():
            k = self.index(x, y)
            values = {"temperature": round(self.heat[k], 1),
                      "humidity": round(max(5, 55 - (self.heat[k] - 28.6) * 0.15), 1),
                      "pressure": 1013.2, "smoke_raw": round(180 + self.smoke[k] * 2900, 1),
                      "co_raw": round(90 + self.smoke[k] * 2500, 1),
                      "presence": any(abs(a["x"] - x) + abs(a["y"] - y) <= 3 for a in self.agents)}
            for sensor, value in values.items():
                result.append({"device_id": device, "zone_id": self.spec.zone(x),
                               "sensor_type": sensor, "value": value, "unit": UNITS[sensor],
                               "source": "simulation", "quality": "valid", "observed_at": observed})
        return result

    def snapshot(self):
        return {"zone_x":self.zone_x,"pheromone":self.pheromone,
                "swarm":dict(enabled=True,transport="http" if self.swarm_url else "embedded_fusion",url=self.swarm_url,
                    tick=self._swarm_tick,error=self.swarm_error,drone_count=len(self.agents),
                    shared_cells=len(self.explored),coverage_percent=round(100*len(self.explored)/max(1,self.width*self.height-len(self.walls)),2),
                    observations=self.observation_counts,coordination=self.swarm_snapshot.get("coordination",{})),
                "width": self.width, "height": self.height, "cell_m": self.cell_m,
                "sim_seconds": round(self.elapsed, 2), "paused": self.paused, "speed": self.speed,
                "heat": [round(v, 1) for v in self.heat], "smoke": [round(v, 3) for v in self.smoke],
                "walls": sorted(self.walls), "fires": [[x, y, v] for (x, y), v in self.fires.items()],
                "sensors": [{"id": name, "x": p[0], "y": p[1], "zone": self.spec.zone(p[0])}
                            for name, p in self.sensor_positions.items()],
                "observed_hazards": self.observed_hazards,
                "observed_hazard_cells": sorted(self.observed_cells),
                "agents": [{k: v for k, v in a.items() if not k.startswith("_")} for a in self.agents],
                "events": list(self.events), "history": list(self.history),
                "metrics": {"peak_temperature": round(max(self.heat), 1), "fire_cells": len(self.fires),
                            "hazard_cells": sum(t >= 70 or s >= 0.65 for t, s in zip(self.heat, self.smoke)),
                            "boundary_events": self.boundary_events,
                            "arrived": sum(a["status"] == "arrived" for a in self.agents)}}
