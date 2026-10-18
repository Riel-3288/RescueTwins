import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

spec = importlib.util.spec_from_file_location('twin_server', Path(__file__).with_name('server.py'))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def cin(name='temperature', value=28.9, **extra):
    payload = {'device_id': 'node-01', 'zone_id': 'A', 'sensor_type': name,
               'value': value, 'unit': app.SENSORS[name][1], 'quality': 'valid',
               'source': 'physical', 'observed_at': None}
    payload.update(extra)
    return {'ri': 'cin-test', 'ct': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S,%f'),
            'con': json.dumps(payload)}


class DataTests(unittest.TestCase):
    def test_real_reading_without_sensor_clock_uses_receipt_time(self):
        result = app.normalize('temperature', cin())
        self.assertEqual(result['value'], 28.9)
        self.assertIsNone(result['observed_at'])
        self.assertFalse(result['stale'])

    def test_invalid_sensor_never_shown_as_zero(self):
        result = app.normalize('temperature', cin(value=None, quality='invalid'))
        self.assertEqual(result['state'], 'invalid')
        self.assertIsNone(result['value'])

    def test_presence_false_is_valid(self):
        result = app.normalize('presence', cin('presence', False))
        self.assertEqual(result['state'], 'valid')
        self.assertIs(result['value'], False)

    def test_wrong_units_or_identity_rejected(self):
        for change in ({'unit': 'Pa'}, {'device_id': 'node-02'}, {'sensor_type': 'humidity'}, {'value': True}):
            self.assertEqual(app.normalize('temperature', cin(**change))['state'], 'invalid')

    def test_stale_even_if_device_clock_is_in_future(self):
        record = cin(observed_at='2099-01-01T00:00:00Z')
        record['ct'] = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime('%Y%m%dT%H%M%S')
        self.assertTrue(app.normalize('temperature', record)['stale'])

    def test_nonfinite_json_rejected(self):
        with self.assertRaises(ValueError):
            app.normalize('temperature', cin(value=float('nan')))

    def test_only_mobius_route_and_web_identity_used(self):
        with patch.object(app, 'request', return_value=(200, {'m2m:cin': cin()})) as call:
            self.assertEqual(app.reading('temperature')['value'], 28.9)
            args = call.call_args.args
            self.assertTrue(args[1].startswith(app.MOBIUS + '/~/id-mn-zone-a/'))
            self.assertEqual(args[2], '/Mobius4/CRescueTwinWeb')

    def test_permission_empty_and_transport_failures_are_distinct(self):
        for code, state in ((403, 'error'), (404, 'empty'), (503, 'error')):
            with patch.object(app, 'request', return_value=(code, {})):
                self.assertEqual(app.reading('temperature')['state'], state)
        with patch.object(app, 'request', side_effect=RuntimeError('Offline')):
            self.assertEqual(app.reading('temperature')['state'], 'error')

    def test_partial_failure_does_not_hide_other_sensors(self):
        def fake(method, url, origin):
            name = url.split('/')[-2]
            return (200, {'m2m:cin': cin()}) if name == 'temperature' else (404, {})
        with patch.object(app, 'request', side_effect=fake):
            values = app.snapshot()['readings']
        self.assertEqual(len(values), 6)
        self.assertEqual(values[0]['value'], 28.9)
        self.assertEqual(values[1]['state'], 'empty')


if __name__ == '__main__':
    unittest.main()
