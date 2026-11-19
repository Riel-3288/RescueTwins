"""Shared geometry. Live demos and legacy recordings declare separate map specs."""
from dataclasses import dataclass
import math
import re

LIVE_WIDTH, LIVE_HEIGHT, LIVE_DRONES = 80, 40, 8
LEGACY_WIDTH, LEGACY_HEIGHT = 50, 30
CELL_M = 2

def legacy_walls():
    zone_x=LEGACY_WIDTH//2
    return {(zone_x,y) for y in range(LEGACY_HEIGHT) if y not in (4,5,20,21)} | {(x,6) for x in range(2,8)} | {(x,18) for x in range(32,38)}

@dataclass(frozen=True)
class MapSpec:
    width:int
    height:int
    zone_x:int
    drone_ids:tuple
    walls:frozenset
    cell_m:float=CELL_M
    radius:int=2

    def inside(self,x,y):return type(x) is int and type(y) is int and 0<=x<self.width and 0<=y<self.height
    def zone(self,x):return 'A' if x<self.zone_x else 'B'
    def payload(self):return dict(width=self.width,height=self.height,zone_x=self.zone_x,drone_ids=list(self.drone_ids),walls=sorted(map(list,self.walls)),cell_m=self.cell_m,radius=self.radius)

    @classmethod
    def from_dict(cls,d):
        if not isinstance(d,dict):raise ValueError('map must be an object')
        w,h=d.get('width'),d.get('height');z=d.get('zone_x',w//2 if type(w) is int else None)
        if any(type(v) is not int for v in (w,h,z)) or not (10<=w<=160 and 10<=h<=100 and 1<=z<w):raise ValueError('Invalid map dimensions/zone boundary')
        ids=d.get('drone_ids',[])
        if not isinstance(ids,list) or not 1<=len(ids)<=16 or len(set(ids))!=len(ids) or any(not isinstance(v,str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',v) for v in ids):raise ValueError('Invalid drone registry')
        obstacles=d.get('walls',[])
        if not isinstance(obstacles,list) or len(obstacles)>w*h:raise ValueError('Invalid walls')
        if any(not isinstance(p,(list,tuple)) or len(p)!=2 or any(type(v) is not int for v in p) or not(0<=p[0]<w and 0<=p[1]<h) for p in obstacles):raise ValueError('Wall outside map')
        cell=d.get('cell_m',CELL_M);radius=d.get('radius',2)
        if type(cell) not in (int,float) or not math.isfinite(cell) or not 0<cell<=100 or type(radius) is not int or not 1<=radius<=5:raise ValueError('Invalid cell size or sensing radius')
        return cls(w,h,z,tuple(ids),frozenset(map(tuple,obstacles)),cell,radius)

def legacy_spec():return MapSpec(LEGACY_WIDTH,LEGACY_HEIGHT,LEGACY_WIDTH//2,('drone-A','drone-B'),frozenset(legacy_walls()))

def live_spec(settings=None):
    c=dict(settings or {});w=c.get('width',LIVE_WIDTH);h=c.get('height',LIVE_HEIGHT);n=c.get('drone_count',LIVE_DRONES)
    if type(n) is not int or not 2<=n<=16:raise ValueError('drone_count must be 2..16')
    z=c.get('zone_x',w//2)
    obstacles={(15,y) for y in range(3,min(h-2,27)) if y not in (8,9,10,20,21,22)}
    obstacles|={(34,y) for y in range(3,min(h-2,27)) if y not in (12,13,14,15,24,25)}
    obstacles|={(x,y) for x in range(21,min(29,w)) for y in (5,6,24,25) if y<h}
    return MapSpec.from_dict(dict(width=w,height=h,zone_x=z,drone_ids=[f'agent-{i:02}' for i in range(1,n+1)],
        walls=c.get('walls',[list(p) for p in obstacles if p[0]<w]),cell_m=c.get('cell_m',CELL_M),radius=c.get('radius',2)))
