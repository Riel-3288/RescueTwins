"""Explicit demo coordinate mapping and local robot task/feedback integration."""
import copy
import math
import uuid

from .core import digest
from .layout import LEGACY_WIDTH,LEGACY_HEIGHT
from .mec import Mec
from .recordings import route


def geo_to_grid(latitude, longitude, bounds, width=LEGACY_WIDTH, height=LEGACY_HEIGHT):
    vals=[latitude,longitude,*[bounds[k] for k in ('south','north','west','east')]]
    if any(type(v) not in (float,int) or not math.isfinite(v) for v in vals):
        raise ValueError('Coordinates and bounds must be finite numbers')
    south,north,west,east=vals[2:]
    if not south<north or not west<east or not south<=latitude<=north or not west<=longitude<=east:
        raise ValueError('Coordinate outside the configured demo map')
    return [min(width-1,int((longitude-west)/(east-west)*width)),
            min(height-1,int((north-latitude)/(north-south)*height))]


def mapped_locations(config, payload=None):
    settings=config['mec_recorded_mapping']
    if payload is None:
        mec=Mec(config['mec'],True)
        try:payload=mec.locations()
        finally:mec.session.close()
    by_address={u['address']:u for u in payload.get('userList',{}).get('user',[])}
    records=[]
    for drone,address in settings['ue_by_drone'].items():
        record={'drone_id':drone,'ue_address':address,'mode':'demo_map_projection',
                'used_for_dispatch':False,'calibrated_physical_position':False}
        try:
            user=by_address[address];info=user['locationInfo']
            if info.get('shape')!=2 or len(info['latitude'])!=1 or len(info['longitude'])!=1:
                raise ValueError('Expected an unambiguous single-point location')
            record.update(grid=geo_to_grid(info['latitude'][0],info['longitude'][0],settings['bounds'],settings.get('grid_width',LEGACY_WIDTH),settings.get('grid_height',LEGACY_HEIGHT)),
                          timeStamp=user.get('timeStamp'),sandbox_zone=user.get('zoneId'),ok=True)
        except (ValueError,KeyError,TypeError,IndexError) as exc:
            record.update(ok=False,error=str(exc))
        records.append(record)
    return {'mapping':settings,'records':records,'raw':payload,
            'note':'Network context only. Grid walls, historical positions and CSE routing are not overwritten.'}


class RobotTasks:
    def __init__(self, store):
        self.store=store
        self.tasks=store.get('local-robot-tasks',[])
        self.invalidate('Executor restarted')

    def save(self):self.store.set('local-robot-tasks',self.tasks[-100:])

    def invalidate(self,reason):
        for t in self.tasks:
            if t['status'] not in ('completed','failed','cancelled'):
                t.update(status='cancelled',reason=reason)
        self.save()

    def offer(self, suggestion, robot_id):
        if robot_id!='robot-01':raise ValueError('Only the local demo robot-01 is configured')
        task={'task_id':uuid.uuid4().hex,'command_id':uuid.uuid4().hex,'robot_id':robot_id,
              'suggestion_id':suggestion['task_id'],'goal':suggestion['goal'],
              'status':'queued','sequence':-1,'executor':'local_robot_simulation',
              'hardware_dispatched':False}
        self.tasks.append(task);self.save();return task

    def feedback(self,body):
        if not isinstance(body,dict):raise ValueError("Feedback must be an object")
        task=next((t for t in self.tasks if t['task_id']==body.get('task_id')),None)
        if not task or any(body.get(k)!=task[k] for k in ('command_id','robot_id','executor')):
            raise ValueError('Feedback identity does not match the task')
        sequence=body.get('sequence')
        if type(sequence) is not int or sequence<0:raise ValueError('Invalid sequence')
        stamp=digest(body)
        if sequence==task['sequence'] and stamp==task.get('feedback_digest'):return copy.deepcopy(task)
        if sequence<=task['sequence']:raise ValueError('Old or conflicting feedback')
        allowed={'queued':{'accepted','failed'},'accepted':{'moving','completed','failed'},'moving':{'moving','completed','failed'}}
        if body.get('status') not in allowed.get(task['status'],set()):raise ValueError('Invalid task transition')
        if body['status']=='completed' and body.get('position')!=task['goal']:
            raise ValueError('Completion needs the assigned goal position')
        task.update(status=body['status'],sequence=sequence,feedback_digest=stamp,feedback=copy.deepcopy(body))
        self.save();return copy.deepcopy(task)

    def simulate(self,suggestion,frame,grid):
        blocked={(x,y) for h in frame['hazards'] for x in range(grid['width']) for y in range(grid['height'])
                 if abs(x-h['x'])+abs(y-h['y'])<=h['radius']}
        path=route(grid,(1,1),tuple(suggestion['goal']),blocked)
        if not path:raise ValueError('Local robot cannot reach the target through known obstacles')
        task=self.offer(suggestion,'robot-01')
        for seq,status in enumerate(('accepted','moving','completed')):
            self.feedback({**{k:task[k] for k in ('task_id','command_id','robot_id','executor')},
                           'sequence':seq,'status':status,'position':list(path[-1] if status=='completed' else path[0])})
        task['route']=list(map(list,path));self.save();return copy.deepcopy(task)
