import { FactoryScene } from './scene3d.js';
import { canvasContext, drawFactory2d } from './flatmap.js';
import { MessageBuffer, agentMetrics, escapeHtml as esc, eventMessage, freshness, matchesReading, readingsCsv, worldToGrid } from './models.js';

const $=id=>document.getElementById(id);
const pages={overview:'Factory Digital Twin',telemetry:'Sensors & Messages',agents:'Agent Status',recorded:'Recorded Mission'};
const statusLabels={ready:'Ready',moving:'Moving',blocked:'No safe route',arrived:'Arrived'};
const filterIds={device:'filterDevice',zone:'filterZone',source:'filterSource',sensor:'filterSensor',quality:'filterQuality',freshness:'filterFreshness',search:'filterSearch'};
const buffer=new MessageBuffer(500);
let state=null,page='overview',mapMode='3d',layer='heat',selectedAgent='agent-01',scene=null,sceneUnavailable=false;
let requestActive=false,serviceOnline=false,lastReceivedCount=0,selectedPayload=null,filteredReadings=[],visibleMessages=[];
let goalDirty=false,commandActive=false,toastTimer,devicesKey='';

function toast(text){$('toast').textContent=text;$('toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('show'),3500);}
function badge(id,text,good){$(id).textContent=text;$(id).className=`pill ${good?'good':'bad'}`;}
function timeText(value){return value?String(value).replace('T',' ').replace(/\.\d+Z$/,'Z'):'—';}
function topicFor(reading){return `rescuetwin/${reading.zone_id}/${reading.device_id}/${reading.sensor_type}`;}
function getFilters(){return Object.fromEntries(Object.entries(filterIds).map(([key,id])=>[key,$(id).value]));}
function emptyRow(columns,message){return `<tr><td colspan="${columns}" class="empty-cell">${esc(message)}</td></tr>`;}
function statusPill(status){return `<span class="pill ${status==='blocked'?'bad':status==='arrived'?'good':'subtle'}">${esc(statusLabels[status]||status)}</span>`;}

async function command(action,extra={}){
  if(commandActive)return false;
  if(!serviceOnline){toast('The service is offline. Check the startup terminal.');return false;}
  commandActive=true;
  try{
    const response=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,...extra}),signal:AbortSignal.timeout(8000)});
    const value=await response.json();if(!response.ok)throw new Error(value.error||'Action failed');
    await poll();return true;
  }catch(error){toast(error.message);return false;}finally{commandActive=false;}
}

function routePage(){
  const requested=location.hash.slice(1)||'overview';page=Object.hasOwn(pages,requested)?requested:'overview';
  for(const key of Object.keys(pages))$('page-'+key).hidden=key!==page;
  for(const link of document.querySelectorAll('[data-page]')){const active=link.dataset.page===page;link.classList.toggle('active',active);if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');}
  $('pageCrumb').textContent=pages[page];document.title=`RescueTwins · ${pages[page]}`;
  if(scene)scene.active=page==='overview'&&mapMode==='3d';
  window.scrollTo({top:0,behavior:'auto'});render();
}

function createScene(){
  if(scene||sceneUnavailable||!state)return;
  try{scene=new FactoryScene($('factory3d'),{onCell:editCell,onAgent:id=>{selectAgent(id);toast(`Selected ${id}. Open Agent Status for route details.`);},onError:message=>{sceneUnavailable=true;$('sceneError').hidden=false;toast(message);}});scene.update(state,layer,selectedAgent);}
  catch(error){sceneUnavailable=true;$('sceneError').hidden=false;$('sceneSelection').textContent='3D graphics unavailable';console.warn('3D view unavailable:',error.message);}
}

function setMapMode(mode){
  mapMode=mode==='2d'?'2d':'3d';
  $('factory3d').hidden=mapMode!=='3d';$('factoryMap').hidden=mapMode!=='2d';
  $('sceneBadge').textContent=mapMode==='3d'?'3D DIGITAL TWIN':'2D FACTORY GRID';
  $('sceneGuide').textContent=mapMode==='3d'?'Left drag: rotate | Middle drag: pan | Wheel: zoom':'Click a cell to edit · Each cell is 2 × 2 m';
  $('resetCamera').hidden=mapMode!=='3d';$('sceneError').hidden=mapMode!=='3d'||!sceneUnavailable;
  for(const button of document.querySelectorAll('[data-mode]')){button.classList.toggle('selected',button.dataset.mode===mapMode);button.setAttribute('aria-pressed',button.dataset.mode===mapMode?'true':'false');}
  if(mapMode==='3d')createScene();
  if(scene)scene.active=page==='overview'&&mapMode==='3d';
  renderMap();
}

function renderMap(){
  if(!state||page!=='overview')return;
  if(mapMode==='3d'){createScene();scene?.update(state,layer,selectedAgent);}else drawFactory2d($('factoryMap'),state,{layer,selected:selectedAgent});
}

async function editCell(x,y){
  if(!state)return;
  const index=y*state.width+x;
  $('sceneSelection').textContent=`Cell (${x}, ${y}) · ${state.heat[index].toFixed(1)}°C · smoke ${(state.smoke[index]*100).toFixed(0)}%`;
  const action=$('tool').value;if(action==='inspect')return;
  const okay=await command(action,{x,y,intensity:Number($('intensity').value),agent_id:$('agentSelect').value});
  if(okay)toast(action==='goal'?`Goal updated for ${$('agentSelect').value}.` : action==='obstacle'?'Obstacle layout updated.':'Fire placed. Resume the simulation to evolve the hazard.');
}

function drawTrend(){
  const context=canvasContext($('trend'));if(!context||!state)return;
  const {ctx,w,h}=context,left=40,right=w-15,top=8,bottom=h-28,history=state.history,maxT=Math.max(100,...history.map(p=>p.temperature));
  ctx.font='9px Segoe UI';ctx.textAlign='right';
  for(let i=0;i<=3;i++){const y=top+(bottom-top)*i/3;ctx.strokeStyle='#edf0f4';ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();ctx.fillStyle='#a6afba';ctx.fillText(Math.round(maxT*(1-i/3)),left-8,y+3);}
  if(history.length<2){ctx.textAlign='center';ctx.fillStyle='#a2adb9';ctx.fillText('Run the simulation to view trends',w/2,h/2);return;}
  for(const [key,max,color]of [['temperature',maxT,'#e58b4d'],['smoke',100,'#6293bc']]){ctx.beginPath();ctx.strokeStyle=color;ctx.lineWidth=2;history.forEach((p,i)=>{const x=left+(right-left)*i/(history.length-1),y=bottom-(bottom-top)*p[key]/max;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();}
  ctx.fillStyle='#a6afba';ctx.textAlign='left';ctx.fillText(`${history[0].t}s`,left,h-8);ctx.textAlign='right';ctx.fillText(`${history.at(-1).t}s`,right,h-8);
}

function renderOverview(){
  renderRescue();
  $('fireCount').textContent=state.metrics.fire_cells;$('hazardCount').textContent=`Hazard cells: ${state.metrics.hazard_cells}`;$('peakTemp').textContent=state.metrics.peak_temperature.toFixed(1);
  $('mqttCount').textContent=state.mqtt.received.toLocaleString();$('mqttSummary').textContent=serviceOnline?`PUBACK ${state.mqtt.puback} · Rejected ${state.mqtt.rejected}`:'Page updates have stopped';
  $('storedCount').textContent=state.cse.enabled?(state.cse.counts.delivered||0).toLocaleString():'—';
  $('cseSummary').textContent=state.cse.enabled?`Pending ${state.cse.counts.pending||0} · Quarantined ${state.cse.counts.quarantined||0}`:'CSE bridge not enabled';
  badge('brokerBadge',!serviceOnline?'Unknown':state.mqtt.connected?'Connected':'Disconnected',serviceOnline&&state.mqtt.connected);
  badge('bridgeBadge',!serviceOnline?'Unknown':state.cse.enabled?'Enabled':'Disabled',serviceOnline&&state.cse.enabled);
  $('brokerAddress').textContent=`${state.mqtt.host}:${state.mqtt.port}`;
  $('deliveryDetail').textContent=state.cse.enabled?`Cumulative stored: ${(state.cse.counts.delivered||0).toLocaleString()}. Two zone workers replay telemetry; rescue events receive priority. Simulator sends changes and heartbeats.`:'MQTT is available independently. Use --with-cse to verify storage.';
  const seconds=Math.floor(state.sim_seconds);$('simTime').textContent=`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')} / ${state.speed}×`;
  $('runState').textContent=state.paused?'● Paused · Telemetry live':'● Simulation running';$('toggle').textContent=state.paused?'▶ Resume':'Ⅱ Pause';$('speed').value=state.speed;
  $('crossings').textContent=`${state.metrics.boundary_events} zone crossings`;
  $('events').innerHTML=state.events.slice(0,15).map(e=>`<div class="event"><i class="event-bullet"></i><span>${esc(eventMessage(e))}</span><time>${Math.floor(e.sim_seconds)}s</time></div>`).join('');
  renderMap();drawTrend();
}

function renderRescue(){
  const rescue=state.rescue;
  if(!rescue){$('rescueSummary').textContent='Restart the Python service to enable rescue operations and delivery diagnostics.';return;}
  const active=rescue.incidents.filter(i=>i.status!=='resolved'),tasks=rescue.tasks;
  $('rescueMode').textContent=rescue.enabled?'Local simulation executor':'Rescue automation disabled';
  $('rescueSummary').textContent=`${active.length} active incidents | ${tasks.filter(t=>['dispatched','accepted','moving'].includes(t.status)).length} active tasks | ${tasks.filter(t=>t.status==='completed').length} inspections completed | ${tasks.filter(t=>['blocked','failed'].includes(t.status)).length} blocked / failed`;
  $('incidentRows').innerHTML=[...rescue.incidents].reverse().slice(0,25).map(i=>{
    const task=[...tasks].reverse().find(t=>t.incident_id===i.incident_id),age=(Date.now()-Date.parse(i.last_observed_at))/1000;
    return `<tr><td>${esc(i.device_id)}<small>${esc(i.incident_id.slice(0,8))} · Zone ${esc(i.zone_id)}</small></td><td><span class="source-tag ${i.source==='physical'?'physical':''}">${esc(i.source)}</span><small>${esc(i.trigger.sensor_type)}: ${esc(i.trigger.value)} ${esc(i.trigger.unit)}</small></td><td><span class="pill ${i.status==='resolved'?'good':'bad'}">${esc(i.status)}</span><small>${age>35&&i.status!=='resolved'?'Sensor data stale; hazard retained':i.acknowledged?'Acknowledged':'Awaiting acknowledgment'}</small></td><td>${task?`${esc(task.agent_id)} · ${esc(task.status)}<small>${esc(task.operation)} at (${(task.goal[0]+.5)*2}, ${(task.goal[1]+.5)*2}) m${task.observation_to_completion_ms!=null?` · ${(task.observation_to_completion_ms/1000).toFixed(1)}s`:''}</small>`:esc(i.dispatch_state==='no_safe_route'?'No safe staging route':'Waiting for dispatch')}</td><td>${!i.acknowledged?`<button class="row-action" data-incident-ack="${esc(i.incident_id)}">Acknowledge</button>`:''}${i.status!=='resolved'?`<button class="row-action" data-incident-retry="${esc(i.incident_id)}">Dispatch again</button>`:''}</td></tr>`;
  }).join('')||emptyRow(5,'No sensor alerts. A fresh valid temperature reading of 60 degC or above opens an incident.');
  $('rescueTimeline').innerHTML=[...rescue.events].reverse().slice(0,12).map(e=>`<div class="event"><i class="event-bullet"></i><span><strong>${esc(e.kind.replaceAll('_',' '))}</strong> · ${esc(e.message||'')}<small>${esc(e.agent_id||e.device_id)} · ${esc(e.incident_id.slice(0,8))}${e.command_id?' · command '+esc(e.command_id.slice(0,8)):''}</small></span><time>${esc(timeText(e.observed_at))}</time></div>`).join('');
  const c=state.cse,pending=(c.counts.pending||0)+(c.counts.processing||0),net=(c.stored_per_s||0)-(c.input_per_s||0);
  const values=[['Waiting / in flight',pending.toLocaleString()],['Oldest waiting',`${Math.round(c.oldest_pending_s||0)} s`],['MQTT ingestion',`${c.input_per_s??'-'} records/s`],['CSE delivery',`${c.stored_per_s??'-'} records/s`],['Net drain (10s window)',`${net.toFixed(1)} records/s`],['Repeated simulator readings suppressed',(state.mqtt.simulator_suppressed||0).toLocaleString()]];
  $('deliveryMetrics').innerHTML=values.map(([k,v])=>`<div><small>${esc(k)}</small><strong>${esc(v)}</strong></div>`).join('');
  $('deliveryError').textContent=!c.enabled?'Bridge disabled. Start with CSE support to verify storage.':c.last_error||'No recorded CSE delivery error. Positive net drain means the queue is shrinking. Metrics refresh every 2 seconds; rates use a 10-second window.';
}

function inspectPayload(message){
  selectedPayload=message;
  $('payloadTopic').textContent=message.topic;
  $('payloadMeta').textContent=message.received_at?`Received: ${timeText(message.received_at)}`:`Observed: ${timeText(message.reading.observed_at)}`;
  $('payloadJson').textContent=JSON.stringify(message.reading,null,2);$('copyPayload').disabled=false;
}

function renderTelemetry(){
  const now=Date.now(),readings=state.readings,filters=getFilters();
  const devices=[...new Set(readings.map(r=>r.device_id))].sort(),key=devices.join(',');
  if(key!==devicesKey){const previous=$('filterDevice').value;$('filterDevice').innerHTML='<option value="">All devices</option>'+devices.map(d=>`<option>${esc(d)}</option>`).join('');$('filterDevice').value=devices.includes(previous)?previous:'';devicesKey=key;}
  $('deviceCount').textContent=devices.length;$('readingCount').textContent=readings.length;
  $('unhealthyCount').textContent=readings.filter(r=>r.quality!=='valid'||freshness(r,now)!=='fresh').length;
  $('sensorBroker').textContent=!serviceOnline?'Unknown':state.mqtt.connected?'Connected':'Offline';
  $('sensorBroker').classList.add('status-word');$('lastReceived').textContent=`Last received: ${timeText(state.mqtt.last_received)}`;
  filteredReadings=readings.filter(r=>matchesReading(r,filters,now)).sort((a,b)=>(a.device_id+a.source+a.sensor_type).localeCompare(b.device_id+b.source+b.sensor_type));
  $('filterCount').textContent=`${filteredReadings.length} of ${readings.length} latest readings`;
  $('sensorRows').innerHTML=filteredReadings.map((r,i)=>{
    const age=freshness(r,now),labels={fresh:'Fresh',stale:'Stale',future:'Future timestamp',unknown:'Unknown'};
    const value=r.quality==='invalid'?'—':typeof r.value==='boolean'?String(r.value):r.value;
    return `<tr><td>${esc(r.device_id)}<small>Zone ${esc(r.zone_id)}</small></td><td>${esc(r.sensor_type)}</td><td class="reading-value">${esc(value)} <small>${esc(r.unit)}</small></td><td><span class="source-tag ${r.source==='physical'?'physical':''}">${esc(r.source)}</span></td><td><span class="pill ${r.quality==='valid'?'good':'bad'}">${esc(r.quality)}</span></td><td><span class="pill ${age==='fresh'?'subtle':'bad'}">${labels[age]}</span></td><td><time>${esc(timeText(r.observed_at))}</time></td><td><button class="row-action" data-reading="${i}" aria-label="Inspect ${esc(r.device_id)} ${esc(r.sensor_type)} ${esc(r.source)}">Inspect ↗</button></td></tr>`;
  }).join('')||emptyRow(8,'No readings match these filters. Try clearing filters or check the MQTT connection.');
  visibleMessages=buffer.filtered(filters,now).slice(0,100);
  $('bufferSummary').textContent=`${visibleMessages.length} matching messages · ${buffer.entries.length}/500 buffered in this browser${buffer.paused?' · Log paused':''}`;
  $('messages').innerHTML=visibleMessages.map((m,i)=>`<button class="message message-button" data-message="${i}"><code>${esc(m.topic)}</code><span><time>${esc(timeText(m.received_at))}</time><strong>${esc(m.reading.value)} ${esc(m.reading.unit)}</strong><span class="message-source">${esc(m.reading.source)} · ${esc(m.reading.quality)}</span></span></button>`).join('')||'<p class="empty">No buffered messages match. Latest readings remain available above.</p>';
  $('pauseLog').textContent=buffer.paused?'Resume Log':'Pause Log';
}

function selectAgent(id){
  if(!state?.agents.some(a=>a.id===id))return;
  selectedAgent=id;goalDirty=false;$('agentSelect').value=id;$('goalFeedback').textContent='';
  if(page==='agents')renderAgents();if(page==='overview')renderMap();
}

function renderAgents(){
  const agents=state.agents,zone=$('agentZoneFilter').value,status=$('agentStatusFilter').value;
  $('agentTotal').textContent=agents.length;$('agentMoving').textContent=agents.filter(a=>a.status==='moving').length;$('agentBlocked').textContent=agents.filter(a=>a.status==='blocked').length;$('agentArrived').textContent=agents.filter(a=>a.status==='arrived').length;
  $('agentRows').innerHTML=agents.filter(a=>(!zone||a.zone===zone)&&(!status||a.status===status)).map(a=>{
    const m=agentMetrics(a,state);
    return `<tr class="${a.id===selectedAgent?'selected-row':''}"><td>${esc(a.id)}</td><td><span class="pill subtle">${esc(a.zone)}</span></td><td>${statusPill(a.status)}</td><td>(${m.x}, ${m.y})</td><td>(${m.goalX}, ${m.goalY})</td><td>${a.travel_m} m</td><td>${a.replans}</td><td><button class="row-action" data-agent="${esc(a.id)}" aria-label="Select ${esc(a.id)}">${a.id===selectedAgent?'Selected':'Select →'}</button></td></tr>`;
  }).join('')||emptyRow(8,'No agents match this zone and status.');
  const agent=agents.find(a=>a.id===selectedAgent)||agents[0];if(!agent)return;
  selectedAgent=agent.id;const m=agentMetrics(agent,state),last=agent.path.at(-1),validPath=last&&last[0]===agent.goal[0]&&last[1]===agent.goal[1];
  $('agentDetailTitle').textContent=`${agent.id} · Route Detail`;$('goalAgentLabel').textContent=`Selected agent: ${agent.id}${state.paused?' · Simulation paused':''}`;
  const metrics=[['Confirmed Zone',agent.zone],['Status',`${statusLabels[agent.status]}${state.paused?' · Paused':''}`],['Remaining Route',validPath?`${m.remaining} m`:'Not planned yet'],['Local Temperature',`${m.temperature.toFixed(1)} °C`],['Local Smoke Index',`${(m.smoke*100).toFixed(0)}%`],['Cell Exposure',m.exposed?'Hazardous':'Below demo thresholds']];
  $('agentDetailMetrics').innerHTML=metrics.map(([name,value])=>`<div><small>${esc(name)}</small><strong>${esc(value)}</strong></div>`).join('');
  if(!goalDirty){$('goalX').value=m.goalX;$('goalY').value=m.goalY;}
  const events=state.events.filter(e=>e.agent_id===agent.id||(e.kind==='goal'&&e.message?.includes(agent.id))).slice(0,12);
  $('agentEvents').innerHTML=events.map(e=>`<div class="event"><i class="event-bullet"></i><span>${esc(eventMessage(e))}</span><time>${Math.floor(e.sim_seconds)}s</time></div>`).join('')||'<p class="empty">No goal or zone-crossing events for this agent yet.</p>';
  drawFactory2d($('agentMap'),state,{selected:selectedAgent});
}

function render(){if(!state)return;if(page==='overview')renderOverview();else if(page==='telemetry')renderTelemetry();else if(page==='recorded')renderRecorded();else renderAgents();}

async function poll(){
  if(requestActive)return;requestActive=true;
  try{
    const response=await fetch('/api/state',{signal:AbortSignal.timeout(5000)});if(!response.ok)throw new Error('API unavailable');
    const next=await response.json();
    if(next.mqtt.received<lastReceivedCount){buffer.clear();selectedPayload=null;}
    lastReceivedCount=next.mqtt.received;state=next;buffer.ingest(state.messages);serviceOnline=true;badge('apiStatus','● Service online',true);render();
  }catch{serviceOnline=false;badge('apiStatus','Service offline',false);render();}finally{requestActive=false;}
}

function download(name,contents,type){const url=URL.createObjectURL(new Blob([contents],{type})),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function toolHint(){$('mapHint').textContent=({ignite:'Click the floor to place a fire',obstacle:'Click a cell to toggle an obstacle',goal:`Click to set a goal for ${$('agentSelect').value}`,inspect:'Click a cell to inspect temperature and smoke'})[$('tool').value];}
function locateAgent(chooseGoal=false){
  if(!state)return;const id=selectedAgent;
  $('agentSelect').value=id;if(chooseGoal){$('tool').value='goal';toolHint();}
  if(page!=='overview')location.hash='overview';setMapMode('3d');scene?.update(state,layer,id);scene?.focusAgent(id);
  $('sceneSelection').textContent=`Selected ${id}`;
  if(chooseGoal)toast(`Click the factory floor to set ${id}'s goal.`);
}

window.addEventListener('hashchange',routePage);
window.addEventListener('resize',()=>{if(!state)return;if(page==='overview'){renderMap();drawTrend();}else if(page==='agents')drawFactory2d($('agentMap'),state,{selected:selectedAgent});});
document.addEventListener('visibilitychange',()=>{if(scene)scene.active=!document.hidden&&page==='overview'&&mapMode==='3d';});
$('demo').addEventListener('click',async()=>{goalDirty=false;await command('demo');});
$('rescueDemo').addEventListener('click',async()=>{goalDirty=false;await command('rescue_demo');});
$('toggle').addEventListener('click',()=>state&&command(state.paused?'resume':'pause'));
$('reset').addEventListener('click',async()=>{goalDirty=false;await command('reset');});
$('extinguish').addEventListener('click',()=>command('extinguish'));
$('speed').addEventListener('change',e=>command('speed',{value:Number(e.target.value)}));
$('intensity').addEventListener('input',e=>$('intensityValue').textContent=Number(e.target.value).toFixed(2)+'×');
$('tool').addEventListener('change',toolHint);
$('agentSelect').addEventListener('change',e=>{selectAgent(e.target.value);toolHint();});
$('mapModes').addEventListener('click',e=>{const button=e.target.closest('[data-mode]');if(button)setMapMode(button.dataset.mode);});
$('layers').addEventListener('click',e=>{const button=e.target.closest('[data-layer]');if(!button)return;layer=button.dataset.layer;for(const b of document.querySelectorAll('[data-layer]')){b.classList.toggle('selected',b.dataset.layer===layer);b.setAttribute('aria-pressed',b.dataset.layer===layer?'true':'false');}renderMap();});
$('resetCamera').addEventListener('click',()=>scene?.resetCamera());$('fallback2d').addEventListener('click',()=>setMapMode('2d'));
document.querySelectorAll('[data-camera]').forEach(button=>button.addEventListener('click',()=>scene?.moveCamera(button.dataset.camera)));
$('factoryMap').addEventListener('click',e=>{if(!state)return;const rect=e.currentTarget.getBoundingClientRect();editCell(Math.floor((e.clientX-rect.left)/rect.width*state.width),Math.floor((e.clientY-rect.top)/rect.height*state.height));});
for(const id of Object.values(filterIds))$(id).addEventListener(id==='filterSearch'?'input':'change',()=>state&&renderTelemetry());
$('clearFilters').addEventListener('click',()=>{for(const id of Object.values(filterIds))$(id).value='';if(state)renderTelemetry();});
$('sensorRows').addEventListener('click',e=>{const button=e.target.closest('[data-reading]');if(!button)return;const reading=filteredReadings[Number(button.dataset.reading)];if(reading)inspectPayload({topic:topicFor(reading),reading});});
$('messages').addEventListener('click',e=>{const button=e.target.closest('[data-message]');if(button&&visibleMessages[Number(button.dataset.message)])inspectPayload(visibleMessages[Number(button.dataset.message)]);});
$('pauseLog').addEventListener('click',()=>{buffer.paused=!buffer.paused;if(state){if(!buffer.paused)buffer.ingest(state.messages);renderTelemetry();}});
$('copyPayload').addEventListener('click',async()=>{if(!selectedPayload)return;try{await navigator.clipboard.writeText(JSON.stringify(selectedPayload.reading,null,2));toast('JSON copied to clipboard.');}catch{toast('Clipboard unavailable. Select and copy the JSON text directly.');}});
$('exportFiltered').addEventListener('click',()=>{if(!filteredReadings.length){toast('No matching readings to export.');return;}download('rescuetwins-filtered-readings.csv',readingsCsv(filteredReadings),'text/csv;charset=utf-8');});
for(const id of ['agentZoneFilter','agentStatusFilter'])$(id).addEventListener('change',()=>state&&renderAgents());
$('agentRows').addEventListener('click',e=>{const button=e.target.closest('[data-agent]');if(button)selectAgent(button.dataset.agent);});
for(const id of ['goalX','goalY'])$(id).addEventListener('input',()=>{goalDirty=true;});
$('goalForm').addEventListener('submit',async e=>{
  e.preventDefault();if(!state)return;
  try{
    const cell=worldToGrid(Number($('goalX').value),Number($('goalY').value),state),i=cell.y*state.width+cell.x;
    if(state.walls.some(([x,y])=>x===cell.x&&y===cell.y)||state.heat[i]>=70||state.smoke[i]>=.65||state.observed_hazard_cells?.some(([x,y])=>x===cell.x&&y===cell.y))throw new Error('Choose a walkable goal outside simulated and sensor-derived hazards.');
    const id=selectedAgent,button=e.currentTarget.querySelector('[type=submit]');button.disabled=true;
    try{if(await command('goal',{...cell,agent_id:id})){goalDirty=false;$('goalFeedback').textContent=`Goal applied to ${id}${state.paused?'. Resume the simulation to start moving.':'. Route updates on the next simulation step.'}`;renderAgents();}}finally{button.disabled=false;}
  }catch(error){$('goalFeedback').textContent=error.message;toast(error.message);}
});
$('locateAgent').addEventListener('click',()=>locateAgent());$('goalOnMap').addEventListener('click',()=>locateAgent(true));
$('incidentRows').addEventListener('click',async e=>{const b=e.target.closest('[data-incident-ack],[data-incident-retry]');if(!b)return;const ack=b.dataset.incidentAck;b.disabled=true;try{await command(ack?'ack_incident':'retry_incident',{incident_id:ack||b.dataset.incidentRetry});}finally{b.disabled=false;}});
setInterval(()=>$('clock').textContent=new Date().toLocaleTimeString('en-GB',{hour12:false}),1000);
window.addEventListener('pagehide',()=>scene?.dispose(),{once:true});

function renderRecorded(){
  const r=state?.recorded;if(!r)return;
  const old=$('recordedDrone').value, ids=r.frame.agents.map(a=>a.device_id);
  if([...$('recordedDrone').options].map(o=>o.value).join(',')!==ids.join(',')){
    $('recordedDrone').replaceChildren(...ids.map(id=>new Option(id,id)));
    if(ids.includes(old))$('recordedDrone').value=old;
  }
  $('recordedStep').max=r.max_step;$('recordedStep').value=r.step;$('recordedStepLabel').textContent=`${r.step} / ${r.max_step}`;
  $('recordedSummary').textContent=`Historical replay · ${r.agent_rows} agent rows · ${r.sensor_report.rows} sensor rows · ${r.sensor_report.compatible} compatible with configured A/B contract · ${r.sensor_report.rejected.length} incompatible · No CSE writes`;
  $('recordedReport').textContent=JSON.stringify(r.sensor_report,null,2);
  $('recordedWarnings').textContent=r.frame.unclassified_hazards.length?`${r.frame.unclassified_hazards.length} sensor signals have no threshold for their original unit. They are not classified as safe or hazardous. Routes check walls and configured hazards only.`:'Routes use recorded positions and configured hazards. No real flight or fire-safety claim.';
  const plan=r.plans.find(p=>p.drone_id===$('recordedDrone').value);
  $('recordedPlanStatus').textContent=plan?`${plan.status}${plan.reason?': '+plan.reason:''}${plan.simulation?' · Round trip simulated; no hardware command':''}`:'Choose a drone and plan a mission.';
  $('recordedPlanJson').textContent=plan?JSON.stringify(plan,null,2):'';
  $('recordedSimulate').disabled=!plan||!['planned','return_home'].includes(plan.status);
  $('recordedRobotTasks').innerHTML=r.robot_task_suggestions.map(t=>`<tr><td>${esc(t.operation)}</td><td>${esc(t.zone_id)}</td><td>${esc(t.goal.join(', '))}</td><td>${esc(t.status==='suggestion_requires_robot_adapter'?'Awaiting robot connection':t.status)}</td></tr>`).join('')||emptyRow(4,'No victim has been reported at this replay step.');
  const context=canvasContext($('recordedMap'));if(!context)return;
  const {ctx,w,h}=context,cw=w/r.grid.width,ch=h/r.grid.height;
  ctx.fillStyle='#f5f8fc';ctx.fillRect(0,0,w,h);
  for(let y=0;y<r.grid.height;y++)for(let x=0;x<r.grid.width;x++){ctx.fillStyle=['#eef6ff','#f0faf1','#fff8ec'][Math.floor(x/10)];ctx.fillRect(x*cw,y*ch,cw-1,ch-1);}
  ctx.fillStyle='#65768b';for(const [x,y]of r.grid.walls)ctx.fillRect(x*cw,y*ch,cw,ch);
  for(const hazard of r.frame.hazards){ctx.fillStyle='#fca5a580';ctx.fillRect((hazard.x-2)*cw,(hazard.y-2)*ch,5*cw,5*ch);}
  if(plan?.outbound){ctx.strokeStyle='#15803d';ctx.lineWidth=3;ctx.beginPath();plan.outbound.forEach(([x,y],i)=>{const px=(x+.5)*cw,py=(y+.5)*ch;i?ctx.lineTo(px,py):ctx.moveTo(px,py);});ctx.stroke();}
  for(const [rows,color]of [[r.frame.nodes,'#bd7b16'],[r.frame.victims,'#dc2626'],[r.frame.agents,'#2563eb']])for(const item of rows){ctx.fillStyle=color;ctx.beginPath();ctx.arc((item.x+.5)*cw,(item.y+.5)*ch,Math.min(cw,ch)*.32,0,Math.PI*2);ctx.fill();ctx.font='11px Segoe UI';const label=item.kind==='victim_found'?'Victim':item.device_id;ctx.fillText(label,Math.max(4,Math.min(w-ctx.measureText(label).width-4,(item.x+.8)*cw)),Math.max(12,(item.y+.3)*ch));}
}

for(const [id,kind]of [['recordedSensors','sensors'],['recordedAgents','agents']])$(id).addEventListener('change',async e=>{
  const file=e.target.files[0];if(!file)return;
  if(file.size>4*1024*1024){toast('Choose a file up to 4 MiB.');return;}
  e.target.disabled=true;
  try{const response=await fetch('/api/upload/'+kind,{method:'POST',headers:{'Content-Type':'application/json'},body:await file.text(),signal:AbortSignal.timeout(15000)});const data=await response.json();if(!response.ok)throw new Error(data.error||'Upload failed');await poll();toast('File loaded. Original values and times retained.');}catch(error){toast(error.message);}finally{e.target.disabled=false;e.target.value='';}
});
$('recordedStep').addEventListener('change',e=>command('recorded_seek',{step:Number(e.target.value)}));
$('recordedDrone').addEventListener('change',()=>renderRecorded());
$('recordedPlanForm').addEventListener('submit',async e=>{
  e.preventDefault();const x=$('recordedGoalX').value,y=$('recordedGoalY').value;
  if((x==='')!==(y==='')){toast('Enter both goal coordinates, or leave both empty for automatic planning.');return;}
  await command('drone_plan',{drone_id:$('recordedDrone').value,goal:x===''?null:[Number(x),Number(y)],battery:Number($('recordedBattery').value)});
});
$('recordedSimulate').addEventListener('click',()=>{const p=state?.recorded?.plans.find(p=>p.drone_id===$('recordedDrone').value);if(p)command('drone_simulate',{drone_id:p.drone_id,plan_id:p.plan_id});});

routePage();setInterval(poll,700);poll();
