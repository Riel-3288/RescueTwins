"""Frontier auction, spatial repulsion and synchronous collision reservations."""
import copy
import heapq
try:
    from .layout import legacy_spec,LEGACY_WIDTH,LEGACY_HEIGHT
except ImportError:
    from layout import legacy_spec,LEGACY_WIDTH,LEGACY_HEIGHT

WIDTH,HEIGHT,RADIUS=LEGACY_WIDTH,LEGACY_HEIGHT,2
def walls():return set(legacy_spec().walls)
walls_cached=walls()
def neighbors(p,width=WIDTH,height=HEIGHT):
    x,y=p
    for q in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
        if 0<=q[0]<width and 0<=q[1]<height:yield q

class Coordinator:
    def __init__(self,spec=None):self.spec=spec or legacy_spec();self.goals={};self.signature=None;self.cached=None
    def plan(self,positions,seen,hazards,targets=None):
        targets=targets or {};spec=self.spec;obstacles=set(spec.walls)
        signature=(tuple(sorted(positions.items())),frozenset(seen),tuple(sorted(hazards.items())),tuple(sorted(targets.items())))
        if signature==self.signature:return copy.deepcopy(self.cached)
        blocked=obstacles|{p for p,h in hazards.items() if h>0.6};r=spec.radius
        gain={}
        for x in range(spec.width):
            for y in range(spec.height):
                if (x,y) in blocked:continue
                gain[x,y]=sum((a,b) not in seen and (a,b) not in obstacles for a in range(max(0,x-r),min(spec.width,x+r+1)) for b in range(max(0,y-r),min(spec.height,y+r+1)))
        # Bid on reachable goals using travel cost and exploration gain.
        trees={};bids=[];assigned={};taken=[]
        for drone,start in sorted(positions.items()):
            others={p for d,p in positions.items() if d!=drone};dist={start:0};prev={};queue=[(0,start)]
            while queue:
                cost,p=heapq.heappop(queue)
                if cost!=dist[p]:continue
                for q in neighbors(p,spec.width,spec.height):
                    if q in blocked or q in others:continue
                    repel=sum(.6/(1+abs(q[0]-a)+abs(q[1]-b)) for a,b in others)
                    new=cost+1+4*hazards.get(q,0)+repel
                    if new<dist.get(q,float('inf')):dist[q]=new;prev[q]=p;heapq.heappush(queue,(new,q))
            trees[drone]=(dist,prev)
            if drone in targets:
                target=targets[drone]
                if target in dist and target not in blocked:
                    assigned[drone]=dict(goal=list(target),bid=0,travel_cost=dist[target],information_gain=gain.get(target,0),mode='manual');taken.append(target)
                continue
            for target,cost in dist.items():
                benefit=gain.get(target,0)
                if benefit:bids.append((cost/benefit**.5-(.75 if self.goals.get(drone)==target else 0),drone,target,benefit,cost))
        # Separate goals to reduce overlapping search areas.
        for score,drone,target,benefit,cost in sorted(bids):
            if drone in assigned or any(max(abs(target[0]-p[0]),abs(target[1]-p[1]))<=r for p in taken):continue
            assigned[drone]=dict(goal=list(target),bid=round(score,5),travel_cost=round(cost,5),information_gain=benefit,mode='explore');taken.append(target)
        # Reserve next cells and reject moves into occupied cells.
        actions={};reserved=set()
        for drone,start in sorted(positions.items()):
            task=assigned.get(drone);path=[start]
            if task:
                path=[tuple(task['goal'])]
                while path[-1]!=start:path.append(trees[drone][1][path[-1]])
                path.reverse()
            nxt=path[1] if len(path)>1 else start
            if nxt in reserved or nxt in {p for d,p in positions.items() if d!=drone}:nxt=start
            reserved.add(nxt)
            actions[drone]=dict(position=list(nxt),status='exploring' if task else 'no_reachable_frontier',path=list(map(list,path)),assignment=task)
        self.goals={d:tuple(t['goal']) for d,t in assigned.items()}
        result=dict(policy='frontier_auction_v1',actions=actions,assignments=assigned,bids_evaluated=len(bids),
            collision_policy='unique_next_cell_no_swaps',unknown_cells=spec.width*spec.height-len(obstacles)-len(set(seen)-obstacles))
        self.signature=signature;self.cached=copy.deepcopy(result);return result
