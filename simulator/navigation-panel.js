(function () {
  'use strict';
  const N = window.EagleNavigation, host = document.getElementById('navigation-demo');
  host.innerHTML = `
    <div class="phead"><h2>GPS-denied navigation</h2><span class="tagm">illustrative model</span></div>
    <p>Can Eagle still locate itself when satellite positioning fails? Play a local survey, then watch the position source change at 10 seconds.</p>
    <label for="nav-scenario">Navigation scenario</label>
    <select id="nav-scenario">
      <option value="jamming">GPS unavailable · simulated interference</option>
      <option value="canopy">Dense canopy · GPS and vision degrade</option>
      <option value="spoofing">Spoofing · inconsistent GPS rejected</option>
      <option value="backup-loss">GPS loss, then backup loss at 25 s</option>
      <option value="clear">Baseline · GPS available</option>
    </select>
    <label class="nav-origin"><input id="nav-origin" type="checkbox" checked> Known map origin before GPS loss</label>
    <div class="seg"><button id="nav-play" type="button">Play navigation</button><button id="nav-reset" type="button">Reset</button></div>
    <canvas id="nav-map" width="560" height="360" aria-label="Local survey route: green simulator truth, cyan estimated position, amber illustrative error budget"></canvas>
    <div class="hint">Local metres · green: simulator truth · cyan: estimate · amber: illustrative error budget</div>
    <div id="nav-status" role="status" aria-live="polite"></div>
    <div id="nav-fix" class="card"></div>
    <ol id="nav-events" aria-label="Navigation transitions"></ol>
    <button id="nav-sitl" type="button">Open local flight controller</button>
    <p class="hint">Optional: start simulator/flight-lab/flight_lab.py with --external-nav --port 8767 first. Actual ArduPilot + virtual external position; no camera VIO.</p>
    <dialog id="nav-flight-dialog"><button id="nav-flight-close" type="button">Close flight controller view</button><p>Local service at 127.0.0.1:8767. If the view is unavailable, start the Python service using the build notes. Closing this view does not stop a running simulated mission.</p><iframe id="nav-flight-frame" title="Actual local ArduPilot simulation"></iframe></dialog>
    <details><summary>What is jamming, and what actually works here?</summary>
      <p><b>Blockage:</b> foliage or terrain obstructs satellite signals. <b>Jamming:</b> interference prevents reception. <b>Spoofing:</b> false satellite signals can produce a plausible wrong fix. A missing GPS fix alone cannot identify the cause.</p>
      <p><b>Fallback:</b> camera features plus a synchronized IMU can estimate local motion. Inertial measurements alone drift; a magnetometer supplies heading, not position. Thermal detection and the Hailo accelerator do not supply a navigation fix by themselves.</p>
      <p><b>This panel:</b> scripted sensor availability, kinematic motion and illustrative uncertainty. The spoofing discrepancy is scripted, not a working spoof detector. It does not run VIO, simulate RF interference, avoid trees or control the perched Eagle in the 3D scene. On backup failure it freezes this demonstration; a real aircraft cannot safely hover without a suitable position reference.</p>
      <p><b>Real stack:</b> calibrated Camera Module 3 + synchronized IMU → an evaluated VIO implementation → external odometry to ArduPilot/PX4 → flight controller attitude and motor loops. Depth/range sensing, collision avoidance and validated failsafes are separate requirements.</p>
      <p><a href="https://github.com/ArduPilot/ardupilot" target="_blank" rel="noopener">ArduPilot</a> · <a href="https://github.com/rpng/open_vins" target="_blank" rel="noopener">OpenVINS candidate</a> · <a href="https://ardupilot.org/dev/docs/mavlink-nongps-position-estimation.html" target="_blank" rel="noopener">External position interface</a> · <a href="../docs/navigation-demo.md">Build notes and limits</a></p>
    </details>`;
  const el = id => document.getElementById(id), ctx = el('nav-map').getContext('2d');
  let state = N.create(), playing = false, last = null, lastText = '';
  const opts = () => ({scenario:el('nav-scenario').value, originKnown:el('nav-origin').checked});
  function draw() {
    const X = x => 65+x*12, Y = y => 310-y*12;
    ctx.clearRect(0,0,560,360); ctx.fillStyle='#08110f';ctx.fillRect(0,0,560,360);
    ctx.strokeStyle='#253b35'; ctx.fillStyle='#93a9a1'; ctx.font='13px monospace';
    for(let x=0;x<=35;x+=5) {ctx.beginPath();ctx.moveTo(X(x),25);ctx.lineTo(X(x),310);ctx.stroke();ctx.fillText(x+'',X(x)-5,335);}
    for(let y=0;y<=20;y+=5) {ctx.beginPath();ctx.moveTo(65,Y(y));ctx.lineTo(500,Y(y));ctx.stroke();ctx.fillText(y+'',30,Y(y)+4);}
    const line = (points,color,dash=[]) => {ctx.strokeStyle=color;ctx.setLineDash(dash);ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(X(p[0]),Y(p[1])):ctx.moveTo(X(p[0]),Y(p[1])));ctx.stroke();ctx.setLineDash([]);};
    line(N.route,'#64796f',[5,5]); line(state.trail,'#6fe3a5');
    ctx.strokeStyle='#ffb347';ctx.beginPath();ctx.arc(X(state.estimated[0]),Y(state.estimated[1]),state.uncertainty*12,0,Math.PI*2);ctx.stroke();
    [[state.x,state.y,'#6fe3a5'],[...state.estimated,'#80e0ff']].forEach(([x,y,c])=>{ctx.fillStyle=c;ctx.beginPath();ctx.arc(X(x),Y(y),5,0,Math.PI*2);ctx.fill();});
    const text = `${Math.floor(state.t)} s · ${state.mode}\nGPS: ${state.gps} · backup: ${state.backup}\n${state.action}\nIllustrative error budget: ${state.uncertainty.toFixed(1)} m (not measured accuracy)`;
    if (text !== lastText) {el('nav-status').textContent=text;lastText=text;}
    const ce = window.__sim?.R?.fix?.ce_m;
    el('nav-fix').textContent = `${opts().originKnown ? 'Map origin assumed known: a local estimate can be registered to that map.' : 'No known map origin: local coordinates only; no absolute latitude/longitude claim.'} The existing sensor fix${ce != null ? ' ('+ce.toFixed(1)+' m CE in the perch model)' : ''} is relative to Eagle. Eagle’s position and heading errors must also be accounted for before reporting a person on a real map. These different error metrics are not added here.`;
    el('nav-events').replaceChildren(...state.events.map(e=>{const li=document.createElement('li');li.textContent=e.t.toFixed(1)+' s · '+e.text;return li;}));
    el('nav-play').textContent=playing?'Pause navigation':state.stopped||state.complete?'Replay navigation':'Play navigation';
  }
  function reset(){state=N.create();playing=false;last=null;draw();}
  el('nav-play').onclick=()=>{if(state.stopped||state.complete)reset();playing=!playing;last=null;draw();};
  el('nav-reset').onclick=reset;el('nav-scenario').onchange=reset;el('nav-origin').onchange=draw;
  el('nav-sitl').onclick=()=>{el('nav-flight-frame').src='http://127.0.0.1:8767';el('nav-flight-dialog').showModal();};
  el('nav-flight-close').onclick=()=>el('nav-flight-dialog').close();
  let renderAt=0;
  function frame(t){if(playing&&last!==null){state=N.step(state,Math.min((t-last)/1000,0.1),opts());if(state.stopped||state.complete)playing=false;}last=t;if(t-renderAt>200){draw();renderAt=t;}requestAnimationFrame(frame);}
  window.__navigationDemo={get state(){return state;},reset};
  draw();requestAnimationFrame(frame);
})();
