import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export const AGENT_COLORS = ['#4084c2','#8b75ba','#309f92','#d0953b','#d15e6e','#20a9a4','#b465c6','#759c32'];

function disposeTree(group) {
  const geometries = new Set(), materials = new Set(), textures = new Set();
  group.traverse(item => {
    if (item.geometry) geometries.add(item.geometry);
    for (const material of (Array.isArray(item.material) ? item.material : [item.material])) {
      if (!material) continue;
      materials.add(material);
      if (material.map) textures.add(material.map);
    }
  });
  geometries.forEach(value => value.dispose());
  materials.forEach(value => value.dispose());
  textures.forEach(value => value.dispose());
  group.clear();
}

export class FactoryScene {
  constructor(canvas, {onCell, onAgent, onError}) {
    this.canvas = canvas; this.onCell = onCell; this.onAgent = onAgent; this.onError = onError;
    this.active = true; this.state = null; this.layer = 'heat'; this.selected = 'agent-01';
    this.renderer = new THREE.WebGLRenderer({canvas, antialias:true, alpha:false, powerPreference:'low-power'});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.7));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor('#eaf0f5');
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(40, 1, .1, 600);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true; this.controls.dampingFactor = .085;
    this.controls.mouseButtons = {LEFT:THREE.MOUSE.ROTATE, MIDDLE:THREE.MOUSE.PAN, RIGHT:THREE.MOUSE.PAN};
    this.controls.screenSpacePanning = true;
    this.controls.minDistance = 28; this.controls.maxDistance = 220;
    this.controls.minPolarAngle = .1; this.controls.maxPolarAngle = Math.PI * .47;
    this.resetCamera();
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x91a4ba, 2.5));
    const sun = new THREE.DirectionalLight(0xfff4df, 2.5); sun.position.set(30,80,40); this.scene.add(sun);
    const fill = new THREE.DirectionalLight(0xd9ebff, 1.2); fill.position.set(-60,30,-20); this.scene.add(fill);
    this.factory = new THREE.Group(); this.scene.add(this.factory);
    this.walls = new THREE.Group(); this.scene.add(this.walls);
    this.robots = new THREE.Group(); this.scene.add(this.robots);
    this.routes = new THREE.Group(); this.scene.add(this.routes);
    this.robotMap = new Map(); this.routeMap = new Map();
    this.raycaster = new THREE.Raycaster(); this.pointer = new THREE.Vector2();
    this.ground = new THREE.Plane(new THREE.Vector3(0,1,0), 0);
    this.matrix = new THREE.Object3D(); this.color = new THREE.Color();
    this.baseA = new THREE.Color('#e2edf1'); this.baseB = new THREE.Color('#e5ede6');
    this.fireColor = new THREE.Color('#e65a2f'); this.smokeColor = new THREE.Color('#52627c');
    this.canvas.addEventListener('webglcontextlost', event => {event.preventDefault(); this.active=false; this.onError?.('The 3D graphics context was lost. Use the 2D grid.');});
    // Keep middle dragging inside the map instead of starting browser auto-scroll.
    this.canvas.addEventListener('pointerdown', event => {if(event.button===1)event.preventDefault();}, {capture:true});
    this.canvas.addEventListener('auxclick', event => {if(event.button===1)event.preventDefault();});
    this.pointers=new Set();
    this.canvas.addEventListener('pointerdown', event => {this.pointers.add(event.pointerId);this.down=this.pointers.size===1?{id:event.pointerId,x:event.clientX,y:event.clientY,button:event.button,moved:false}:null;});
    this.canvas.addEventListener('pointermove', event => {if(this.down&&Math.hypot(event.clientX-this.down.x,event.clientY-this.down.y)>6)this.down.moved=true;});
    this.canvas.addEventListener('pointerup', event => this.pick(event));
    this.canvas.addEventListener('pointercancel', event => {this.down=null;this.pointers.delete(event.pointerId);});
    this.renderer.setAnimationLoop(time => this.animate(time / 1000));
  }

  material(color, extra={}) {return new THREE.MeshStandardMaterial({color, roughness:.75, metalness:.08, ...extra});}

  box(group, x,y,z, width,height,depth, color) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(width,height,depth), this.material(color));
    mesh.position.set(x,y,z); group.add(mesh); return mesh;
  }

  label(text, x,y,z, color='#6d8393', width=13) {
    const surface = document.createElement('canvas'); surface.width=512; surface.height=112;
    const context=surface.getContext('2d'); context.clearRect(0,0,512,112);
    context.fillStyle=color; context.font='600 55px Segoe UI, sans-serif'; context.textAlign='center'; context.textBaseline='middle'; context.fillText(text,256,56);
    const texture = new THREE.CanvasTexture(surface); texture.colorSpace=THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({map:texture, transparent:true, depthWrite:false}));
    sprite.position.set(x,y,z); sprite.scale.set(width,width*112/512,1); return sprite;
  }

  resetCamera() {
    const span=Math.max((this.state?.width||80)*(this.state?.cell_m||2),(this.state?.height||40)*(this.state?.cell_m||2));this.camera.position.set(span*.8,span*1.05,span*.85);this.controls.maxDistance=span*4;this.camera.far=span*6;this.camera.updateProjectionMatrix(); this.controls.target.set(0,0,0); this.controls.update();
  }

  moveCamera(action) {
    const offset=this.camera.position.clone().sub(this.controls.target);
    if(action==='left'||action==='right')offset.applyAxisAngle(new THREE.Vector3(0,1,0),action==='left'?.25:-.25);
    else offset.multiplyScalar(action==='in'?.82:1.22);
    this.camera.position.copy(this.controls.target).add(offset);this.controls.update();
  }

  buildFactory(state) {
    const width=state.width*state.cell_m, depth=state.height*state.cell_m;
    const split=(state.zone_x??state.width/2)*state.cell_m-width/2;
    this.box(this.factory,0,-.6,0,width+1,1,depth+1,'#b6c7d1');
    this.box(this.factory,(split-width/2)/2,-.02,0,split+width/2,.08,depth,'#e2edf1');
    this.box(this.factory,(split+width/2)/2,-.02,0,width/2-split,.08,depth,'#e5ede6');
    const points=[];
    for(let x=0;x<=state.width;x++) points.push(x*state.cell_m-width/2,.065,-depth/2,x*state.cell_m-width/2,.065,depth/2);
    for(let y=0;y<=state.height;y++) points.push(-width/2,.065,y*state.cell_m-depth/2,width/2,.065,y*state.cell_m-depth/2);
    const geometry=new THREE.BufferGeometry(); geometry.setAttribute('position',new THREE.Float32BufferAttribute(points,3));
    this.factory.add(new THREE.LineSegments(geometry,new THREE.LineBasicMaterial({color:'#91aab9',transparent:true,opacity:.3})));
    const boundary=new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(split,.12,-depth/2),new THREE.Vector3(split,.12,depth/2)]),new THREE.LineDashedMaterial({color:'#4f91b0',dashSize:2,gapSize:1})); boundary.computeLineDistances();this.factory.add(boundary);
    this.factory.add(this.label('ZONE A',(split-width/2)/2,1.2,-depth/2+4),this.label('ZONE B',(split+width/2)/2,1.2,-depth/2+4));
    for(const sensor of state.sensors) {
      const x=(sensor.x+.5)*state.cell_m-state.width*state.cell_m/2,z=(sensor.y+.5)*state.cell_m-state.height*state.cell_m/2;
      const base=new THREE.Mesh(new THREE.CylinderGeometry(.85,1.05,.3,12),this.material('#4d7c76'));base.position.set(x,.15,z);this.factory.add(base);
      const pole=new THREE.Mesh(new THREE.CylinderGeometry(.17,.17,2.5,8),this.material('#7b939e'));pole.position.set(x,1.4,z);this.factory.add(pole);
      this.box(this.factory,x,2.8,z,.9,.8,.7,'#2da58b');
      const lamp=new THREE.Mesh(new THREE.SphereGeometry(.22,8,6),new THREE.MeshBasicMaterial({color:'#b7ffda'}));lamp.position.set(x,3.45,z);this.factory.add(lamp);
      this.factory.add(this.label(sensor.id,x,4.4,z,'#357a6b',9));
    }
    this.tiles=new THREE.InstancedMesh(new THREE.PlaneGeometry(state.cell_m*.98,state.cell_m*.98),new THREE.MeshBasicMaterial({transparent:true,opacity:.87,depthWrite:false}),state.width*state.height);
    this.tiles.instanceMatrix.setUsage(THREE.StaticDrawUsage);
    for(let y=0;y<state.height;y++)for(let x=0;x<state.width;x++){
      this.matrix.position.set((x+.5)*state.cell_m-width/2,.04,(y+.5)*state.cell_m-depth/2);this.matrix.rotation.set(-Math.PI/2,0,0);this.matrix.scale.set(1,1,1);this.matrix.updateMatrix();this.tiles.setMatrixAt(y*state.width+x,this.matrix.matrix);
    }
    this.tiles.instanceMatrix.needsUpdate=true;this.scene.add(this.tiles);
    this.flames=new THREE.InstancedMesh(new THREE.ConeGeometry(.85,3,7),new THREE.MeshBasicMaterial({color:'#f27732'}),state.width*state.height);this.flames.count=0;this.flames.frustumCulled=false;this.scene.add(this.flames);
    this.cores=new THREE.InstancedMesh(new THREE.ConeGeometry(.45,1.8,7),new THREE.MeshBasicMaterial({color:'#ffdf87'}),state.width*state.height);this.cores.count=0;this.cores.frustumCulled=false;this.scene.add(this.cores);
    this.smokeClouds=new THREE.InstancedMesh(new THREE.SphereGeometry(1,8,6),new THREE.MeshBasicMaterial({color:'#748399',transparent:true,opacity:.19,depthWrite:false}),200);this.smokeClouds.count=0;this.smokeClouds.frustumCulled=false;this.scene.add(this.smokeClouds);
  }

  updateWalls(state) {
    const key=JSON.stringify(state.walls);if(key===this.wallKey)return;this.wallKey=key;disposeTree(this.walls);
    for(const [x,y]of state.walls){
      const rack=x>=21&&x<=28&&[5,6,24,25].includes(y);
      this.box(this.walls,(x+.5)*this.state.cell_m-this.state.width*this.state.cell_m/2,rack?1.2:2,(y+.5)*this.state.cell_m-this.state.height*this.state.cell_m/2,rack?1.75:1.9,rack?2.4:4,rack?1.75:1.9,rack?'#c69a60':'#7d94a7');
      if(rack)this.box(this.walls,(x+.5)*this.state.cell_m-this.state.width*this.state.cell_m/2,2.55,(y+.5)*this.state.cell_m-this.state.height*this.state.cell_m/2,1.9,.14,1.9,'#596e80');
    }
  }

  makeRobot(agent,index) {
    const group=new THREE.Group(), color=AGENT_COLORS[index%AGENT_COLORS.length];
    this.box(group,0,.65,0,1.55,.8,1.9,color);this.box(group,0,1.25,-.05,.95,.55,1.1,'#dce8ed');
    this.box(group,0,1.55,.1,.7,.18,.65,color);
    this.box(group,0,.95,0,3.8,.12,.18,color);this.box(group,0,.95,0,.18,.12,3.8,color);
    for(const x of [-1.8,1.8])for(const z of [-1.8,1.8]){const rotor=new THREE.Mesh(new THREE.TorusGeometry(.75,.07,5,20),this.material('#344856'));rotor.rotation.x=Math.PI/2;rotor.position.set(x,1,z);group.add(rotor);}
    const ring=new THREE.Mesh(new THREE.TorusGeometry(1.45,.09,5,28),new THREE.MeshBasicMaterial({color:'#ed6a39'}));ring.rotation.x=-Math.PI/2;ring.position.y=.1;group.add(ring);
    group.traverse(item=>{item.userData.agentId=agent.id;});
    const label=this.label(agent.id,0,3.5,0,color,8);group.add(label);
    group.position.set((agent.x+.5)*this.state.cell_m-this.state.width*this.state.cell_m/2,0,(agent.y+.5)*this.state.cell_m-this.state.height*this.state.cell_m/2);
    this.robots.add(group);
    const goal=new THREE.Mesh(new THREE.TorusGeometry(1,.055,5,20),new THREE.MeshBasicMaterial({color}));goal.rotation.x=-Math.PI/2;goal.position.y=.16;this.scene.add(goal);
    const record={group,ring,label,goal,target:group.position.clone(),pathKey:null,line:null,index};this.robotMap.set(agent.id,record);return record;
  }

  update(state, layer, selected) {
    this.state=state;this.layer=layer;this.selected=selected;
    if(!this.tiles)this.buildFactory(state);
    this.updateWalls(state);
    const observedCells=new Set((state.observed_hazard_cells||[]).map(([x,y])=>y*state.width+x));
    for(let i=0;i<state.heat.length;i++){
      this.color.copy(i%state.width<(state.zone_x??state.width/2)?this.baseA:this.baseB);
      if(layer==='heat')this.color.lerp(this.fireColor,Math.min(1,Math.max(0,(state.heat[i]-28.6)/115)));
      if(layer==='exploration')this.color.set((state.pheromone?.[i]||0)>0?'#56ad8b':'#b3bdc8');
      if(layer==='smoke')this.color.lerp(this.smokeColor,Math.min(1,state.smoke[i]));
      if(observedCells.has(i))this.color.lerp(new THREE.Color('#ce394e'),.7);
      this.tiles.setColorAt(i,this.color);
    }
    this.tiles.instanceColor.needsUpdate=true;
    this.smokeCells=state.smoke.map((v,i)=>({v,i})).filter(p=>p.v>.035).sort((a,b)=>b.v-a.v).slice(0,200);
    state.agents.forEach((agent,index)=>{
      const r=this.robotMap.get(agent.id)||this.makeRobot(agent,index);
      r.target.set((agent.x+.5)*this.state.cell_m-this.state.width*this.state.cell_m/2,0,(agent.y+.5)*this.state.cell_m-this.state.height*this.state.cell_m/2);r.ring.visible=agent.id===selected;
      r.goal.position.set((agent.goal[0]+.5)*state.cell_m-state.width*state.cell_m/2,.16,(agent.goal[1]+.5)*state.cell_m-state.height*state.cell_m/2);
      const pathKey=JSON.stringify(agent.path);
      if(r.pathKey!==pathKey){
        if(r.line){this.routes.remove(r.line);r.line.geometry.dispose();r.line.material.dispose();r.line=null;}
        if(agent.path.length>1){const points=agent.path.map(([x,y])=>new THREE.Vector3((x+.5)*this.state.cell_m-this.state.width*this.state.cell_m/2,.2,(y+.5)*this.state.cell_m-this.state.height*this.state.cell_m/2));r.line=new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color:AGENT_COLORS[index%AGENT_COLORS.length],transparent:true,opacity:.85}));this.routes.add(r.line);}
        r.pathKey=pathKey;
      }
    });
  }

  focusAgent(id) {
    const record=this.robotMap.get(id);if(!record)return;
    this.selected=id;this.controls.target.copy(record.target);this.camera.position.copy(record.target).add(new THREE.Vector3(27,36,34));this.controls.update();
    this.robotMap.forEach((r,key)=>{r.ring.visible=key===id;});
  }

  pick(event) {
    this.pointers.delete(event.pointerId);
    const down=this.down;this.down=null;
    if(!down||down.id!==event.pointerId||this.pointers.size||down.moved||down.button!==0||!this.state||Math.hypot(event.clientX-down.x,event.clientY-down.y)>6)return;
    const rect=this.canvas.getBoundingClientRect();this.pointer.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);
    this.scene.updateMatrixWorld(true);this.camera.updateMatrixWorld();this.raycaster.setFromCamera(this.pointer,this.camera);
    const robot=this.raycaster.intersectObjects(this.robots.children,true).find(hit=>hit.object.userData.agentId);
    if(robot){this.onAgent?.(robot.object.userData.agentId);return;}
    const point=this.raycaster.ray.intersectPlane(this.ground,new THREE.Vector3());if(!point)return;
    const x=Math.floor((point.x+this.state.width*this.state.cell_m/2)/this.state.cell_m),y=Math.floor((point.z+this.state.height*this.state.cell_m/2)/this.state.cell_m);
    if(x>=0&&x<this.state.width&&y>=0&&y<this.state.height)this.onCell?.(x,y);
  }

  animate(time) {
    if(!this.active||!this.canvas.isConnected||this.canvas.hidden||!this.canvas.clientWidth||!this.state)return;
    const width=this.canvas.clientWidth,height=this.canvas.clientHeight;
    if(width!==this.lastWidth||height!==this.lastHeight){this.renderer.setSize(width,height,false);this.camera.aspect=width/height;this.camera.updateProjectionMatrix();this.lastWidth=width;this.lastHeight=height;}
    this.robotMap.forEach(r=>{
      const dx=r.target.x-r.group.position.x,dz=r.target.z-r.group.position.z;
      r.group.position.lerp(r.target,.22);
      if(Math.abs(dx)+Math.abs(dz)>.03)r.group.rotation.y=Math.atan2(dx,dz);
      r.label.rotation.y=-r.group.rotation.y;
    });
    this.matrix.rotation.set(0,0,0);
    this.flames.count=this.cores.count=this.state.fires.length;
    this.state.fires.forEach(([x,y],i)=>{
      const flicker=1+.13*Math.sin(time*5+i*2.1);
      this.matrix.position.set((x+.5)*this.state.cell_m-this.state.width*this.state.cell_m/2,1.5*flicker,(y+.5)*this.state.cell_m-this.state.height*this.state.cell_m/2);this.matrix.scale.set(1,flicker,1);this.matrix.updateMatrix();this.flames.setMatrixAt(i,this.matrix.matrix);
      this.matrix.position.y=.9*flicker;this.matrix.updateMatrix();this.cores.setMatrixAt(i,this.matrix.matrix);
    });
    this.flames.instanceMatrix.needsUpdate=this.cores.instanceMatrix.needsUpdate=true;
    this.smokeClouds.count=this.smokeCells.length;
    this.smokeCells.forEach(({v,i},j)=>{
      const x=i%this.state.width,y=Math.floor(i/this.state.width),phase=time*.45+i*.7;
      this.matrix.position.set((x+.5)*this.state.cell_m-this.state.width*this.state.cell_m/2+Math.sin(phase)*.4,2+v*5+Math.cos(phase)*.5,(y+.5)*this.state.cell_m-this.state.height*this.state.cell_m/2);
      this.matrix.scale.setScalar(.65+v*1.8);this.matrix.updateMatrix();this.smokeClouds.setMatrixAt(j,this.matrix.matrix);
    });
    this.smokeClouds.instanceMatrix.needsUpdate=true;
    this.controls.update();this.renderer.render(this.scene,this.camera);
  }

  dispose() {this.active=false;this.renderer.setAnimationLoop(null);this.controls.dispose();disposeTree(this.scene);this.renderer.dispose();}
}
