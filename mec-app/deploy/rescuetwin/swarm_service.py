"""Tick-clocked, per-mission map fusion and swarm allocation (legacy and live maps)."""
import argparse
import copy
import hmac
import json
import math
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
try:
    from .layout import MapSpec,legacy_spec,LEGACY_WIDTH,LEGACY_HEIGHT
    from .swarm_logic import Coordinator
except ImportError:
    from layout import MapSpec,legacy_spec,LEGACY_WIDTH,LEGACY_HEIGHT
    from swarm_logic import Coordinator

class Fusion:
    def __init__(self):self.missions={};self.coordinators={};self.lock=threading.Lock()
    def report(self,body):
        if not isinstance(body,dict):raise ValueError('Expected report object')
        mission,drone=body['mission_id'],body['drone_id']
        if not isinstance(mission,str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',mission):raise ValueError('Invalid mission_id')
        spec=MapSpec.from_dict(body['map']) if 'map' in body else legacy_spec()
        if drone not in spec.drone_ids:raise ValueError('Drone not in this mission registry')
        tick,seq=body['tick'],body['sequence']
        if any(type(v) is not int or v<0 for v in (tick,seq)):raise ValueError('tick/sequence must be nonnegative integers')
        cells=body['cells'];parsed={}
        if not isinstance(cells,list) or not 1<=len(cells)<=spec.width*spec.height:raise ValueError('Invalid observation count')
        for cell in cells:
            x,y,h=cell['x'],cell['y'],cell['hazard']
            if not spec.inside(x,y):raise ValueError('Cell outside mission map')
            if type(h) not in (int,float) or not math.isfinite(h) or not 0<=h<=1:raise ValueError('hazard must be finite and in [0,1]')
            if (x,y) in parsed:raise ValueError('Duplicate cell')
            parsed[x,y]=h
        def point(value):
            if not isinstance(value,dict) or not spec.inside(value.get('x'),value.get('y')):raise ValueError('Invalid position/goal')
            p=value['x'],value['y']
            if p in spec.walls:raise ValueError('Position/goal is inside wall')
            return p
        position=point(body['position']) if body.get('position') is not None else None
        goal=point(body['goal']) if body.get('goal') is not None else None
        if position is not None and position not in parsed:raise ValueError('Position must be observed')
        # Validate reports before changing mission state.
        now=time.monotonic()
        with self.lock:
            for key in list(self.missions):
                if now-self.missions[key]['access']>3600:del self.missions[key];self.coordinators.pop(key,None)
            if mission in self.missions and self.missions[mission]['map']!=spec.payload():raise ValueError('Mission map/registry cannot change; use a new mission ID')
            if mission not in self.missions:
                if len(self.missions)>=32:raise ValueError('Mission capacity reached')
                self.missions[mission]=dict(tick=tick,reports={},seen={},access=now,map=spec.payload())
                self.coordinators[mission]=Coordinator(spec)
            state=self.missions[mission];previous=state['reports'].get(drone)
            if position is not None and any(d!=drone and r['tick']==tick and r.get('position')==position for d,r in state['reports'].items()):raise ValueError('Drone positions overlap')
            if previous and seq==previous['sequence']:
                if previous['body']!=body:raise ValueError('Conflicting duplicate sequence')
            else:
                if previous and (seq<previous['sequence'] or tick<previous['tick']):raise ValueError('Out-of-order report')
                if tick<state['tick']-8 or tick>state['tick']+1:raise ValueError('Report tick outside active mission window')
                state['reports'][drone]=dict(sequence=seq,tick=tick,cells=parsed,body=copy.deepcopy(body),received=now,position=position,goal=goal)
                for p in parsed:state['seen'][p]=max(tick,state['seen'].get(p,-1))
                state['tick']=max(tick,state['tick'])
            state['access']=now;current=state['tick'];hazards={}
            for r in state['reports'].values():
                if current-r['tick']<=8 and now-r['received']<=30:
                    for p,h in r['cells'].items():hazards[p]=max(h,hazards.get(p,0))
            # Allocate moves only when every drone reports the same round.
            current_reports={d:r for d,r in state['reports'].items() if r.get('position') is not None and r['tick']==current and now-r['received']<=30}
            coordination=self.coordinators[mission].plan({d:r['position'] for d,r in current_reports.items()},state['seen'],hazards,
                         {d:r['goal'] for d,r in current_reports.items() if r.get('goal') is not None}) if len(current_reports)==len(spec.drone_ids) else dict(policy='frontier_auction_v1',actions={},status='waiting_for_current_peer_positions')
            return dict(mission_id=mission,tick=current,width=spec.width,height=spec.height,zone_x=spec.zone_x,zones=['A','B'],
                source='mec_swarm_fusion',pheromone=[dict(x=x,y=y,value=.997**(current-t)) for (x,y),t in state['seen'].items()],
                hazards=[dict(x=x,y=y,value=h,blocked=h>.6) for (x,y),h in hazards.items() if h>0],coordination=coordination,
                active_drones=[d for d,r in state['reports'].items() if current-r['tick']<=8 and now-r['received']<=30])

def make_server(host='127.0.0.1',port=8780,token=None):
    fusion=Fusion()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,value):
            raw=json.dumps(value,allow_nan=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):self.reply(200 if self.path=='/health' else 404,dict(service='swarm-fusion',maps='per-mission',max_drones=16,zones=['A','B']))
        def do_POST(self):
            if self.path!='/v1/reports':return self.reply(404,{'error':'Unknown endpoint'})
            if token and not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):return self.reply(401,{'error':'Authentication required'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=1024000:raise ValueError('Body must be 1..1024000 bytes')
                self.connection.settimeout(10);body=json.loads(self.rfile.read(size));self.reply(200,fusion.report(body))
            except (ValueError,KeyError,TypeError,TimeoutError) as exc:self.reply(400,{'error':str(exc)})
    server=ThreadingHTTPServer((host,port),Handler);server.daemon_threads=True;return server

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8780);args=parser.parse_args();token=os.getenv('SWARM_API_TOKEN')
    if args.host not in ('127.0.0.1','localhost','::1') and not token:parser.error('Set SWARM_API_TOKEN before exposing the service')
    server=make_server(args.host,args.port,token);print(f'Swarm Fusion listening on {args.host}:{args.port}; per-mission maps and drone registry',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
if __name__=='__main__':main()
