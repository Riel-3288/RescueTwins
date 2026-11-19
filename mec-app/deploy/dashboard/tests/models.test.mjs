import test from 'node:test';
import assert from 'node:assert/strict';
import { MessageBuffer, agentMetrics, freshness, matchesReading, readingsCsv, worldToGrid } from '../src/models.js';

const now=Date.parse('2026-10-16T08:00:00Z');
const reading=(extra={})=>({device_id:'node-01',zone_id:'A',sensor_type:'temperature',value:28.6,unit:'degC',source:'simulation',quality:'valid',observed_at:'2026-10-16T07:59:58Z',...extra});
const message=(extra={},received='2026-10-16T07:59:59Z')=>({topic:'rescuetwin/A/node-01/temperature',reading:reading(extra),received_at:received});

test('freshness is evaluated per reading, including future and missing timestamps',()=>{
  assert.equal(freshness(reading(),now),'fresh');
  assert.equal(freshness(reading({observed_at:'2026-10-16T07:59:49Z'}),now),'stale');
  assert.equal(freshness(reading({observed_at:'2026-10-16T08:00:06Z'}),now),'future');
  assert.equal(freshness(reading({observed_at:''}),now),'unknown');
});

test('combined source, zone, sensor, quality and search filters keep physical data distinct',()=>{
  const physical=reading({source:'physical'}), simulated=reading();
  const filters={source:'physical',zone:'A',sensor:'temperature',quality:'valid',freshness:'fresh',search:'NODE-01/temperature'};
  assert.equal(matchesReading(physical,filters,now),true);
  assert.equal(matchesReading(simulated,filters,now),false);
  assert.equal(matchesReading(reading({source:'physical',quality:'invalid',value:null}),filters,now),false);
  assert.equal(matchesReading(physical,{zone:'B'},now),false);
});

test('overlapping API snapshots deduplicate without hiding separate broker deliveries',()=>{
  const buffer=new MessageBuffer(3), first=message(), second=message({},'2026-10-16T08:00:00Z');
  buffer.ingest([first]);buffer.ingest([second,first]);buffer.ingest([second]);
  assert.equal(buffer.entries.length,2);
  assert.equal(buffer.entries[0].received_at,second.received_at);
  buffer.ingest([message({value:31},'2026-10-16T08:00:01Z'),message({value:32},'2026-10-16T08:00:02Z')]);
  assert.equal(buffer.entries.length,3);
  assert.equal(buffer.entries[0].reading.value,32);
});

test('paused message log holds its buffer and can resume',()=>{
  const buffer=new MessageBuffer();buffer.ingest([message()]);buffer.paused=true;
  buffer.ingest([message({value:80},'2026-10-16T08:00:01Z')]);assert.equal(buffer.entries.length,1);
  buffer.paused=false;buffer.ingest([message({value:80},'2026-10-16T08:00:01Z')]);assert.equal(buffer.entries.length,2);
  assert.equal(buffer.filtered({search:'80'},now).length,1);
});

test('CSV preserves false, zero and invalid-null readings',()=>{
  const csv=readingsCsv([reading({sensor_type:'presence',unit:'boolean',value:false}),reading({value:0}),reading({quality:'invalid',value:null})]);
  assert.match(csv,/"false"/);assert.match(csv,/"0"/);assert.match(csv,/"","degC","simulation","invalid"/);
  assert.equal(csv.split('\r\n').length,4);
});

test('goal coordinates snap to valid cells and reject edges, infinities and negative input',()=>{
  const state={width:50,height:30,cell_m:2};
  assert.deepEqual(worldToGrid(99.9,59.9,state),{x:49,y:29});
  assert.deepEqual(worldToGrid(21,17,state),{x:10,y:8});
  for(const [x,y]of [[100,0],[0,60],[-.1,0],[Infinity,0],[NaN,0]])assert.throws(()=>worldToGrid(x,y,state));
});

test('agent metrics use world-space centres and route segments rather than travelled distance',()=>{
  const state={width:3,cell_m:2,heat:[28,28,28,28,75,28],smoke:[0,0,0,0,.7,0]};
  const agent={x:1,y:1,goal:[2,1],path:[[1,1],[2,1]],travel_m:100};
  const metrics=agentMetrics(agent,state);
  assert.deepEqual([metrics.x,metrics.y,metrics.goalX,metrics.goalY],[3,3,5,3]);
  assert.equal(metrics.remaining,2);assert.equal(metrics.exposed,true);
  assert.equal(agentMetrics({...agent,path:[]},state).remaining,null);
});
