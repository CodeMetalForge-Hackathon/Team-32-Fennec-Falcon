const assert=require('node:assert/strict'),path=require('node:path'),os=require('node:os');
let playwright;try{playwright=require('playwright');}catch{playwright=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));}const {chromium}=playwright;
(async()=>{
 const b=await chromium.launch({channel:'chrome',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 try{
 const p=await b.newPage({viewport:{width:1500,height:1100}}),errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.addInitScript(()=>{const raf=requestAnimationFrame.bind(window);requestAnimationFrame=cb=>/animate/.test(cb.toString())?0:raf(cb);});
 await p.goto('http://127.0.0.1:8770/simulator/forest.html?view=dense',{waitUntil:'domcontentloaded',timeout:90000});
 await p.waitForFunction(()=>window.__eagleMission&&window.__sim?.ready,{timeout:60000});
 assert(!p.url().includes('forest.html'),'old forest route redirects to unified mission');
 assert.equal(await p.locator('#forest-dialog').count(),0);assert.equal(await p.locator('#nav-play').count(),0);
 assert.equal(await p.locator('#c-signal').inputValue(),'none');
 await p.evaluate(()=>__sim.quality('low'));
 for(const view of ['nose','gimbal','chase','left','right','front','overhead','belly','support','operator','relay','orbit']){
  await p.locator('#mission-pov').selectOption(view);
  assert.equal(await p.evaluate(()=>__eagleMission.view),view);
  const cam=await p.evaluate(()=>__eagleHost.camera.position.toArray());assert(cam.every(Number.isFinite));
 }
 await p.locator('#mission-pov').selectOption('nose');await p.evaluate(()=>__eagleMission.frame(.05));assert.equal(await p.evaluate(()=>__eagleHost.aircraftVisible),false);
 await p.locator('#mission-pov').selectOption('chase');
 await p.evaluate(()=>__eagleMission.advance(12));
 const shared=await p.evaluate(()=>({flight:__eagleMission.state.forest.p,actor:__eagleHost.pose.toArray(),result:__sim.R.find.status}));
 assert(Math.abs(shared.flight[0]-shared.actor[0])<1e-8);assert(Math.abs(shared.flight[2]-shared.actor[1])<1e-8);assert.equal(shared.result,'IN TRANSIT');
 const cv=p.locator('#gl'),box=await cv.boundingBox(),t=await p.evaluate(()=>__eagleMission.state.t);
 await p.mouse.move(box.x+box.width*.5,box.y+box.height*.35);await p.mouse.down();await p.mouse.move(box.x+box.width*.55,box.y+box.height*.4,{steps:3});await p.mouse.up();assert.equal(await p.evaluate(()=>__eagleMission.view),'orbit');assert.equal(await p.evaluate(()=>__eagleMission.state.t),t);
 await p.locator('#mission-step').click();assert.equal(Math.round(await p.evaluate(()=>__eagleMission.state.t)),13);
 await p.locator('#run').click();assert(await p.evaluate(()=>__eagleMission.playing));await p.evaluate(()=>__eagleMission.frame(.05));assert((await p.evaluate(()=>__eagleMission.state.t))>13);await p.locator('#run').click();assert(!await p.evaluate(()=>__eagleMission.playing));
 for(const id of ['cutaway','collision']){await p.locator('#mission-'+id).check();await p.locator('#mission-'+id).uncheck();}
 await p.evaluate(()=>__sim.apply({signal:'softWhistle',opDist_m:10}));await p.locator('#mission-case').selectOption('moving');
 await p.evaluate(()=>__eagleMission.advance(60));assert(await p.evaluate(()=>__eagleMission.state.observing));
 assert(await p.evaluate(()=>__eagleMission.state.forest.attached&&!__eagleMission.state.forest.motors));
 await p.evaluate(()=>__sim.pip('thermal'));await p.evaluate(()=>__eagleHost.pip());
 assert.equal(await p.evaluate(()=>__sim.R.find.identity),'UNVERIFIED');
 await p.evaluate(()=>__eagleMission.advance(100));assert(await p.evaluate(()=>__eagleMission.state.delivered&&__eagleMission.state.done));
 assert((await p.locator('#log').innerText()).includes('MARKER'));
 await p.locator('#mission-case').selectOption('blocked');assert.equal(await p.evaluate(()=>__eagleMission.state.phase),'ABORTED');assert(!await p.evaluate(()=>__eagleMission.state.observed));
 await p.locator('#mission-case').selectOption('sensor-loss');await p.evaluate(()=>__eagleMission.advance(15));assert.equal(await p.evaluate(()=>__eagleMission.state.phase),'ABORTED');
 await p.locator('#mission-case').selectOption('branch');await p.evaluate(()=>__eagleMission.advance(20));await p.locator('#mission-pov').selectOption('chase');await p.evaluate(()=>{__eagleHost.pip();__eagleHost.render();});
 await p.screenshot({path:path.join(__dirname,'../docs/images/unified-mission.jpg'),quality:78});
 await p.locator('#mission-pov').selectOption('nose');await cv.screenshot({path:path.join(__dirname,'../docs/images/unified-nose-view.jpg'),quality:78});
 for(const width of [320,375,414,768]){await p.setViewportSize({width,height:950});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'overflow '+width);}
 assert.deepEqual(errors,[]);console.log('PASS unified browser: old-route redirect, one scene/aircraft/clock, twelve POVs, drag orbit, playback, supported observation, shared thermal view, marker delivery, aborts and mobile widths');
 }finally{await b.close();}
})().catch(e=>{console.error(e);process.exit(1)});
