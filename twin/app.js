const sensors = [
  ['temperature','Temperature','°C','♧'], ['humidity','Humidity','%','◒'],
  ['pressure','Pressure','hPa','≋'], ['presence','Presence','','◈'],
  ['smoke_raw','MQ-2 raw','ADC','⌁'], ['co_raw','MQ-7 raw','ADC','⌁']
];
const $ = id => document.getElementById(id);
let latest = new Map(), busy = false;
function timeLabel(value) { return value ? new Date(value).toLocaleString('en-GB',{hour12:false}) : 'Not synchronized'; }
function makeCard([key, title, unit, icon]) {
  const article = document.createElement('article'); article.className = 'card'; article.id = 'card-'+key;
  article.innerHTML = `<div class="card-top"><div class="card-title"><span class="sensor-icon">${icon}</span>${title}</div><span class="badge">Loading</span></div><div class="value-line"><span class="value">—</span><span class="unit">${unit}</span></div><div class="sensor-code">node-01 / ${key}</div><div class="meta"><span class="timestamp">No records yet</span><button class="details" disabled>View record ↗</button></div><p class="error-message" hidden></p>`;
  article.querySelector('.details').addEventListener('click',()=>{
    $('detail-title').textContent = title+' · Latest record';
    const r = latest.get(key);
    $('detail-json').textContent = JSON.stringify({received_at:r.received_at,observed_at:r.observed_at,resource_id:r.resource_id,data:r.payload},null,2);
    $('detail').showModal();
  });
  $('cards').append(article);
}
sensors.forEach(makeCard);
function render(data) {
  latest = new Map(data.readings.map(r=>[r.sensor,r]));
  let valid=0, fresh=0, errors=0, found=0;
  for (const [key] of sensors) {
    const r=latest.get(key) || {state:'error',message:'Sensor missing from response'};
    const card=$('card-'+key), badge=card.querySelector('.badge');
    if(r.state==='valid')valid++;
    if(r.state==='valid'&&!r.stale)fresh++;
    if(r.state==='error')errors++;
    if(r.payload)found++;
    let label={valid:r.stale?'Historical':'Valid',invalid:'Invalid',empty:'No data',error:'Read error'}[r.state]||'Unknown';
    if(r.source==='simulation'&&r.state==='valid')label='Simulated'+(r.stale?' · Historical':'');
    badge.textContent=label; badge.className='badge '+(r.state==='valid'&&r.stale?'':r.state);
    card.querySelector('.value').textContent=r.state==='valid' ? (key==='presence'?(r.value?'Detected':'Not detected'):Number(r.value).toLocaleString('en-GB',{maximumFractionDigits:2})) : '—';
    if(key==='presence')card.querySelector('.value').style.fontSize='23px';
    card.querySelector('.timestamp').textContent=r.received_at?'Received '+timeLabel(r.received_at):(r.state==='empty'?'No uploads yet':'Waiting for a valid record');
    const msg=card.querySelector('.error-message');
    msg.hidden=!r.message&&r.state!=='invalid'; msg.textContent=r.message||(r.state==='invalid'?'Invalid reading or fields; value not displayed.':'');
    card.querySelector('.details').disabled=!r.payload;
  }
  $('count').textContent=`${valid} / 6 valid`;
  $('connection').textContent=errors===6?'Platform connection error':errors?'Some readings failed':found?'Data access healthy':'Platform responding; awaiting data';
  $('connection-dot').className='dot '+(errors?'bad':'good');
  $('last-check').textContent='Checked '+timeLabel(data.checked_at);
  $('node-state').textContent=fresh?`${fresh} valid readings received in the last 60 seconds`:found?'Historical or invalid records only; awaiting new uploads':'No sensor records yet';
  $('node-core').className='node-core'+(fresh?' active':'');
}
async function refresh() {
  if(busy)return; busy=true; $('refresh').disabled=true;
  try {
    const response=await fetch('/api/snapshot',{cache:'no-store',signal:AbortSignal.timeout(12000)});
    if(!response.ok)throw new Error('Local server returned '+response.status);
    render(await response.json());
  } catch(e) {
    $('connection').textContent='Local dashboard connection lost'; $('connection-dot').className='dot bad';
    $('last-check').textContent='Check that server.py is running';
    $('node-state').textContent='Disconnected; displayed values are from the last successful read'; $('node-core').className='node-core';
    for(const card of document.querySelectorAll('.card')) {const b=card.querySelector('.badge');b.textContent='Not updated';b.className='badge error';}
  } finally {busy=false;$('refresh').disabled=false;}
}
$('refresh').addEventListener('click',refresh);
$('close-detail').addEventListener('click',()=>$('detail').close());
function tick(){$('clock').textContent=new Date().toLocaleTimeString('en-GB',{hour12:false});}
tick();setInterval(tick,1000);refresh();setInterval(refresh,5000);
