import { AGENT_COLORS } from './scene3d.js';

export function canvasContext(canvas) {
  const rect=canvas.getBoundingClientRect(),ratio=Math.min(window.devicePixelRatio||1,2);
  if(!rect.width||!rect.height)return null;
  if(canvas.width!==Math.round(rect.width*ratio)||canvas.height!==Math.round(rect.height*ratio)){canvas.width=Math.round(rect.width*ratio);canvas.height=Math.round(rect.height*ratio);}
  const ctx=canvas.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,rect.width,rect.height);
  return {ctx,w:rect.width,h:rect.height};
}

export function drawFactory2d(canvas,state,{layer='heat',selected=null}={}) {
  const context=canvasContext(canvas);if(!context)return;
  const {ctx,w,h}=context,cw=w/state.width,ch=h/state.height;
  ctx.fillStyle='#f8fafc';ctx.fillRect(0,0,w,h);
  for(let y=0;y<state.height;y++)for(let x=0;x<state.width;x++){
    const k=y*state.width+x,heat=state.heat[k],smoke=state.smoke[k];
    if(layer==='heat'&&heat>30){const t=Math.min(1,(heat-28.6)/120);ctx.fillStyle=`rgba(236,${Math.round(172-t*125)},58,${Math.min(.88,t*1.2+.07)})`;ctx.fillRect(x*cw,y*ch,cw+.3,ch+.3);}
    if(layer==='smoke'&&smoke>.005){ctx.fillStyle=`rgba(68,83,111,${Math.min(.9,smoke)})`;ctx.fillRect(x*cw,y*ch,cw+.3,ch+.3);}
  }
  ctx.strokeStyle='#e5eaf080';ctx.lineWidth=.5;
  for(const [x,y] of state.observed_hazard_cells||[]){ctx.fillStyle='rgba(210,56,71,.48)';ctx.fillRect(x*cw,y*ch,cw,ch);}
  for(let x=0;x<=state.width;x++){ctx.beginPath();ctx.moveTo(x*cw,0);ctx.lineTo(x*cw,h);ctx.stroke();}
  for(let y=0;y<=state.height;y++){ctx.beginPath();ctx.moveTo(0,y*ch);ctx.lineTo(w,y*ch);ctx.stroke();}
  for(const [x,y]of state.walls){ctx.fillStyle='#8497a6';ctx.fillRect(x*cw+.5,y*ch+.5,cw-1,ch-1);ctx.fillStyle='#9aabb8';ctx.fillRect(x*cw+1,y*ch+1,cw-2,2);}
  ctx.setLineDash([5,5]);ctx.strokeStyle='#abc3d6';ctx.lineWidth=1.1;ctx.beginPath();ctx.moveTo(w/2,0);ctx.lineTo(w/2,h);ctx.stroke();ctx.setLineDash([]);
  ctx.font='600 11px Segoe UI,sans-serif';ctx.fillStyle='#91a6b8';ctx.textAlign='left';ctx.fillText('ZONE A',14,22);ctx.fillText('ZONE B',w/2+14,22);
  for(const [ex,ey]of [[48,8],[48,22]]){ctx.fillStyle='#daeae2';ctx.fillRect(ex*cw,(ey-1)*ch,cw*2,ch*3);ctx.fillStyle='#6a9b7e';ctx.textAlign='center';ctx.font='bold 12px Segoe UI';ctx.fillText('→',(ex+1)*cw,(ey+.8)*ch);}
  state.agents.forEach((a,i)=>{
    const last=a.path.at(-1),valid=last&&last[0]===a.goal[0]&&last[1]===a.goal[1];
    if(a.path.length>1&&valid){ctx.globalAlpha=selected&&a.id!==selected?.25:1;ctx.strokeStyle=AGENT_COLORS[i%4];ctx.lineWidth=a.id===selected?2.2:1.5;ctx.setLineDash([4,3]);ctx.beginPath();a.path.forEach(([x,y],n)=>n?ctx.lineTo((x+.5)*cw,(y+.5)*ch):ctx.moveTo((x+.5)*cw,(y+.5)*ch));ctx.stroke();ctx.setLineDash([]);ctx.globalAlpha=1;}
    const [gx,gy]=a.goal;ctx.strokeStyle=AGENT_COLORS[i%4];ctx.strokeRect(gx*cw+1,gy*ch+1,cw-2,ch-2);
  });
  for(const [x,y]of state.fires){const cx=(x+.5)*cw,cy=(y+.5)*ch;ctx.fillStyle='#f06436';ctx.beginPath();ctx.moveTo(cx,cy-ch*.7);ctx.quadraticCurveTo(cx+cw*.9,cy+ch*.3,cx,cy+ch*.55);ctx.quadraticCurveTo(cx-cw*.8,cy+ch*.3,cx,cy-ch*.7);ctx.fill();}
  for(const sensor of state.sensors){const x=(sensor.x+.5)*cw,y=(sensor.y+.5)*ch;ctx.fillStyle='white';ctx.beginPath();ctx.arc(x,y,6,0,Math.PI*2);ctx.fill();ctx.strokeStyle='#2e9b8a';ctx.lineWidth=2;ctx.stroke();ctx.fillStyle='#2e9b8a';ctx.fillRect(x-2,y-2,4,4);ctx.font='9px Segoe UI';ctx.textAlign='center';ctx.fillStyle='#437365';ctx.fillText(sensor.id,x,y-11);}
  state.agents.forEach((a,i)=>{const x=(a.x+.5)*cw,y=(a.y+.5)*ch;ctx.fillStyle=AGENT_COLORS[i%4];ctx.strokeStyle=a.id===selected?'#ed6a39':'white';ctx.lineWidth=a.id===selected?3:2;ctx.beginPath();ctx.arc(x,y,a.id===selected?7:5,0,Math.PI*2);ctx.fill();ctx.stroke();ctx.fillStyle='#526c83';ctx.font='9px Segoe UI';ctx.textAlign='center';ctx.fillText(a.id.replace('agent-','A'),x,y+16);});
}
