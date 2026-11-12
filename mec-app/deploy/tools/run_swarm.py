"""Run the original simulator with explicit MEC task allocation; source stays unchanged."""
import importlib.util
import json
import os
from pathlib import Path
import sys
from urllib.request import Request,urlopen

def load_simulation():
    path=Path(__file__).resolve().parents[3]/'simulation'/'rescue_sim.py'
    spec=importlib.util.spec_from_file_location('rescuetwin_team_sim',path)
    sim=importlib.util.module_from_spec(spec);spec.loader.exec_module(sim)
    base=sim.World
    class CoordinatedWorld(base):
        def sync_mec(self):
            result=None
            for agent in self.agents:
                row,col=agent['pos']
                cells=[dict(x=c,y=r,hazard=float(self.haz[r,c]))
                       for r in range(max(0,row-sim.SENSE_R),min(sim.H,row+sim.SENSE_R+1))
                       for c in range(max(0,col-sim.SENSE_R),min(sim.W,col+sim.SENSE_R+1)) if not self.walls[r,c]]
                payload=dict(mission_id=self.mission_id,drone_id=agent['id'],sequence=self.t,tick=self.t,
                             cells=cells,position=dict(x=col,y=row))
                headers={'Content-Type':'application/json'}
                if os.getenv('SWARM_API_TOKEN'):headers['Authorization']='Bearer '+os.environ['SWARM_API_TOKEN']
                req=Request(self.mec_url+'/v1/reports',data=json.dumps(payload).encode(),headers=headers)
                with urlopen(req,timeout=10) as response:result=json.load(response)
            if (result.get('width'),result.get('height'),result.get('mission_id'))!=(sim.W,sim.H,self.mission_id):
                raise ValueError('MEC map does not match the current mission')
            if result.get('coordination',{}).get('policy')!='frontier_auction_v1':
                raise ValueError('Start the updated swarm service: coordination policy missing')
            self.mec_snapshot=result
            self.phero.fill(0);self.seen.fill(False);self.haz.fill(0)
            for c in result['pheromone']:
                self.phero[c['y'],c['x']]=c['value'];self.seen[c['y'],c['x']]=True
            for c in result['hazards']:self.haz[c['y'],c['x']]=c['value']
            self.blocked=(self.haz>sim.HAZARD_BLOCK)&~self.walls

        def move_agents(self):
            if self.mode!='swarm' or not self.mec_url:return super().move_agents()
            actions=self.mec_snapshot['coordination']['actions'];destinations=[]
            for a in self.agents:
                x,y=actions[a['id']]['position'];r,c=a['pos']
                if not (0<=x<sim.W and 0<=y<sim.H) or abs(x-c)+abs(y-r)>1 or self.walls[y,x] or self.blocked[y,x]:
                    raise ValueError('MEC returned an invalid move')
                destinations.append((y,x))
            if len(set(destinations))!=len(destinations):raise ValueError('Conflicting MEC reservations')
            starts=[a['pos'] for a in self.agents]
            if any(p==starts[j] for i,p in enumerate(destinations) for j in range(len(starts)) if i!=j):
                raise ValueError('MEC move enters another drone position')
            for a,pos in zip(self.agents,destinations):
                a['pos']=pos;zone=sim.zone_of(pos[1])
                if zone!=a['zone']:
                    self.handovers.append(dict(step=self.t,drone=a['id'],src=a['zone'],dst=zone))
                    if self.logging:self.log_agent.append(dict(kind='handover',observed_at=self.ts(),step=self.t,
                        device_id=a['id'],from_zone=a['zone'],to_zone=zone,note='simulated zone crossing; CSE handover not performed'))
                    a['zone']=zone
    sim.World=CoordinatedWorld
    return sim

if __name__=='__main__':
    if not os.getenv('SWARM_MEC_URL'):os.environ['SWARM_MEC_URL']='http://127.0.0.1:8780'
    import argparse
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--output-dir',type=Path)
    args,remaining=parser.parse_known_args()
    sim=load_simulation()
    if args.output_dir:
        folder=args.output_dir.resolve();folder.mkdir(parents=True,exist_ok=True)
        export=sim.export_json
        def export_to_folder(data):
            original=sim.HERE
            try:sim.HERE=folder;return export(data)
            finally:sim.HERE=original
        sim.export_json=export_to_folder
    sys.argv=[sys.argv[0],*remaining]
    sim.main()
