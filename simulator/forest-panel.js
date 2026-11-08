(function(){
  'use strict';
  const F=window.EagleForest, host=document.createElement('section');host.id='forest-demo';
  (document.getElementById('navigation-demo')||document.body).append(host);
  host.innerHTML=`<div class="phead"><h2>Forest navigation & perching</h2><span class="tagm">new · simulation</span></div>
    <p>Follow Eagle between obstacles, inspect a branch, verify its grip and depart. See what is implemented and what still needs real sensors.</p>
    <button id="forest-open">Open forest lab</button>
    <dialog id="forest-dialog" aria-labelledby="forest-title">
      <header><div><span class="forest-eyebrow">EAGLE / AUTONOMY LAB</span><h2 id="forest-title">From forest flight to a verified perch</h2></div><button id="forest-close">Close</button></header>
      <p class="forest-intro">Working 3D route search + a simulated landing/perching sequence. All geometry, pose and contact signals are synthetic. This lab does not command the aircraft or the ArduPilot demo.</p>
      <div class="forest-layout"><div class="forest-visual">
        <div class="forest-controls"><label>Try a scenario<select id="forest-scenario">
          <option value="branch">Branch perch · nominal</option><option value="moving">Moving branch · choose another</option>
          <option value="grip-failure">Grip failure · retreat and retry</option><option value="landing">Ground landing · clearing</option>
          <option value="blocked">Blocked canopy · no route</option><option value="sensor-loss">Lose pose / depth · abort</option>
        </select></label><label>Playback<select id="forest-speed"><option value="1">1×</option><option value="4" selected>4×</option><option value="8">8×</option></select></label></div>
        <div class="seg"><button id="forest-play">Play mission</button><button id="forest-step">Step 1 second</button><button id="forest-reset">Reset</button></div>
        <canvas id="forest-map" width="900" height="490" aria-label="Oblique 3D synthetic forest map with planned route, obstacles, candidates and drone"></canvas>
        <p class="hint">Oblique 3D view · cyan: computed route · white: flown trail · gold: selected support · green blocks: foliage · brown blocks: wood. Full map supplied; hidden-obstacle sensing is not simulated.</p>
        <canvas id="forest-profile" width="900" height="140" aria-label="Flight altitude over elapsed simulated time"></canvas>
        <div id="forest-stages" aria-label="Mission stages"></div>
      </div><aside>
        <div id="forest-status" role="status" aria-live="polite"></div>
        <h3>Why this support?</h3><div id="forest-candidates"></div>
        <h3>Where we are now</h3>
        <ul class="forest-readiness"><li><b>Runs here</b> — voxel A* route search, aircraft clearance, approach checks, stage transitions and injected recovery.</li><li><b>Runs separately</b> — ArduPilot SITL with virtual external position. Its controller is not driving this forest model.</li><li><b>Still to integrate</b> — onboard VIO, measured depth, EGO-Swarm, branch perception, gripper/contact sensing and vehicle-specific recovery.</li></ul>
        <details><summary>Open-source building blocks</summary><p><a href="https://github.com/ZJU-FAST-Lab/ego-planner-swarm">EGO-Swarm</a>: navigation candidate (GPL-3.0). <a href="https://github.com/stephansturges/OpenLander">OpenLander</a>: clearing perception (MIT). <a href="https://github.com/JuliaDi/Tree-Perching-Code">SLAP code</a>: trunk perch/recovery reference (MIT; motion capture/operator inputs). <a href="https://github.com/YassinBaraa/Diplomski_rad">Pi/Hailo branch prototype</a>: close hardware match; motion capture and unresolved license. None is bundled in this model.</p></details>
        <h3>Mission decisions</h3><ol id="forest-events"></ol>
      </aside></div>
      <footer>Canopy leaves are not a landing pad. Branch strength and grip are assumed in the nominal model, not inferred from images. Dimensions and timing are illustrative, not hardware limits. <a href="../docs/forest-navigation.md">Model details and next integration</a></footer>
    </dialog>`;
  const el=id=>document.getElementById('forest-'+id),ctx=el('map').getContext('2d'),profile=el('profile').getContext('2d');
  let s=F.create(),playing=false,last=null,history=[[0,3]],renderAt=0;
  const descriptions={TRANSIT:'Following a route computed around inflated 3D obstacles.',INSPECT:'Rechecking the modeled support and its clear approach.',APPROACH:'Tracking the selected support at a slower modeled speed.',CONTACT:'Contact is injected by the model; no real sensor is connected.',VERIFY_SUPPORT:'Waiting for a synthetic support-confirmed signal before motor shutdown.',PERCHED:'Synthetic attachment confirmed. Motors modeled off for four seconds.',LANDED:'Synthetic ground support confirmed. Motors modeled off for four seconds.',RELEASE:'Motors modeled on; attachment released.',DEPART:'Retracing the checked approach corridor to clear the support.',RECOVER:'Grip failed. Motors stay on while retreating to the inspection point.',COMPLETE:'Departure completed. Replay or choose a failure scenario.',ABORTED:'Mission cannot continue. This animation stops; aircraft emergency dynamics are not simulated.'};
  const stages=['TRANSIT','INSPECT','APPROACH','CONTACT','VERIFY_SUPPORT','PERCHED','RELEASE','DEPART'];
  function project(p){return [110+p[0]*24+p[1]*14,425+p[1]*4-p[0]*4-p[2]*23];}
  function poly(points,fill,stroke){ctx.beginPath();points.forEach((p,i)=>{const q=project(p);i?ctx.lineTo(...q):ctx.moveTo(...q);});ctx.closePath();ctx.fillStyle=fill;ctx.fill();ctx.strokeStyle=stroke;ctx.stroke();}
  function line(points,color,width=2,dash=[]){ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dash);ctx.beginPath();points.forEach((p,i)=>{const q=project(p);i?ctx.lineTo(...q):ctx.moveTo(...q);});ctx.stroke();ctx.setLineDash([]);ctx.lineWidth=1;}
  function draw(){
    ctx.fillStyle='#0b1718';ctx.fillRect(0,0,900,490);ctx.font='13px monospace';
    for(let x=0;x<=22;x+=2)line([[x,0,0],[x,16,0]],'#203638',1);
    for(let y=0;y<=16;y+=2)line([[0,y,0],[22,y,0]],'#203638',1);
    line([[0,0,0],[0,0,12]],'#91a7a5',1);ctx.fillStyle='#b6cbc7';ctx.fillText('height 12 m',18,126);ctx.fillText('local forest · 22 × 16 m',25,467);
    for(const o of s.world.obstacles){
      const [x,y,z]=o.min,[X,Y,Z]=o.max,fol=o.kind==='foliage',c=fol?'#387751':'#876343';
      poly([[x,y,z],[X,y,z],[X,y,Z],[x,y,Z]],c+'55',c);
      poly([[X,y,z],[X,Y,z],[X,Y,Z],[X,y,Z]],c+'77',c);
      poly([[x,y,Z],[X,y,Z],[X,Y,Z],[x,Y,Z]],c+'99',c);
    }
    line(s.path,'#80e0ff',3,[7,5]);line(s.trail,'#f1f5e8',2);
    for(const c of s.world.candidates){
      const [x,y]=project(c.p);ctx.fillStyle=c===s.target?'#ffc66b':c.reason||s.failed.includes(c.id)?'#d08278':'#91b4a5';ctx.beginPath();ctx.arc(x,y,5,0,Math.PI*2);ctx.fill();ctx.fillText(c.id,x+8,y-7);
    }
    const [x,y]=project(s.p);line([[s.p[0],s.p[1],0],s.p],'#a6d6dd',1,[2,5]);
    ctx.strokeStyle=s.motors?'#80e0ff':'#ffc66b';ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(x-12,y-6);ctx.lineTo(x+12,y+6);ctx.moveTo(x-12,y+6);ctx.lineTo(x+12,y-6);ctx.stroke();ctx.lineWidth=1;
    for(const dx of [-12,12])for(const dy of [-6,6]){ctx.beginPath();ctx.arc(x+dx,y+dy,5,0,Math.PI*2);ctx.stroke();}
    profile.fillStyle='#0b1718';profile.fillRect(0,0,900,140);profile.font='12px monospace';profile.fillStyle='#b6cbc7';profile.fillText('ALTITUDE (modeled m)',18,18);
    const maxT=Math.max(30,s.t),px=t=>70+t/maxT*800,py=z=>112-z*7;
    for(const z of [0,4,8,12]){profile.strokeStyle='#243e40';profile.beginPath();profile.moveTo(70,py(z));profile.lineTo(870,py(z));profile.stroke();profile.fillText(z+'',42,py(z)+4);}
    profile.strokeStyle='#80e0ff';profile.beginPath();history.forEach(([t,z],i)=>i?profile.lineTo(px(t),py(z)):profile.moveTo(px(t),py(z)));profile.stroke();profile.fillText('0 s',70,133);profile.fillText(maxT.toFixed(0)+' s',830,133);
    el('status').replaceChildren();
    const title=document.createElement('h3');title.textContent=s.phase.replaceAll('_',' ')+' · '+s.t.toFixed(1)+' s';
    const p=document.createElement('p');p.textContent=descriptions[s.phase];
    const data=document.createElement('p');data.className='forest-data';data.textContent=`Local xyz: ${s.p.map(v=>v.toFixed(1)).join(', ')} m\nMotors: ${s.motors?'ON (modeled)':'OFF (modeled)'}\nSupport: ${s.attached?'confirmed (synthetic)':'not confirmed'}`;
    el('status').append(title,p,data);
    el('stages').replaceChildren(...stages.map(stage=>{const tag=document.createElement('span');tag.textContent=stage==='PERCHED'&&s.scenario==='landing'?'LANDED':stage.replaceAll('_',' ');tag.className=stage===s.phase||(stage==='PERCHED'&&s.phase==='LANDED')?'active':'';return tag;}));
    el('candidates').replaceChildren(...s.world.candidates.map(c=>{const p=document.createElement('p');p.textContent=`${c.id}: ${s.failed.includes(c.id)?'grip failed; excluded':c.reason|| (c===s.target?'selected; modeled geometry passes':'alternate support')}`;return p;}));
    el('events').replaceChildren(...s.events.map(e=>{const li=document.createElement('li');li.textContent=e.t.toFixed(1)+' s · '+e.text;return li;}));
    el('play').textContent=playing?'Pause':s.done?'Replay mission':'Play mission';
  }
  function reset(){s=F.create(el('scenario').value);history=[[0,3]];playing=false;last=null;draw();}
  function advance(seconds){for(let i=0;i<Math.round(seconds/0.05)&&!s.done;i++){F.step(s,0.05);history.push([s.t,s.p[2]]);}if(s.done)playing=false;}
  el('open').onclick=()=>{el('dialog').showModal();draw();};el('close').onclick=()=>{playing=false;last=null;el('dialog').close();};
  el('dialog').addEventListener('close',()=>{playing=false;last=null;});
  el('scenario').onchange=reset;el('reset').onclick=reset;el('step').onclick=()=>{playing=false;advance(1);draw();};
  el('play').onclick=()=>{if(s.done)reset();playing=!playing;last=null;draw();};
  let carry=0;
  function frame(t){if(playing&&last!==null&&el('dialog').open){carry+=Math.min((t-last)/1000,0.1)*Number(el('speed').value);while(carry>=0.05&&!s.done){advance(0.05);carry-=0.05;}}else carry=0;last=t;if(t-renderAt>120&&el('dialog').open){draw();renderAt=t;}requestAnimationFrame(frame);}
  window.__forestDemo={get state(){return s;},advance,reset};draw();requestAnimationFrame(frame);
  if(document.body.dataset.forestStandalone==='true')el('dialog').showModal();
})();
