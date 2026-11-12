"""Deterministic frontier bidding and collision-free one-step reservations."""
import copy
import heapq

WIDTH, HEIGHT, RADIUS = 50, 30, 2

def walls():
    return {(25,y) for y in range(30) if y not in (4,5,20,21)} | {(x,6) for x in range(2,8)} | {(x,18) for x in range(32,38)}

def neighbors(p):
    x,y=p
    for q in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
        if 0<=q[0]<WIDTH and 0<=q[1]<HEIGHT:yield q

class Coordinator:
    def __init__(self):self.goals={};self.signature=None;self.cached=None

    def plan(self,positions,seen,hazards):
        signature=(tuple(sorted(positions.items())),frozenset(seen),tuple(sorted(hazards.items())))
        if signature==self.signature:return copy.deepcopy(self.cached)
        blocked=walls()|{p for p,h in hazards.items() if h>0.6}
        gain={}
        for x in range(WIDTH):
            for y in range(HEIGHT):
                if (x,y) in blocked:continue
                gain[x,y]=sum((a,b) not in seen and (a,b) not in walls_cached
                    for a in range(max(0,x-RADIUS),min(WIDTH,x+RADIUS+1))
                    for b in range(max(0,y-RADIUS),min(HEIGHT,y+RADIUS+1)))
        bids=[];trees={}
        for drone,start in sorted(positions.items()):
            other={p for d,p in positions.items() if d!=drone}
            distance={start:0};previous={};queue=[(0,start)]
            while queue:
                cost,p=heapq.heappop(queue)
                if cost!=distance[p]:continue
                for q in neighbors(p):
                    if q in blocked or q in other:continue
                    new=cost+1+4*hazards.get(q,0)
                    if new<distance.get(q,float('inf')):
                        distance[q]=new;previous[q]=p;heapq.heappush(queue,(new,q))
            trees[drone]=(distance,previous)
            for target,cost in distance.items():
                benefit=gain.get(target,0)
                if not benefit:continue
                # Prefer nearby information gain; retain a useful goal to avoid oscillation.
                score=cost/(benefit**0.5)-(0.75 if self.goals.get(drone)==target else 0)
                bids.append((score,drone,target,benefit,cost))
        assigned={};taken=[]
        for score,drone,target,benefit,cost in sorted(bids):
            if drone in assigned or any(max(abs(target[0]-p[0]),abs(target[1]-p[1]))<=RADIUS for p in taken):continue
            assigned[drone]=dict(goal=list(target),bid=round(score,5),travel_cost=round(cost,5),information_gain=benefit)
            taken.append(target)
        # Reserve next cells; disallow moving into another drone's current cell (also prevents swaps).
        actions={};reserved=set()
        for drone,start in sorted(positions.items()):
            assignment=assigned.get(drone)
            path=[start]
            if assignment:
                goal=tuple(assignment['goal']);path=[goal];previous=trees[drone][1]
                while path[-1]!=start:path.append(previous[path[-1]])
                path.reverse()
            nxt=path[1] if len(path)>1 else start
            if nxt in reserved or nxt in {p for d,p in positions.items() if d!=drone}:nxt=start
            reserved.add(nxt)
            actions[drone]=dict(position=list(nxt),status='exploring' if assignment else 'no_reachable_frontier',
                                path=[list(p) for p in path],assignment=assignment)
        self.goals={d:tuple(a['goal']) for d,a in assigned.items()}
        result = {'policy':'frontier_auction_v1','actions':actions,'assignments':assigned,
                'bids_evaluated':len(bids),'collision_policy':'unique_next_cell_no_swaps',
                'unknown_cells':WIDTH*HEIGHT-len(walls_cached)-len(set(seen)-walls_cached)}
        self.signature=signature;self.cached=copy.deepcopy(result)
        return result

walls_cached=walls()
