"""File uploads through the team's oneM2M MQTT binding; no topic bridge."""
from __future__ import annotations

import os
from pathlib import Path

from .core import digest, validate_reading
from .mqtt import native_cin
from .recordings import parse_records


def native_plan(config, zone, path):
    if zone not in config.get('native_mqtt', {}):
        raise ValueError('Configure native_mqtt for the requested zone')
    binding = config['native_mqtt'][zone]
    device = binding.get('device_id')
    if not device or device not in config['devices']:
        raise ValueError('Bind the MQTT account to an explicit registered device_id')
    if binding['originator'] != config['devices'][device]['originator']:
        raise ValueError('MQTT originator differs from the device registration')
    rows = parse_records(Path(path).read_bytes())
    events, rejected, seen = [], [], set()
    for index, row in enumerate(rows):
        try:
            validate_reading(row, config['devices'])
            if row['device_id'] != device or row['zone_id'] != zone:
                raise ValueError('This MQTT account is not bound to this device/zone')
            key = digest(row)
            if key in seen:
                continue
            seen.add(key)
            target = f"/{binding['cse_id'].strip('/')}/{binding['cse_name']}/{device}/{row['sensor_type']}"
            events.append({'index': index, 'target': target, 'reading': row, 'event_id': key})
        except (ValueError, KeyError, TypeError) as exc:
            rejected.append({'index': index, 'reason': str(exc)})
    return {'mode': 'oneM2M_MQTT_binding', 'zone': zone, 'input_rows': len(rows),
            'request_topic': f"/oneM2M/req/{binding['originator']}/{binding['cse_id'].strip('/')}/json",
            'response_topic': f"/oneM2M/resp/{binding['originator']}/{binding['cse_id'].strip('/')}/json",
            'events': events, 'rejected': rejected, 'complete': False, 'sent': False}


def native_upload(config, plan, skip_invalid=False, send=False, sender=native_cin):
    if plan['rejected'] and not skip_invalid:
        raise ValueError('Inspect rejected rows first, or explicitly use --skip-invalid')
    if not plan['events']:
        raise ValueError('No compatible records for this account')
    if not send:
        return dict(plan, note='Preview only; use --send to publish. Timestamps and units are preserved.')
    binding = config['native_mqtt'][plan['zone']]
    if not binding.get('allow_upload', False):
        raise ValueError('Uploads are not enabled for this native MQTT account')
    if binding.get('username') and not os.getenv(binding.get('password_env', 'MQTT_PASSWORD')):
        raise ValueError('Set the configured MQTT password environment variable privately before sending')
    result = dict(plan, events=[], sent=True)
    for event in plan['events']:
        try:
            response = sender(binding, binding['originator'], binding['cse_id'], event['target'], event['reading'])
            if type(response.get('rsc')) is not int or response['rsc'] != 2001:
                raise ValueError('Expected rsc=2001 for CIN creation')
            result['events'].append({'index': event['index'], 'rqi': response['rqi'], 'rsc': response['rsc'],
                                     'event_id': event['event_id'], 'created': True})
        except Exception as exc:
            result['events'].append({'index': event['index'], 'created': False, 'error': type(exc).__name__})
            return result
    result['complete'] = True
    result['note'] = 'Each event received matching oneM2M rqi and rsc=2001; repeated sends create new CINs.'
    return result
