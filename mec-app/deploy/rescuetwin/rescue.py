"""Turn sensor alerts into local inspection tasks."""
from __future__ import annotations

import copy
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone

from .core import Store, digest, utcnow, utc_timestamp


class TelemetryGate:
    """Throttle simulator readings without filtering physical publishers."""
    def __init__(self):
        self.previous = {}
        self.skipped = 0

    def accept(self, reading, backlog=0, now=None):
        now = time.monotonic() if now is None else now
        key = (reading['device_id'], reading['sensor_type'])
        old = self.previous.get(key)
        delta = {'temperature': 1, 'humidity': 1, 'pressure': 1, 'smoke_raw': 50, 'co_raw': 50}.get(key[1], 0)
        changed = old is None or (reading['value'] != old[1] if key[1] == 'presence' else abs(reading['value']-old[1]) >= delta)
        # Always forward threshold crossings.
        thresholds = {'temperature': (50, 60), 'smoke_raw': (2000, 2500), 'co_raw': (1800, 2300)}
        crossing = old is not None and key[1] in thresholds and any(
            (old[1] >= t) != (reading['value'] >= t) for t in thresholds[key[1]])
        minimum, heartbeat = (5, 15) if backlog >= 200 else (1, 5)
        if old is None or crossing or (now-old[0] >= minimum and changed) or now-old[0] >= heartbeat:
            self.previous[key] = (now, reading['value'])
            return True
        self.skipped += 1
        return False


class Rescue:
    def __init__(self, config, factory, publish, bridge=None, store=None):
        self.config, self.factory, self.publish, self.bridge = config, factory, publish, bridge
        self.settings = config.get('rescue', {})
        self.enabled = self.settings.get('enabled', True)
        self.store = store or Store(self.settings.get('state_file', str(Path(config['state_file']).with_name('rescue.sqlite'))))
        saved = self.store.get('rescue', {})
        self.incidents = saved.get('incidents', [])
        self.tasks = saved.get('tasks', [])
        self.events = saved.get('events', [])
        self.signals = {tuple(s['key']):s['value'] for s in saved.get('signals', [])}
        self.outgoing = []
        self.last_retry = {}
        self.ignored = 0
        # Cancel old tasks because agent positions reset on restart.
        for task in self.tasks:
            if task['status'] not in ('completed', 'cancelled', 'failed'):
                task.update(status='cancelled', reason='Executor restarted; fresh dispatch required', updated_at=utcnow())
        if saved:
            self.save()

    def save(self):
        self.store.set('rescue', {'incidents': self.incidents, 'tasks': self.tasks[-200:], 'events': self.events[-200:],
                                 'signals': [{'key':list(k), 'value':v} for k,v in self.signals.items()]})

    def event(self, kind, incident, task=None, **details):
        event = {'event_id': uuid.uuid4().hex, 'kind': kind, 'incident_id': incident['incident_id'],
                 'device_id': incident['device_id'], 'source': incident['source'], 'observed_at': utcnow(), **details}
        if task:
            event.update(task_id=task['task_id'], command_id=task['command_id'], agent_id=task['agent_id'])
        self.events.append(event)
        self.events = self.events[-200:]
        if self.bridge:
            self.bridge.store.enqueue_event(incident['zone_id'], 'events', event)
        self.outgoing.append(('rescuetwin-control/events', event))
        self.save()

    def ingest(self, reading, now=None):
        if not self.enabled or reading['sensor_type'] not in ('temperature', 'smoke_raw', 'co_raw'):
            return
        now = time.time() if now is None else now
        observed = utc_timestamp(reading['observed_at'])
        key = (reading['device_id'], reading['source'], reading['sensor_type'])
        previous = self.signals.get(key)
        if (reading['quality'] != 'valid' or not -5 <= now-observed <= self.settings.get('max_age_s', 35)
                or (previous and observed <= previous['timestamp'])):
            self.ignored += 1
            return
        rule = self.settings.get('thresholds', {}).get(reading['sensor_type'], {})
        defaults = {'temperature': (60, 50), 'smoke_raw': (2500, 2000), 'co_raw': (2300, 1800)}
        high, low = rule.get('trigger', defaults[key[2]][0]), rule.get('clear', defaults[key[2]][1])
        clear_count = 0
        if previous and previous['active']:
            clear_count = previous.get('clear_count',0)+1 if reading['value'] < low else 0
            active = clear_count < 2
        else:
            active = reading['value'] >= high
        self.signals[key] = {'timestamp': observed, 'active': active, 'value': reading['value'], 'clear_count':clear_count}
        incident = next((i for i in reversed(self.incidents) if i['device_id'] == key[0] and i['source'] == key[1]
                         and i['status'] != 'resolved'), None)
        if active and incident is None:
            position = self.settings.get('sensor_positions', {}).get(key[0])
            if position:
                x, y = int(position['x_m']/self.factory.cell_m), int(position['y_m']/self.factory.cell_m)
            elif key[0] in self.factory.sensor_positions:
                x, y = self.factory.sensor_positions[key[0]]
            else:
                self.ignored += 1
                return
            if not self.factory.inside(x, y):
                self.ignored += 1
                return
            incident = {'incident_id': uuid.uuid4().hex, 'device_id': key[0], 'source': key[1], 'zone_id': reading['zone_id'],
                        'status': 'active', 'acknowledged': False, 'x': x, 'y': y, 'opened_at': utcnow(),
                        'trigger': copy.deepcopy(reading), 'last_observed_at': reading['observed_at'], 'clear_samples': 0}
            self.incidents.append(incident)
            self.event('incident_opened', incident, message=f"{key[0]}: {key[2]} exceeded the demo threshold")
        if incident:
            active_signals = [v for k, v in self.signals.items() if k[:2] == key[:2] and v['active']]
            incident['last_observed_at'] = reading['observed_at']
            if active_signals:
                incident['clear_samples'] = 0
                incident['last_observed_at'] = datetime.fromtimestamp(max(s['timestamp'] for s in active_signals),timezone.utc).isoformat()
            else:
                incident['clear_samples'] = 2
                if incident['clear_samples'] >= 2:
                    incident.update(status='resolved', resolved_at=utcnow())
                    self.event('incident_resolved', incident, message='Fresh readings are below the clear thresholds')
                    for task in self.tasks:
                        if task['incident_id'] == incident['incident_id'] and task['status'] not in ('completed', 'cancelled', 'failed'):
                            self.hold_agent(task)
                            task.update(status='cancelled', reason='Sensor hazard cleared', updated_at=utcnow())
                            self.event('task_cancelled', incident, task, message=task['reason'])
            self.factory.set_observed_hazards(self.hazards())

    def hazards(self):
        if not self.enabled:
            return []
        return [{'incident_id': i['incident_id'], 'x': i['x'], 'y': i['y'], 'radius': 2,
                 'source': i['source'], 'device_id': i['device_id']} for i in self.incidents if i['status'] != 'resolved']

    def dispatch(self, incident):
        busy = {t['agent_id'] for t in self.tasks if t['status'] not in ('completed', 'cancelled', 'failed')}
        choices = []
        for agent in self.factory.agents:
            if agent['id'] in busy:
                continue
            for dx, dy in ((-3,0),(3,0),(0,-3),(0,3),(-3,-2),(-3,2),(3,-2),(3,2)):
                goal = (incident['x']+dx, incident['y']+dy)
                if not self.factory.inside(*goal):
                    continue
                route = self.factory.route((agent['x'], agent['y']), goal)
                if route:
                    choices.append((len(route), agent['id'], goal))
        if not choices:
            if incident.get('dispatch_state') != 'no_safe_route':
                incident['dispatch_state'] = 'no_safe_route'
                self.event('dispatch_blocked', incident, message='No available agent has a safe staging route')
            return
        _, agent_id, goal = min(choices)
        task = {'task_id': uuid.uuid4().hex, 'command_id': uuid.uuid4().hex,
                'incident_id': incident['incident_id'], 'agent_id': agent_id, 'executor': 'local_simulation',
                'operation': 'inspect', 'goal': list(goal), 'status': 'dispatched',
                'created_at': utcnow(), 'updated_at': utcnow()}
        self.tasks.append(task)
        incident['dispatch_state'] = 'assigned'
        self.event('task_dispatched', incident, task, message=f'{agent_id} assigned to a safe inspection staging cell')
        self.send_command(task)

    def send_command(self, task):
        command = {key: task[key] for key in ('task_id', 'command_id', 'incident_id', 'agent_id', 'executor', 'operation', 'goal', 'created_at')}
        self.outgoing.append(('rescuetwin-control/commands/' + task['agent_id'], command))
        self.last_retry[task['task_id']] = time.monotonic()

    def feedback(self, value):
        if not isinstance(value, dict):
            return
        task = next((t for t in self.tasks if t['command_id'] == value.get('command_id') and t['task_id'] == value.get('task_id')
                     and t['agent_id'] == value.get('agent_id')), None)
        if not task or task['status'] in ('completed', 'cancelled', 'failed') or value.get('executor') != 'local_simulation':
            return
        status = value.get('status')
        allowed = {'dispatched': {'accepted', 'blocked', 'failed'}, 'accepted': {'moving', 'completed', 'blocked', 'failed'},
                   'moving': {'moving', 'completed', 'blocked', 'failed'}, 'blocked': {'moving', 'completed', 'failed'}}
        if status not in allowed.get(task['status'], set()):
            return
        try:
            if not -5 <= time.time()-utc_timestamp(value['observed_at']) <= 35:
                return
        except (KeyError, ValueError, TypeError):
            return
        previous = task['status']
        task.update(status=status, updated_at=utcnow(), feedback=copy.deepcopy(value))
        if status == 'accepted':
            task['accepted_sim_s'] = self.factory.elapsed
        incident = next(i for i in self.incidents if i['incident_id'] == task['incident_id'])
        if status != previous:
            self.event('task_' + status, incident, task, message=value.get('message', status))
        if status == 'completed':
            task['completed_at'] = utcnow()
            task['observation_to_completion_ms'] = round((utc_timestamp(task['completed_at'])-utc_timestamp(incident['trigger']['observed_at']))*1000)
            self.save()

    def tick(self):
        self.factory.set_observed_hazards(self.hazards())
        if self.enabled and self.settings.get('auto_dispatch', True):
            for incident in self.incidents:
                if incident['status'] == 'resolved':
                    continue
                related = [t for t in self.tasks if t['incident_id'] == incident['incident_id']]
                if not related:
                    self.dispatch(incident)
        for task in self.tasks:
            if task['status'] == 'dispatched' and time.monotonic()-self.last_retry.get(task['task_id'], 0) > 3:
                if time.time()-utc_timestamp(task['created_at']) > 30:
                    task.update(status='failed', reason='Executor did not acknowledge within 30s')
                    incident = next(i for i in self.incidents if i['incident_id'] == task['incident_id'])
                    self.event('task_failed', incident, task, message=task['reason'])
                else:
                    self.send_command(task)
            elif task['status'] in ('accepted','moving','blocked') and not self.factory.paused and self.factory.elapsed-task.get('accepted_sim_s',self.factory.elapsed) > self.settings.get('execution_timeout_s',120):
                self.hold_agent(task)
                task.update(status='failed', reason='Inspection execution timed out')
                incident = next(i for i in self.incidents if i['incident_id'] == task['incident_id'])
                self.event('task_failed', incident, task, message=task['reason'])
        for topic, value in self.outgoing:
            self.publish(topic, value)
        self.outgoing.clear()

    def acknowledge(self, incident_id):
        incident = next((i for i in self.incidents if i['incident_id'] == incident_id), None)
        if incident is None:
            raise ValueError('Incident not found')
        if not incident['acknowledged']:
            incident.update(acknowledged=True, acknowledged_at=utcnow())
            self.event('incident_acknowledged', incident, message='Operator acknowledged the incident')

    def cancel_agent(self, agent_id, reason):
        for task in self.tasks:
            if task['agent_id'] == agent_id and task['status'] not in ('completed','cancelled','failed'):
                self.hold_agent(task)
                task.update(status='cancelled', reason=reason, updated_at=utcnow())
                incident = next(i for i in self.incidents if i['incident_id'] == task['incident_id'])
                self.event('task_cancelled', incident, task, message=reason)

    def reset_simulation(self):
        for incident in self.incidents:
            if incident['source'] == 'simulation' and incident['status'] != 'resolved':
                incident.update(status='resolved', resolved_at=utcnow(), resolution='scenario_reset')
                self.event('incident_reset', incident, message='Operator reset the simulated scenario')
        self.signals = {k:v for k,v in self.signals.items() if k[1] != 'simulation'}

    def hold_agent(self, task):
        agent = next((a for a in self.factory.agents if a['id'] == task['agent_id']), None)
        if agent and agent['goal'] == task['goal']:
            agent.update(goal=[agent['x'], agent['y']], path=[], status='ready', _planned=False, _manual=False)

    def retry(self, incident_id):
        incident = next((i for i in self.incidents if i['incident_id'] == incident_id and i['status'] != 'resolved'), None)
        if not incident:
            raise ValueError('Active incident not found')
        for task in self.tasks:
            if task['incident_id'] == incident_id and task['status'] not in ('completed','cancelled','failed'):
                self.cancel_agent(task['agent_id'], 'Operator requested a fresh inspection')
        self.factory.set_observed_hazards(self.hazards())
        self.dispatch(incident)

    def snapshot(self):
        return copy.deepcopy({'enabled': self.enabled, 'executor': 'local_simulation', 'incidents': self.incidents[-100:],
                              'tasks': self.tasks[-100:], 'events': self.events[-100:], 'ignored_readings': self.ignored})


class LocalExecutor:
    """Execute and acknowledge local inspection commands."""
    def __init__(self, factory, rescue, publish):
        self.factory, self.rescue, self.publish = factory, rescue, publish
        self.accepted = {}
        self.last_emit = {}

    def emit(self, task, status, agent, message):
        value = {'feedback_id': uuid.uuid4().hex, 'command_id': task['command_id'], 'task_id': task['task_id'],
                 'incident_id': task['incident_id'], 'agent_id': task['agent_id'], 'status': status,
                 'executor': 'local_simulation', 'x_m': (agent['x']+.5)*2, 'y_m': (agent['y']+.5)*2,
                 'observed_at': utcnow(), 'message': message}
        self.publish('rescuetwin-control/feedback/' + agent['id'], value)
        self.accepted[task['command_id']] = status
        self.last_emit[task['command_id']] = time.monotonic()

    def command(self, value):
        task = next((t for t in self.rescue.tasks if t['command_id'] == value.get('command_id') and t['status'] not in ('completed','cancelled','failed')), None)
        if not task or value.get('executor') != 'local_simulation' or value.get('agent_id') != task['agent_id']:
            return
        expected = {k: task[k] for k in ('task_id','command_id','incident_id','agent_id','executor','operation','goal','created_at')}
        if digest(value) != digest(expected):
            return
        agent = next(a for a in self.factory.agents if a['id'] == task['agent_id'])
        if task['command_id'] not in self.accepted:
            try:
                self.factory.command({'action': 'goal', 'agent_id': agent['id'], 'x': task['goal'][0], 'y': task['goal'][1]})
            except ValueError as exc:
                self.emit(task, 'blocked', agent, str(exc)); return
        self.emit(task, self.accepted.get(task['command_id'], 'accepted'), agent, 'Inspection command acknowledged')

    def tick(self):
        for task in self.rescue.tasks:
            if task['status'] not in ('accepted','moving','blocked') or task['command_id'] not in self.accepted:
                continue
            agent = next(a for a in self.factory.agents if a['id'] == task['agent_id'])
            if agent['goal'] != task['goal']:
                self.rescue.cancel_agent(agent['id'], 'Goal changed by operator'); continue
            if self.factory.paused:
                continue
            status = 'completed' if agent['status'] == 'arrived' else 'blocked' if agent['status'] == 'blocked' else 'moving'
            if self.accepted[task['command_id']] != status or (task['status'] != status and time.monotonic()-self.last_emit.get(task['command_id'],0) > 2):
                self.emit(task, status, agent, 'Safe staging point reached; inspection complete' if status == 'completed' else 'No safe route' if status == 'blocked' else 'Following the inspection route')
