export const SENSOR_UNITS = {temperature:'degC', humidity:'percent', pressure:'hPa', smoke_raw:'mV', co_raw:'mV', presence:'boolean'};

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

export function freshness(reading, now = Date.now()) {
  const time = Date.parse(reading?.observed_at);
  if (!Number.isFinite(time)) return 'unknown';
  const age = (now - time) / 1000;
  return age < -5 ? 'future' : age >= 10 ? 'stale' : 'fresh';
}

export function matchesReading(reading, filters = {}, now = Date.now()) {
  for (const [filter, field] of [['device','device_id'], ['zone','zone_id'], ['source','source'], ['sensor','sensor_type'], ['quality','quality']]) {
    if (filters[filter] && reading[field] !== filters[filter]) return false;
  }
  if (filters.freshness && freshness(reading, now) !== filters.freshness) return false;
  if (filters.search) {
    const topic = `rescuetwin/${reading.zone_id}/${reading.device_id}/${reading.sensor_type}`;
    if (!(topic + ' ' + JSON.stringify(reading)).toLowerCase().includes(filters.search.toLowerCase().trim())) return false;
  }
  return true;
}

export function messageKey(message) { return JSON.stringify([message.topic, message.received_at ?? null, message.reading]); }

export class MessageBuffer {
  constructor(limit = 500) { this.limit = limit; this.entries = []; this.keys = new Set(); this.paused = false; }
  ingest(messages) {
    if (this.paused) return;
    const fresh = messages.filter(m => {
      const key = messageKey(m);
      if (this.keys.has(key)) return false;
      this.keys.add(key);
      return true;
    });
    this.entries = [...fresh, ...this.entries].sort((a,b) => (Date.parse(b.received_at) || 0) - (Date.parse(a.received_at) || 0)).slice(0, this.limit);
    this.keys = new Set(this.entries.map(messageKey));
  }
  filtered(filters, now) { return this.entries.filter(m => matchesReading(m.reading, filters, now)); }
  clear() { this.entries = []; this.keys.clear(); }
}

export function readingsCsv(readings) {
  const fields = ['device_id','zone_id','sensor_type','value','unit','source','quality','observed_at'];
  const quote = value => '"' + String(value ?? '').replaceAll('"','""') + '"';
  return '\ufeff' + [fields.join(','), ...readings.map(r => fields.map(f => quote(r[f])).join(','))].join('\r\n');
}

export function worldToGrid(x, y, state) {
  if (![x,y].every(Number.isFinite) || x < 0 || y < 0 || x >= state.width * state.cell_m || y >= state.height * state.cell_m) {
    throw new Error(`Choose coordinates inside ${state.width * state.cell_m} × ${state.height * state.cell_m} m.`);
  }
  return {x: Math.floor(x / state.cell_m), y: Math.floor(y / state.cell_m)};
}

export function agentMetrics(agent, state) {
  const index = agent.y * state.width + agent.x;
  return {x: (agent.x + .5) * state.cell_m, y: (agent.y + .5) * state.cell_m,
    goalX: (agent.goal[0] + .5) * state.cell_m, goalY: (agent.goal[1] + .5) * state.cell_m,
    remaining: agent.path.length ? Math.max(0, agent.path.length - 1) * state.cell_m : null,
    temperature: state.heat[index], smoke: state.smoke[index],
    exposed: state.heat[index] >= 70 || state.smoke[index] >= .65 || !!state.observed_hazard_cells?.some(([x,y])=>x===agent.x&&y===agent.y)};
}

export function eventMessage(event) {
  const messages = {reset:'Scenario reset. Place a fire or run the demo.', pause:'Simulation paused', resume:'Simulation resumed', obstacle:'Obstacle layout updated', goal:'Agent goal updated', extinguish:'Fires cleared. Heat and smoke will dissipate.'};
  if (event.kind === 'fire') return `Fire placed at (${event.x * 2}, ${event.y * 2}) m`;
  if (event.kind === 'zone_crossing') return `${event.agent_id}: ${event.from_zone} → ${event.to_zone}`;
  return messages[event.kind] || event.message;
}
