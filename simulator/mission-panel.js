/* One scene, one aircraft state, one mission clock. The main renderer owns every POV. */
(function(){
 'use strict';
 const H=window.__eagleHost,M=window.EagleModel,U=window.EagleMission,T=window.THREE;
 if(!H||!M||!U)return;
 const nav=document.getElementById('navigation-demo');
 nav.innerHTML=`<div class="phead"><h2>Eagle mission</h2><span class="tagm">one shared simulation</span></div>
 <p class="hint">Navigate → verify support → observe/listen → relay or store → depart. Camera, route, sensor view and map use the same aircraft and forest. Main view uses inspection lighting; sensor calculations use the selected time and weather.</p>
 <label>Forest<select id="mission-site"><option value="fushan">Fushan · dense valley forest</option><option value="fushan-ridge">Fushan · dense exposed ridge</option></select></label>
 <label>Flight case<select id="mission-case"><option value="branch">Branch perch</option><option value="moving">Moving branch · alternate support</option><option value="grip-failure">Grip failure · retreat and retry</option><option value="landing">Clearing landing</option><option value="blocked">No validated corridor</option><option value="sensor-loss">Pose/depth lost at 5 s</option></select></label>
 <label>Playback speed<select id="mission-speed"><option value="1">1×</option><option value="4" selected>4×</option><option value="8">8×</option><option value="16">16×</option></select></label>
 <div class="mission-tools"><button id="mission-reset">Reset mission</button><button id="mission-step">Step 1 second</button><button id="mission-export">Export run</button></div>
 <div class="mission-layers"><label><input id="mission-cutaway" type="checkbox"> Canopy cutaway</label><label><input id="mission-collision" type="checkbox"> Collision envelopes</label></div>
 <p id="mission-status" role="status" aria-live="polite"></p><p id="mission-density" class="hint"></p>
 <details><summary>What is connected?</summary><p class="hint">Computed forest flight and attachment now gate the existing Eagle observation and radio model. Thermal, visible and ID views render this same forest. Pose/contact, sensors and transport remain synthetic or modeled. ArduPilot SITL is still a separate hardware-interface test, not the controller for this mission. Scene inspection lighting stays readable; sensor calculations use the selected time and weather.</p><a href="../docs/unified-mission.md">Integration details</a> · <a href="../docs/taiwan-forest-analysis.md">Fushan evidence</a></details>`;
 const el=id=>document.getElementById('mission-'+id),run=document.getElementById('run');
 const viewGroup=document.querySelector('.viewbtns');viewGroup.innerHTML=`<label for="mission-pov">View</label><select id="mission-pov" aria-label="Mission camera viewpoint"><option value="chase">Chase · behind Eagle</option><option value="nose">Nose · flight direction</option><option value="gimbal">Gimbal · person-facing</option><option value="left">Left shoulder</option><option value="right">Right shoulder</option><option value="front">Front · looking back</option><option value="overhead">Overhead · follow</option><option value="belly">Belly · ground below</option><option value="support">Perch approach camera</option><option value="operator">Person’s viewpoint</option><option value="relay">High overview</option><option value="orbit">Free orbit · drag to inspect</option></select>`;
 document.querySelector('.brand h1').textContent='Eagle · unified forest mission';
 document.querySelector('.mapcap').textContent='Mission map · local metres · delivered observations only';
 document.querySelector('#overlays').hidden=true;
 // A chosen perch now comes from the flight planner, not the old independent perch selector.
 const oldPerch=document.getElementById('c-perch');if(oldPerch)oldPerch.closest('.ctl')?.setAttribute('hidden','');
 let state,playing=false,view='chase',layer,uiAge=0,lastPhase='',heading=new T.Vector3(1,0,0),forward=new T.Vector3(1,0,0),lastPosition=null;
 const to=p=>new T.Vector3(p[0],p[2],-p[1]);
 const options=()=>({cutaway:el('cutaway').checked,collision:el('collision').checked});
 function reset(){
  playing=false;state=U.create(M,H.settingsForPatch(),{site:el('site').value,scenario:el('case').value});lastPosition=null;heading.set(1,0,0);lastPhase='';
  if(!layer){layer=window.EagleForestRenderer.create(H.canvas,{renderer:H.renderer});H.attach(layer);}
  H.sync(state,layer,options());H.publish(state);setView(view);draw();
 }
 function setView(value){
  const aliases={eagle:'gimbal',top:'overhead'};view=aliases[value]||value;
  if(![...el('pov').options].some(o=>o.value===view))view='chase';el('pov').value=view;H.cameraMode(view);
  if(view==='orbit')H.orbitTarget(to(state.forest.p),true);updateCamera();H.render();
 }
 function updateCamera(){
  if(!state)return;const f=state.forest,p=to(f.p),next=f.phase==='TRANSIT'?f.path[f.next]:['RECOVER','DEPART'].includes(f.phase)?f.target?.approach:f.target?.p;
  const delta=next?to(next).sub(p):lastPosition?p.clone().sub(lastPosition):heading.clone();
  if(delta.length()>.08){forward.copy(delta).normalize();delta.y=0;if(delta.length()>.01)heading.copy(delta.normalize());}
  lastPosition=p.clone();const side=new T.Vector3(-heading.z,0,heading.x),up=new T.Vector3(0,1,0),person=H.person.clone().addScaledVector(up,state.config.opPosture==='standing'?1.1:state.config.opPosture==='sitting'?.7:.25);
  if(view==='orbit'){H.controls.update();return;}
  let eye,target=p.clone().addScaledVector(up,.3),fov=55;
  if(view==='nose'){eye=p.clone().addScaledVector(up,.35);target=eye.clone().addScaledVector(forward,8);fov=72;}
  else if(view==='gimbal'){eye=p.clone().addScaledVector(up,.35);target=person;const hfov=M.PARAMS.thermal.cameras[state.config.camera]?.hfov||56;fov=2*Math.atan(Math.tan(hfov*Math.PI/360)/H.camera.aspect)*180/Math.PI;}
  else if(view==='left'||view==='right'){eye=p.clone().addScaledVector(side,view==='left'?5:-5).addScaledVector(heading,-2).addScaledVector(up,1.8);}
  else if(view==='front'){eye=p.clone().addScaledVector(heading,6).addScaledVector(up,1.2);}
  else if(view==='overhead'){eye=p.clone().addScaledVector(up,22).addScaledVector(heading,-.05);fov=60;}
  else if(view==='belly'){eye=p.clone().addScaledVector(up,-.15);target=p.clone().addScaledVector(up,-8).addScaledVector(heading,.01);fov=70;}
  else if(view==='support'){const c=to(f.target?.p||f.p);eye=c.clone().addScaledVector(side,7).addScaledVector(heading,-6).addScaledVector(up,3);target=c.clone().lerp(p,.35);}
  else if(view==='operator'){eye=person;target=p.clone().addScaledVector(up,.35);fov=65;}
  else if(view==='relay'){eye=p.clone().add(new T.Vector3(20,34,24));target=p.clone().lerp(person,.4);fov=65;}
  else eye=p.clone().addScaledVector(heading,-7).addScaledVector(up,3);
  H.look(eye,target,fov);
 }
 function sync(){H.sync(state,layer,options());updateCamera();}
 function advance(seconds){
  for(let left=seconds;left>1e-8&&!state.done;left-=.05)U.step(state,Math.min(.05,left));
  if(state.done)playing=false;sync();H.publish(state);draw();H.render();
 }
 function toggle(){if(state.done)reset();if(state.done){draw();return;}playing=!playing;draw();}
 function frame(dt){
  if(playing){for(let left=dt*Number(el('speed').value);left>1e-8&&!state.done;left-=.05)U.step(state,Math.min(.05,left));if(state.done)playing=false;}
  sync();uiAge+=dt;
  if(uiAge>.2||lastPhase!==state.phase){uiAge=0;lastPhase=state.phase;H.publish(state);draw();}
 }
 function draw(){
  const f=state.forest;run.disabled=false;run.textContent=playing?'Pause mission':state.done?'Replay mission':state.t?'Resume mission':'Run mission';
  el('status').textContent=`${state.phase.replaceAll('_',' ')} · ${state.t.toFixed(1)} s\nEagle xyz: ${f.p.map(v=>v.toFixed(1)).join(', ')} m\nSupport: ${f.attached?'verified (synthetic)':'not attached'} · propulsion ${f.motors?'on':'off'}\n${state.delivered?'Observation delivered to coordinator':state.stored?'Observation stored on Eagle':!state.observed?'Observation and relay wait for support verification':state.result?.find.status+' · identity UNVERIFIED'}`;
  el('density').textContent=`Fushan reconstruction · ${(f.world.metrics.crownProjection*100).toFixed(0)}% modeled crown footprints · ${f.world.ferns.length} understory placements. Full synthetic map and pose; no onboard VIO.`;
  const stages=['TRANSIT','INSPECT','APPROACH','CONTACT','VERIFY_SUPPORT','OBSERVE','RELAY','MARKER','RELEASE','DEPART','COMPLETE'],active=stages.indexOf(state.phase);
  document.getElementById('steps').replaceChildren(...stages.map((stage,i)=>{const li=document.createElement('li');li.textContent=stage.replaceAll('_',' ');li.className=stage===state.phase?'on':active>i?'done':'';return li;}));
  const log=document.getElementById('log');log.replaceChildren(...state.events.map(e=>{const d=document.createElement('div');d.className='l';d.textContent=`${e.t.toFixed(1)} s · ${e.stage} · ${e.note}`;return d;}));log.scrollTop=log.scrollHeight;drawMap();
 }
 function drawMap(){
  if(!state)return;const canvas=document.getElementById('map'),c=canvas.getContext('2d'),w=canvas.width,scale=(w-36)/48,xy=p=>[18+p[0]*scale,18+(40-p[1])*scale];
  c.fillStyle='#10211c';c.fillRect(0,0,w,w);c.strokeStyle='#345147';c.lineWidth=1;
  for(let x=0;x<=48;x+=8){const a=xy([x,0]),b=xy([x,40]);c.beginPath();c.moveTo(...a);c.lineTo(...b);c.stroke();}
  for(const t of state.forest.world.trees){const a=xy([t.x,t.y]);c.fillStyle='#294432';c.beginPath();c.arc(...a,t.crownScale[0]*scale,0,Math.PI*2);c.fill();}
  c.strokeStyle='#80e0ff';c.beginPath();state.forest.trail.forEach((p,i)=>{const a=xy(p);i?c.lineTo(...a):c.moveTo(...a);});c.stroke();
  const p=xy(state.forest.p);c.fillStyle='#80e0ff';c.beginPath();c.arc(...p,4,0,Math.PI*2);c.fill();c.font='12px monospace';c.fillText('EAGLE',p[0]+7,p[1]-6);
  if(state.delivered){const q=xy(state.reportedPosition);c.strokeStyle='#ffcf6c';c.beginPath();c.arc(...q,Math.max(2,state.result.fix.ce_m*scale),0,Math.PI*2);c.stroke();c.strokeRect(q[0]-5,q[1]-5,10,10);c.fillStyle='#ffcf6c';c.fillText('PERSON · ID ?',q[0]-30,q[1]+19);}
  c.fillStyle='#b0c2b5';c.fillText('local 48 × 40 m · model',12,w-15);
 }
 el('site').onchange=reset;el('case').onchange=reset;el('reset').onclick=reset;el('pov').onchange=()=>setView(el('pov').value);
 el('step').onclick=()=>{playing=false;advance(1);};for(const id of ['cutaway','collision'])el(id).onchange=()=>{sync();H.render();};
 el('export').onclick=()=>{const blob=new Blob([JSON.stringify(U.serializable(state),null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='eagle-unified-mission.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 H.canvas.addEventListener('pointerdown',()=>{if(view!=='orbit'){view='orbit';el('pov').value=view;H.cameraMode(view);H.orbitTarget(to(state.forest.p),false);}},{capture:true});
 const api={get state(){return state;},get playing(){return playing;},get view(){return view;},toggle,frame,advance,reset,setView,updateCamera,drawMap,settingsChanged:reset};
 window.__eagleMission=api;reset();
})();
