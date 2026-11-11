"""Independent, step-clocked MEC map fusion service. Coordinates are grid cells.

Reports are full local observations, not additive increments. A new mission ID is
required after a simulator restart. State is intentionally ephemeral.
"""
import argparse
import hmac
import json
import math
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WIDTH, HEIGHT = 50, 30


class Fusion:
    def __init__(self):
        self.missions = {}
        self.lock = threading.Lock()

    def report(self, body):
        mission, drone = body['mission_id'], body['drone_id']
        if not isinstance(mission, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', mission):
            raise ValueError('Invalid mission_id')
        if drone not in ('drone-A', 'drone-B'):
            raise ValueError('Only drone-A/B are configured')
        tick, seq = body['tick'], body['sequence']
        if any(type(v) is not int or v < 0 for v in (tick, seq)):
            raise ValueError('tick and sequence must be nonnegative integers')
        cells = body['cells']
        if not isinstance(cells, list) or not 1 <= len(cells) <= WIDTH * HEIGHT:
            raise ValueError('Expected 1..1500 observed cells')
        parsed = {}
        for cell in cells:
            x, y, hazard = cell['x'], cell['y'], cell['hazard']
            if type(x) is not int or type(y) is not int or not (0 <= x < WIDTH and 0 <= y < HEIGHT):
                raise ValueError('Cell outside 50x30 grid')
            if type(hazard) not in (int, float) or not math.isfinite(hazard) or not 0 <= hazard <= 1:
                raise ValueError('hazard must be finite and in [0,1]')
            if (x, y) in parsed:
                raise ValueError('Duplicate cell')
            parsed[x, y] = hazard
        now = time.monotonic()
        with self.lock:
            # Remove abandoned runs and bound memory for a long-running host.
            for key in list(self.missions):
                if now - self.missions[key]['access'] > 3600:
                    del self.missions[key]
            if mission not in self.missions:
                if len(self.missions) >= 32:
                    raise ValueError('Mission capacity reached; use a fresh service or wait for expiry')
                self.missions[mission] = dict(tick=tick, reports={}, seen={}, access=now)
            state = self.missions[mission]
            previous = state['reports'].get(drone)
            if previous and seq == previous['sequence']:
                if previous['body'] != body:
                    raise ValueError('Conflicting duplicate sequence')
            else:
                if previous and (seq < previous['sequence'] or tick < previous['tick']):
                    raise ValueError('Out-of-order report')
                if tick < state['tick'] - 8 or tick > state['tick'] + 1:
                    raise ValueError('Report tick outside active mission window')
                state['reports'][drone] = dict(sequence=seq, tick=tick, cells=parsed, body=body, received=now)
                for p in parsed:
                    state['seen'][p] = max(tick, state['seen'].get(p, -1))
                state['tick'] = max(tick, state['tick'])
            state['access'] = now
            current = state['tick']
            hazards = {}
            for report in state['reports'].values():
                if current - report['tick'] <= 8 and now - report['received'] <= 30:
                    for p, value in report['cells'].items():
                        hazards[p] = max(value, hazards.get(p, 0))
            return dict(mission_id=mission, tick=current, width=WIDTH, height=HEIGHT,
                        zones=['A', 'B'], source='mec_swarm_fusion',
                        pheromone=[dict(x=x, y=y, value=(1-0.003)**(current-t)) for (x,y),t in state['seen'].items()],
                        hazards=[dict(x=x,y=y,value=v,blocked=v>0.6) for (x,y),v in hazards.items() if v>0],
                        active_drones=[d for d,r in state['reports'].items() if current-r['tick']<=8 and now-r['received']<=30])


def make_server(host='127.0.0.1', port=8780, token=None):
    fusion = Fusion()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, value):
            raw = json.dumps(value, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.reply(200 if self.path == '/health' else 404,
                       dict(service='swarm-fusion', width=WIDTH, height=HEIGHT, zones=['A','B']))

        def do_POST(self):
            if self.path != '/v1/reports':
                return self.reply(404, {'error':'Unknown endpoint'})
            if token and not hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer '+token):
                return self.reply(401, {'error':'Authentication required'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 256000:
                    raise ValueError('Body must be 1..256000 bytes')
                self.connection.settimeout(10)
                body = json.loads(self.rfile.read(size))
                self.reply(200, fusion.report(body))
            except (ValueError, KeyError, TypeError, TimeoutError) as exc:
                self.reply(400, {'error':str(exc)})
    server = ThreadingHTTPServer((host,port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8780)
    args = parser.parse_args()
    token = os.environ.get('SWARM_API_TOKEN')
    if args.host not in ('127.0.0.1','localhost','::1') and not token:
        parser.error('Set SWARM_API_TOKEN before exposing the service to other hosts')
    server = make_server(args.host, args.port, token)
    print(f'Swarm fusion listening on {args.host}:{args.port} (50x30, A/B)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
