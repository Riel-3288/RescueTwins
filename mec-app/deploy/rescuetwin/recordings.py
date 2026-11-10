"""Recorded-file integration and drone route previews; never commands real equipment."""
from __future__ import annotations

import copy
import heapq
import json
import math
from collections import Counter
from pathlib import Path

from .core import FIELDS, UNITS, canonical, digest, utc_timestamp, validate_reading

MAX_BYTES = 4 * 1024 * 1024


def parse_records(raw):
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('File must contain 1 byte to 4 MiB')
    from .__main__ import strict_json
    text = raw.decode('utf-8-sig')
    try:
        value = strict_json(text)
    except json.JSONDecodeError:
        value = [strict_json(line) for line in text.splitlines() if line.strip()]
    if isinstance(value, dict) and set(value) == {'m2m:cin'}:
        value = value['m2m:cin']['con']
        if isinstance(value, str):
            value = strict_json(value)
    rows = value if isinstance(value, list) else [value]
    if not rows or len(rows) > 20000 or any(not isinstance(r, dict) for r in rows):
        raise ValueError('Expected a JSON object, object array, JSONL, or m2m:cin.con; maximum 20000 records')
    return rows


def sensor_report(rows, config):
    accepted, rejected = [], []
    for index, row in enumerate(rows):
        try:
            validate_reading(row, config['devices'])
            accepted.append(index)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            rejected.append({'index': index, 'reason': str(exc)})
    return {'rows': len(rows), 'compatible': len(accepted), 'accepted_indices': accepted,
            'rejected': rejected, 'zones': dict(Counter(str(r.get('zone_id', '?')) for r in rows)),
            'units': dict(Counter(str(r.get('unit', '?')) for r in rows)),
            'input_digest': digest(rows), 'cse_written': False,
            'note': 'Original source, values, units and timestamps retained. Compatibility is not write permission.'}


def sariel_grid():
    # Matches simulation/rescue_sim.py build_walls; all positions remain GRID CELLS.
    walls = {(10, y) for y in range(20)} | {(20, y) for y in range(20)}
    walls -= {(10, y) for y in (4, 5, 14, 15)} | {(20, y) for y in (8, 9, 10)}
    walls |= {(x, 6) for x in range(2, 8)} | {(x, 13) for x in range(12, 18)}
    walls |= {(x, 7) for x in range(22, 28)} | {(24, y) for y in range(13, 16)}
    return {'width': 30, 'height': 20, 'zone_width': 10,
            'walls': sorted(map(list, walls)), 'coordinates': 'grid_cells',
            'layout': 'simulation/rescue_sim.py (30 columns x 20 rows)'}


def route(grid, start, goal, blocked=()):
    start, goal = tuple(start), tuple(goal)
    obstacles = {tuple(p) for p in grid['walls']} | set(blocked)
    def inside(p):
        return 0 <= p[0] < grid['width'] and 0 <= p[1] < grid['height']
    if not inside(start) or not inside(goal) or start in obstacles or goal in obstacles:
        return []
    frontier, costs, previous = [(0, start)], {start: 0}, {}
    while frontier:
        _, cell = heapq.heappop(frontier)
        if cell == goal:
            path = [cell]
            while path[-1] != start:
                path.append(previous[path[-1]])
            return list(reversed(path))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nxt = cell[0] + dx, cell[1] + dy
            score = costs[cell] + 1
            if inside(nxt) and nxt not in obstacles and score < costs.get(nxt, math.inf):
                costs[nxt], previous[nxt] = score, cell
                heapq.heappush(frontier, (score + abs(nxt[0]-goal[0]) + abs(nxt[1]-goal[1]), nxt))
    return []


class RecordedMission:
    def __init__(self, config):
        self.config = config
        self.grid = sariel_grid()
        self.sensors, self.agent_log = [], []
        self.step = 0
        self.plans = {}
        self.report = sensor_report([], config)

    def upload(self, kind, raw):
        rows = parse_records(raw)
        if kind == 'sensors':
            # Malformed contract rows remain available for inspection, never CSE writes.
            report = sensor_report(rows, self.config)
            self.sensors, self.report = copy.deepcopy(rows), report
        elif kind == 'agents':
            for row in rows:
                if row.get('kind') not in ('node', 'position', 'victim_found', 'handover'):
                    raise ValueError('Unknown agent log kind')
                if not isinstance(row.get('device_id'), str) or not row['device_id']:
                    raise ValueError('Agent log requires device_id')
                if row['kind'] != 'node':
                    utc_timestamp(row.get('observed_at'))
                    if type(row.get('step')) is not int or row['step'] < 0:
                        raise ValueError('Agent log requires a nonnegative integer step')
                if row['kind'] == 'handover':
                    if any(row.get(k) not in ('A', 'B', 'C') for k in ('from_zone', 'to_zone')):
                        raise ValueError('Invalid handover zone')
                else:
                    if any(type(row.get(k)) is not int for k in ('x', 'y')):
                        raise ValueError('Grid coordinates must be integers')
                    if not (0 <= row['x'] < self.grid['width'] and 0 <= row['y'] < self.grid['height']):
                        raise ValueError('Coordinates outside the Sariel grid')
                    if row.get('zone_id') != 'ABC'[row['x']//self.grid['zone_width']]:
                        raise ValueError('zone_id differs from the grid position')
                    if row['kind'] in ('node', 'position') and [row['x'],row['y']] in self.grid['walls']:
                        raise ValueError('Node or drone position is inside a wall')
            positions = [r for r in rows if r['kind'] == 'position']
            if not positions:
                raise ValueError('Agent log needs at least one drone position')
            times = {}
            for row in positions:
                if row.get('source') not in ('physical', 'simulation'):
                    raise ValueError('Position requires physical/simulation source')
                stamp = utc_timestamp(row['observed_at'])
                if row['step'] in times and times[row['step']] != stamp:
                    raise ValueError('Positions at the same replay step need a consistent timestamp')
                times[row['step']] = stamp
            ordered = [times[k] for k in sorted(times)]
            if any(a >= b for a,b in zip(ordered, ordered[1:])):
                raise ValueError('Position timestamps must increase with replay step')
            identities = [(r['device_id'], r.get('step'), r['kind']) for r in rows if r['kind'] == 'position']
            if len(identities) != len(set(identities)):
                raise ValueError('Duplicate position for the same drone and step')
            self.agent_log = sorted(copy.deepcopy(rows), key=lambda r: r.get('step', -1))
            self.step = 0
        else:
            raise ValueError('Upload kind must be sensors or agents')
        self.plans.clear()
        return self.snapshot()

    def seek(self, step):
        maximum = max((r.get('step', 0) for r in self.agent_log), default=0)
        if type(step) is not int or not 0 <= step <= maximum:
            raise ValueError('Replay step outside the uploaded timeline')
        self.step = step
        self.plans.clear()

    def frame(self):
        agents, nodes, victims, events = {}, {}, [], []
        for row in self.agent_log:
            if row.get('step', 0) > self.step:
                continue
            if row['kind'] == 'node':
                nodes[row['device_id']] = row
            elif row['kind'] == 'position':
                agents[row['device_id']] = row
            elif row['kind'] == 'victim_found':
                victims.append(row)
            else:
                events.append(row)
        # Each drone uses its latest position at/before the selected step, not a future row.
        as_of = max((utc_timestamp(a['observed_at']) for a in agents.values()), default=None)
        latest = {}
        for row in self.sensors:
            try:
                stamp = utc_timestamp(row.get('observed_at'))
                if as_of is None or stamp > as_of:
                    continue
                if set(row) != FIELDS or row['source'] not in ('physical', 'simulation'):
                    continue
                if row['zone_id'] not in ('A', 'B', 'C') or row['sensor_type'] not in UNITS:
                    continue
                value = row.get('value')
                if row['quality'] != 'valid' or type(value) not in (int, float, bool) or not math.isfinite(value):
                    continue
                key = row['device_id'], row['source'], row['sensor_type']
                if key not in latest or stamp > utc_timestamp(latest[key]['observed_at']):
                    latest[key] = row
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
        hazards, unknown = [], []
        settings = self.config.get('recorded_mission', {})
        for row in latest.values():
            node = nodes.get(row['device_id'])
            if not node or row['zone_id'] != node['zone_id']:
                continue
            age = as_of - utc_timestamp(row['observed_at'])
            if age > settings.get('max_sensor_age_s', 35):
                continue
            rule = settings.get('hazard_thresholds', {}).get(row['sensor_type'])
            if not rule or rule['unit'] != row['unit']:
                if row['sensor_type'] in ('smoke_raw', 'co_raw', 'temperature'):
                    unknown.append({'device_id': row['device_id'], 'unit': row['unit'],
                                    'reason': 'No configured threshold for this unit; no automatic conversion'})
                continue
            if type(row['value']) in (int, float) and row['value'] >= rule['trigger']:
                hazards.append({'x': node['x'], 'y': node['y'], 'device_id': row['device_id'],
                                'reading': row, 'radius': 2})
        return {'agents': list(agents.values()), 'nodes': list(nodes.values()), 'victims': victims,
                'handovers': events, 'hazards': hazards, 'unclassified_hazards': unknown,
                'readings': list(latest.values())}

    def plan(self, drone_id, goal=None, battery=100):
        frame = self.frame()
        agent = next((a for a in frame['agents'] if a['device_id'] == drone_id), None)
        if agent is None:
            raise ValueError('Select a drone present at the selected replay step')
        if type(battery) not in (int, float) or not math.isfinite(battery) or not 0 <= battery <= 100:
            raise ValueError('Simulated battery must be between 0 and 100 units')
        start = agent['x'], agent['y']
        first = next(r for r in self.agent_log if r['kind'] == 'position' and r['device_id'] == drone_id)
        home = first['x'], first['y']
        blocked = {(x, y) for h in frame['hazards'] for x in range(self.grid['width'])
                   for y in range(self.grid['height']) if abs(x-h['x'])+abs(y-h['y']) <= h['radius']}
        if goal is not None:
            if not isinstance(goal, list) or len(goal) != 2 or any(type(x) is not int for x in goal):
                raise ValueError('Goal must be [x, y] in integer grid cells')
            targets = [tuple(goal)]
        else:
            # Known victims only; no undiscovered future victim coordinates enter the planner.
            targets = [(r['x'],r['y']) for r in frame['victims']]
            if not targets:
                zone = 'ABC'.index(agent['zone_id'])
                targets = [(zone*10+8, 2), (zone*10+8, 17), (zone*10+2, 17), (zone*10+2, 2)]
        choices = []
        for target in targets:
            path = route(self.grid, start, target, blocked)
            back = route(self.grid, target, home, blocked)
            if path and back:
                choices.append((len(path)+len(back), target, path, back))
        common = {'drone_id': drone_id, 'replay_step': self.step, 'start': list(start), 'home': list(home),
                  'battery_units': battery, 'mode': 'offline_route_preview',
                  'source_position': copy.deepcopy(agent), 'hardware_dispatched': False,
                  'warning': 'Grid route checks walls and configured hazards only. Smoke mV thresholds and flight dynamics are uncalibrated.'}
        if not choices:
            plan = dict(common, status='blocked', reason='No outbound and return route avoiding walls/configured hazards')
        else:
            _, target, path, back = min(choices)
            steps = len(path)+len(back)-2
            reserve = 10
            if steps + reserve > battery:
                fallback = route(self.grid, start, home, blocked)
                if fallback and len(fallback)-1+reserve <= battery:
                    plan = dict(common, status='return_home', reason='Inspection would consume the return reserve',
                                goal=list(home), outbound=list(map(list,fallback)), return_path=[],
                                energy_units=len(fallback)-1, reserve_units=reserve)
                else:
                    plan = dict(common, status='blocked', reason='Insufficient battery for a return route with reserve')
            else:
                plan = dict(common, status='planned', goal=list(target), outbound=list(map(list,path)),
                            return_path=list(map(list,back)), energy_units=steps, reserve_units=reserve)
        plan['plan_id'] = digest(plan)[:24]
        self.plans[drone_id] = plan
        return copy.deepcopy(plan)

    def simulate(self, drone_id, plan_id):
        plan = self.plans.get(drone_id)
        if not plan or plan['plan_id'] != plan_id or plan['replay_step'] != self.step:
            raise ValueError('Plan changed; make a new plan before simulating')
        if plan['status'] not in ('planned', 'return_home'):
            raise ValueError('This plan cannot run')
        path = plan['outbound'] + plan['return_path'][1:]
        plan['simulation'] = {'status': 'simulated_complete', 'final_position': path[-1],
                              'battery_remaining_units': plan['battery_units']-plan['energy_units'],
                              'route': path, 'inspection_completed': plan['status'] == 'planned',
                              'real_flight': False}
        return copy.deepcopy(plan)

    def snapshot(self):
        frame = self.frame()
        tasks = [{'task_id': digest(v)[:24], 'operation': 'inspect_reported_victim',
                  'zone_id': v['zone_id'], 'goal': [v['x'],v['y']], 'coordinates': 'grid_cells',
                  'source_event': v, 'status': 'suggestion_requires_robot_adapter',
                  'robot_id': None, 'hardware_dispatched': False} for v in frame['victims']]
        return {'mode': 'recorded_files', 'grid': self.grid, 'step': self.step,
                'max_step': max((r.get('step', 0) for r in self.agent_log), default=0),
                'sensor_report': self.report, 'agent_rows': len(self.agent_log),
                'frame': frame, 'plans': list(self.plans.values()), 'robot_task_suggestions': tasks,
                'note': 'Historical replay. Grid zones A/B/C are preserved; CSE only supports A/B until mapping is agreed.'}

    def load_defaults(self):
        folder = Path(__file__).resolve().parents[3] / 'simulation'
        # Default demo needs no upload. New uploads replace the relevant dataset atomically.
        for kind, name in [('agents','sim_agent_log.jsonl'), ('sensors','sim_sensor_data.jsonl')]:
            path = folder / name
            if path.is_file():
                self.upload(kind, path.read_bytes())
