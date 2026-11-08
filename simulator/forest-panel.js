(function(){
  'use strict';
  const F=window.EagleForest, host=document.createElement('section');host.id='forest-demo';
  (document.getElementById('navigation-demo')||document.body).append(host);
  host.innerHTML=`<div class="phead"><h2>Forest navigation & perching</h2><span class="tagm">new · simulation</span></div>
    <p>Explore a Fushan-informed mountain-forest reconstruction: sloping ground, broadleaf crowns, understory and branch approaches.</p>
    <button id="forest-open">Open forest lab</button>
    <dialog id="forest-dialog" aria-labelledby="forest-title">
      <header><div><span class="forest-eyebrow">TAIWAN / FIELD CONTEXT</span><h2 id="forest-title">Fushan · a forest with structure</h2></div><button id="forest-close">Close</button></header>
      <p class="forest-intro">福山試驗林 · Northern Taiwan, lower-mountain evergreen broadleaf forest. Literature-informed reconstruction, not a scanned replica. Terrain, tree positions and contact remain modeled.</p>
      <div class="forest-site-strip"><label>Forest representation<select id="forest-site"><option value="fushan">Fushan · taller valley forest</option><option value="fushan-ridge">Fushan · shorter exposed ridge</option><option value="baseline">Original blocks · comparison baseline</option></select></label><p id="forest-provenance">Real site evidence. Modeled local geometry. No surveyed tree coordinates loaded.</p></div>
      <div class="forest-layout"><div class="forest-visual">
        <div class="forest-controls"><label>Try a scenario<select id="forest-scenario">
          <option value="branch">Branch perch · nominal</option><option value="moving">Moving branch · choose another</option>
          <option value="grip-failure">Grip failure · retreat and retry</option><option value="landing">Ground landing · clearing</option>
          <option value="blocked">Blocked canopy · no route</option><option value="sensor-loss">Lose pose / depth · abort</option>
        </select></label><label>Playback<select id="forest-speed"><option value="1">1×</option><option value="4" selected>4×</option><option value="8">8×</option></select></label></div>
        <div class="seg"><button id="forest-play">Play mission</button><button id="forest-step">Step 1 second</button><button id="forest-reset">Reset</button></div>
        <div class="forest-view-controls" aria-label="Camera controls"><button id="forest-view-orbit">Overview</button><button id="forest-view-top">Top</button><button id="forest-view-side">Side</button><button id="forest-view-reset">Reset view</button><button id="forest-view-pov">Drone view</button><label><input id="forest-follow" type="checkbox"> Follow drone</label></div>
        <div class="forest-layer-controls"><label><input id="forest-cutaway" type="checkbox"> Canopy cutaway</label><label><input id="forest-collision" type="checkbox"> Collision envelopes</label><label><input id="forest-mist" type="checkbox"> Mist visualization</label><span id="forest-renderer-state"></span></div>
        <p id="forest-cover-note" class="hint"></p>
        <canvas id="forest-map" width="900" height="490" tabindex="0" aria-label="Interactive 3D forest. Drag to orbit; scroll to zoom; right-drag or shift-drag to pan. Arrow keys orbit; plus and minus zoom; Home resets."></canvas>
        <p class="hint">Drag: orbit · scroll/pinch: zoom · right/shift-drag: pan. Cyan: route · cream: trail · gold: support. Route overlays show through foliage for explanation. Cutaway changes visibility, not collision geometry. Full synthetic map supplied.</p>
        <canvas id="forest-profile" width="900" height="140" aria-label="Flight altitude over elapsed simulated time"></canvas>
        <div id="forest-stages" aria-label="Mission stages"></div>
      </div><aside>
        <h3>What is documented at Fushan?</h3><div id="forest-evidence"></div>
        <div id="forest-status" role="status" aria-live="polite"></div>
        <h3>Why this support?</h3><div id="forest-candidates"></div>
        <h3>Where we are now</h3>
        <ul class="forest-readiness"><li><b>Field implications</b> — wet surfaces, small stems, ferns and layered crowns complicate depth, localization and grip. A visible gap is not a verified flight corridor.</li><li><b>Runs here</b> — terrain clearance, conservative obstacle envelopes, 3D route search and synthetic attachment/recovery.</li><li><b>Still needed</b> — surveyed tree/ground geometry, measured depth, onboard VIO, mechanical grip validation and the planner-to-ArduPilot bridge.</li></ul>
        <details><summary>Open-source building blocks</summary><p><a href="https://github.com/ZJU-FAST-Lab/ego-planner-swarm">EGO-Swarm</a>: navigation candidate (GPL-3.0). <a href="https://github.com/stephansturges/OpenLander">OpenLander</a>: clearing perception (MIT). <a href="https://github.com/JuliaDi/Tree-Perching-Code">SLAP code</a>: trunk perch/recovery reference (MIT; motion capture/operator inputs). <a href="https://github.com/YassinBaraa/Diplomski_rad">Pi/Hailo branch prototype</a>: close hardware match; motion capture and unresolved license. None is bundled in this model.</p></details>
        <h3>Mission decisions</h3><ol id="forest-events"></ol>
      </aside></div>
      <footer>Fushan is one Taiwanese forest type, not a proxy for every altitude or region. The 48 × 40 m active patch, 54 major stems and dense understory are a seeded reconstruction. Surrounding trees are visual context outside the bounded flight model. Individual tree locations, support strength, wet grip and wind response are not measured. Mist is a visual sensitivity control, not a calibrated visibility model. <a href="../docs/taiwan-forest-analysis.md">Site analysis & evidence</a> · <a href="../docs/forest-navigation.md">Model contract</a></footer>
    </dialog>`;
  const el=id=>document.getElementById('forest-'+id),profile=el('profile').getContext('2d');
  let renderer=null;try{renderer=window.EagleForestRenderer.create(el('map'));}catch(error){console.warn('Forest WebGL fallback:',error.message);}
  const ctx=renderer?null:el('map').getContext('2d');
  el('renderer-state').textContent=renderer?'Perspective 3D':'2D fallback · WebGL unavailable';
  let s=F.create('branch',el('site').value),playing=false,last=null,history=[[0,s.p[2]]],renderAt=0,pov=false,dirty=true;
  for(const item of window.EagleForestSite.profile.evidence){const p=document.createElement('p'),label=document.createElement('b'),a=document.createElement('a');label.textContent=item.label+' · ';a.textContent=item.value;a.href=item.source;a.target='_blank';a.rel='noopener';p.append(label,a);el('evidence').append(p);}
  const descriptions={TRANSIT:'Following a route computed around inflated 3D obstacles.',INSPECT:'Rechecking the modeled support and its clear approach.',APPROACH:'Tracking the selected support at a slower modeled speed.',CONTACT:'Contact is injected by the model; no real sensor is connected.',VERIFY_SUPPORT:'Waiting for a synthetic support-confirmed signal before motor shutdown.',PERCHED:'Synthetic attachment confirmed. Motors modeled off for four seconds.',LANDED:'Synthetic ground support confirmed. Motors modeled off for four seconds.',RELEASE:'Motors modeled on; attachment released.',DEPART:'Retracing the checked approach corridor to clear the support.',RECOVER:'Grip failed. Motors stay on while retreating to the inspection point.',COMPLETE:'Departure completed. Replay or choose a failure scenario.',ABORTED:'Mission cannot continue. This animation stops; aircraft emergency dynamics are not simulated.'};
  const stages=['TRANSIT','INSPECT','APPROACH','CONTACT','VERIFY_SUPPORT','PERCHED','RELEASE','DEPART'];
  const initialView={yaw:0.55,pitch:0.55,zoom:1,panX:0,panY:0};
  let view={...initialView};
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  function cameraPoint(p){
    const b=s.world.bounds||F.bounds,target=el('follow').checked?s.p:[b[0]/2,b[1]/2,b[2]/2],x=p[0]-target[0],y=p[1]-target[1],z=p[2]-target[2];
    const c=Math.cos(view.yaw),a=Math.sin(view.yaw),d=-x*a+y*c;
    return [x*c+y*a,d*Math.sin(view.pitch)-z*Math.cos(view.pitch),-d*Math.cos(view.pitch)+z*Math.sin(view.pitch)];
  }
  function project(p){const q=cameraPoint(p),scale=25*view.zoom;return [450+view.panX+q[0]*scale,245+view.panY+q[1]*scale];}
  function poly(points,fill,stroke){ctx.beginPath();points.forEach((p,i)=>{const q=project(p);i?ctx.lineTo(...q):ctx.moveTo(...q);});ctx.closePath();ctx.fillStyle=fill;ctx.fill();ctx.strokeStyle=stroke;ctx.stroke();}
  function line(points,color,width=2,dash=[]){ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dash);ctx.beginPath();points.forEach((p,i)=>{const q=project(p);i?ctx.lineTo(...q):ctx.moveTo(...q);});ctx.stroke();ctx.setLineDash([]);ctx.lineWidth=1;}
  function draw(){
    dirty=false;
    const density=s.world.metrics;
    el('cover-note').textContent=density?`Modeled crown footprints cover ${(density.crownProjection*100).toFixed(0)}% of the active patch · ${density.understory} understory placements · these are reconstruction settings, not measured canopy closure.`:'Original sparse block baseline.';
    if(renderer)renderer.render(s,view,{follow:el('follow').checked,cutaway:el('cutaway').checked,collision:el('collision').checked,mist:el('mist').checked,pov});
    if(ctx){
    ctx.fillStyle='#0b1718';ctx.fillRect(0,0,900,490);ctx.font='13px monospace';
    for(let x=0;x<=22;x+=2)line([[x,0,0],[x,16,0]],'#203638',1);
    for(let y=0;y<=16;y+=2)line([[0,y,0],[22,y,0]],'#203638',1);
    line([[0,0,0],[0,0,12]],'#91a7a5',1);ctx.fillStyle='#b6cbc7';ctx.fillText('local forest · 22 × 16 × 12 m',20,467);
    // Sort every box face by camera depth so orbiting exposes all sides.
    const faces=[];
    for(const o of s.world.obstacles){
      const [x,y,z]=o.min,[X,Y,Z]=o.max,fol=o.kind==='foliage',c=fol?'#387751':'#876343';
      const polygons=[[[x,y,z],[X,y,z],[X,y,Z],[x,y,Z]],[[x,Y,z],[X,Y,z],[X,Y,Z],[x,Y,Z]],[[x,y,z],[x,Y,z],[x,Y,Z],[x,y,Z]],[[X,y,z],[X,Y,z],[X,Y,Z],[X,y,Z]],[[x,y,Z],[X,y,Z],[X,Y,Z],[x,Y,Z]],[[x,y,z],[X,y,z],[X,Y,z],[x,Y,z]]];
      for(const points of polygons)faces.push({points,c,depth:points.reduce((a,p)=>a+cameraPoint(p)[2],0)/4});
    }
    faces.sort((a,b)=>a.depth-b.depth).forEach(f=>poly(f.points,f.c+'38',f.c+'b0'));
    line(s.path,'#80e0ff',3,[7,5]);line(s.trail,'#f1f5e8',2);
    for(const c of s.world.candidates){
      const [x,y]=project(c.p);ctx.fillStyle=c===s.target?'#ffc66b':c.reason||s.failed.includes(c.id)?'#d08278':'#91b4a5';ctx.beginPath();ctx.arc(x,y,5,0,Math.PI*2);ctx.fill();ctx.fillText(c.id,x+8,y-7);
    }
    const [x,y]=project(s.p);line([[s.p[0],s.p[1],0],s.p],'#a6d6dd',1,[2,5]);
    const droneColor=s.motors?'#80e0ff':'#ffc66b',at=(dx,dy)=>[s.p[0]+dx,s.p[1]+dy,s.p[2]];
    line([at(-0.5,-0.5),at(0.5,0.5)],droneColor,3);line([at(-0.5,0.5),at(0.5,-0.5)],droneColor,3);
    for(const dx of [-0.5,0.5])for(const dy of [-0.5,0.5]){
      const ring=Array.from({length:25},(_,i)=>at(dx+0.22*Math.cos(i*Math.PI/12),dy+0.22*Math.sin(i*Math.PI/12)));line(ring,droneColor,2);
    }
    // Solid center marker stays legible from a low side view.
    ctx.fillStyle=droneColor;ctx.beginPath();ctx.arc(x,y,4,0,Math.PI*2);ctx.fill();
    for(const [p,label,color] of [[[3,0,0],'X','#efac8c'],[[0,3,0],'Y','#a9d688'],[[0,0,3],'Z','#80e0ff']]){line([[0,0,0],p],color,2);const q=project(p);ctx.fillStyle=color;ctx.fillText(label,...q);}
    ctx.fillStyle='#b6cbc7';ctx.fillText(`${Math.round(view.zoom*100)}% · ${el('follow').checked?'following drone':'orbit view'}`,20,26);
    }
    profile.fillStyle='#0b1718';profile.fillRect(0,0,900,140);profile.font='12px monospace';profile.fillStyle='#b6cbc7';profile.fillText('ALTITUDE (modeled m)',18,18);
    const maxT=Math.max(30,s.t),maxZ=Math.max(12,Math.ceil(Math.max(...history.map(p=>p[1]))/6)*6),px=t=>70+t/maxT*800,py=z=>112-z/maxZ*84;
    for(const z of [0,maxZ/3,maxZ*2/3,maxZ]){profile.strokeStyle='#243e40';profile.beginPath();profile.moveTo(70,py(z));profile.lineTo(870,py(z));profile.stroke();profile.fillText(z.toFixed(0),42,py(z)+4);}
    profile.strokeStyle='#80e0ff';profile.beginPath();history.forEach(([t,z],i)=>i?profile.lineTo(px(t),py(z)):profile.moveTo(px(t),py(z)));profile.stroke();profile.fillText('0 s',70,133);profile.fillText(maxT.toFixed(0)+' s',830,133);
    el('status').replaceChildren();
    const title=document.createElement('h3');title.textContent=s.phase.replaceAll('_',' ')+' · '+s.t.toFixed(1)+' s';
    const p=document.createElement('p');p.textContent=descriptions[s.phase];
    const data=document.createElement('p');data.className='forest-data';data.textContent=`Local xyz: ${s.p.map(v=>v.toFixed(1)).join(', ')} m\nAbove ground: ${(s.p[2]-(s.world.ground?.(s.p[0],s.p[1])||0)).toFixed(1)} m (modeled)\nMotors: ${s.motors?'ON (modeled)':'OFF (modeled)'}\nSupport: ${s.attached?'confirmed (synthetic)':'not confirmed'}`;
    el('status').append(title,p,data);
    el('stages').replaceChildren(...stages.map(stage=>{const tag=document.createElement('span');tag.textContent=stage==='PERCHED'&&s.scenario==='landing'?'LANDED':stage.replaceAll('_',' ');tag.className=stage===s.phase||(stage==='PERCHED'&&s.phase==='LANDED')?'active':'';return tag;}));
    el('candidates').replaceChildren(...s.world.candidates.map(c=>{const p=document.createElement('p');p.textContent=`${c.id}: ${s.failed.includes(c.id)?'grip failed; excluded':c.reason|| (c===s.target?'selected; modeled geometry passes':'alternate support')}`;return p;}));
    el('events').replaceChildren(...s.events.map(e=>{const li=document.createElement('li');li.textContent=e.t.toFixed(1)+' s · '+e.text;return li;}));
    el('play').textContent=playing?'Pause':s.done?'Replay mission':'Play mission';
  }
  function reset(){s=F.create(el('scenario').value,el('site').value);history=[[0,s.p[2]]];playing=false;last=null;el('provenance').textContent=el('site').value.startsWith('fushan')?(el('site').value==='fushan-ridge'?'Shorter ridge crowns; same modeled terrain and stem positions for comparison. ':'Taller valley crowns. ')+'Site evidence: TFRI + research. Local geometry: procedural.':'Original analytical boxes. No ecological or geographic reconstruction.';draw();}
  function advance(seconds){for(let i=0;i<Math.round(seconds/0.05)&&!s.done;i++){F.step(s,0.05);history.push([s.t,s.p[2]]);}dirty=true;if(s.done)playing=false;}
  el('open').onclick=()=>{el('dialog').showModal();draw();};el('close').onclick=()=>{playing=false;last=null;el('dialog').close();};
  el('dialog').addEventListener('close',()=>{playing=false;last=null;});
  el('scenario').onchange=reset;el('reset').onclick=reset;el('step').onclick=()=>{playing=false;advance(1);draw();};
  el('site').onchange=reset;for(const id of ['cutaway','collision','mist'])el(id).onchange=draw;
  el('play').onclick=()=>{if(s.done)reset();playing=!playing;last=null;draw();};
  const canvas=el('map'),pointers=new Map();let gesture=null;
  const point=e=>{const r=canvas.getBoundingClientRect();return {x:(e.clientX-r.left)*900/r.width,y:(e.clientY-r.top)*490/r.height};};
  const gestureState=()=>{const points=[...pointers.values()];if(points.length<2)return null;return {x:(points[0].x+points[1].x)/2,y:(points[0].y+points[1].y)/2,d:Math.hypot(points[0].x-points[1].x,points[0].y-points[1].y)};};
  canvas.addEventListener('pointerdown',e=>{canvas.focus({preventScroll:true});canvas.setPointerCapture(e.pointerId);pointers.set(e.pointerId,point(e));gesture=gestureState();canvas.classList.add('dragging');e.preventDefault();});
  canvas.addEventListener('pointermove',e=>{
    if(!pointers.has(e.pointerId))return;const old=pointers.get(e.pointerId),p=point(e);pointers.set(e.pointerId,p);
    pov=false;
    if(pointers.size>=2){const next=gestureState();if(gesture&&gesture.d>0){view.zoom=clamp(view.zoom*next.d/gesture.d,0.35,4);view.panX+=next.x-gesture.x;view.panY+=next.y-gesture.y;}gesture=next;}
    else if(e.shiftKey||(e.buttons&6)){view.panX+=p.x-old.x;view.panY+=p.y-old.y;}
    else{view.yaw+=(p.x-old.x)*0.006;view.pitch=clamp(view.pitch+(p.y-old.y)*0.006,0.02,Math.PI/2-0.01);}
    draw();e.preventDefault();
  });
  const release=e=>{pointers.delete(e.pointerId);gesture=gestureState();if(!pointers.size)canvas.classList.remove('dragging');};
  canvas.addEventListener('pointerup',release);canvas.addEventListener('pointercancel',release);canvas.addEventListener('lostpointercapture',release);
  canvas.addEventListener('contextmenu',e=>e.preventDefault());
  canvas.addEventListener('wheel',e=>{view.zoom=clamp(view.zoom*Math.exp(-e.deltaY*0.001),0.35,4);draw();e.preventDefault();},{passive:false});
  function resetView(){view={...initialView};el('follow').checked=false;pov=false;draw();}
  el('view-reset').onclick=resetView;el('view-orbit').onclick=resetView;
  el('view-top').onclick=()=>{pov=false;view={...initialView,yaw:0,pitch:Math.PI/2-0.01};el('follow').checked=false;draw();};
  el('view-side').onclick=()=>{pov=false;view={...initialView,yaw:0,pitch:0.02};el('follow').checked=false;draw();};
  el('view-pov').onclick=()=>{pov=!pov;draw();};
  el('follow').onchange=()=>{view.panX=0;view.panY=0;draw();};
  canvas.addEventListener('keydown',e=>{
    const moves={ArrowLeft:[-0.12,0],ArrowRight:[0.12,0],ArrowUp:[0,-0.1],ArrowDown:[0,0.1]};
    if(moves[e.key]){view.yaw+=moves[e.key][0];view.pitch=clamp(view.pitch+moves[e.key][1],0.02,Math.PI/2-0.01);}
    else if(['+','=','-','_'].includes(e.key))view.zoom=clamp(view.zoom*(e.key==='-'||e.key==='_'?0.9:1.1),0.35,4);
    else if(e.key==='Home')resetView();else return;draw();e.preventDefault();
  });
  let carry=0;
  function frame(t){if(playing&&last!==null&&el('dialog').open){carry+=Math.min((t-last)/1000,0.1)*Number(el('speed').value);while(carry>=0.05&&!s.done){advance(0.05);carry-=0.05;}}else carry=0;last=t;if(dirty&&t-renderAt>120&&el('dialog').open){draw();renderAt=t;}requestAnimationFrame(frame);}
  window.__forestDemo={get state(){return s;},get camera(){return {...view,follow:el('follow').checked};},get renderer(){return renderer?'webgl':'fallback';},advance,reset};draw();requestAnimationFrame(frame);
  if(document.body.dataset.forestStandalone==='true')el('dialog').showModal();
})();
