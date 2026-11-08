const {chromium}=require('C:/Users/ellis/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8770/simulator/forest.html');
 assert(await page.locator('#forest-dialog').evaluate(d=>d.open));
 assert.equal(await page.evaluate(()=>__forestDemo.renderer),'webgl');
 assert.equal(await page.locator('#forest-site').inputValue(),'fushan');
 assert(await page.locator('#forest-evidence').innerText().then(t=>t.includes('600–733')));
 const camera=()=>page.evaluate(()=>__forestDemo.camera);
 const canvas=page.locator('#forest-map'),box=await canvas.boundingBox();
 const startCamera=await camera(),beforeImage=await canvas.screenshot();
 await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();
 await page.mouse.move(box.x+box.width/2+90,box.y+box.height/2+25,{steps:8});await page.mouse.up();
 assert.notEqual((await camera()).yaw,startCamera.yaw);assert.notDeepEqual(await canvas.screenshot(),beforeImage);
 assert.equal(await page.evaluate(()=>__forestDemo.state.t),0,'camera drag does not advance mission');
 const beforeZoom=await camera();await page.mouse.wheel(0,-200);await page.waitForTimeout(100);assert((await camera()).zoom>beforeZoom.zoom);
 await page.keyboard.down('Shift');await page.mouse.down();await page.mouse.move(box.x+box.width/2+130,box.y+box.height/2+45,{steps:4});await page.mouse.up();await page.keyboard.up('Shift');assert.notEqual((await camera()).panX,0);
 await page.locator('#forest-view-top').click();assert((await camera()).pitch>1.5);
 await page.locator('#forest-view-side').click();assert((await camera()).pitch<0.1);
 await page.locator('#forest-follow').check();assert((await camera()).follow);
 await page.locator('#forest-view-reset').click();assert.deepEqual(await camera(),startCamera);
 for(const id of ['cutaway','collision','mist']){await page.locator('#forest-'+id).check();await page.locator('#forest-'+id).uncheck();}
 await page.locator('#forest-view-pov').click();await page.locator('#forest-view-orbit').click();
 await canvas.focus();await page.keyboard.press('ArrowLeft');assert.notEqual((await camera()).yaw,startCamera.yaw);await page.keyboard.press('Home');assert.deepEqual(await camera(),startCamera);
 await page.locator('#forest-step').click();assert.equal(await page.evaluate(()=>Math.round(__forestDemo.state.t)),1);
 await page.locator('#forest-play').click();await page.waitForFunction(()=>__forestDemo.state.t>1.2);await page.locator('#forest-play').click();
 for(const scenario of ['branch','moving','grip-failure','landing','blocked','sensor-loss']){
  await page.locator('#forest-scenario').selectOption(scenario);
  const result=await page.evaluate(()=>{__forestDemo.advance(120);return {phase:__forestDemo.state.phase,target:__forestDemo.state.target?.id};});
  assert.equal(result.phase,['blocked','sensor-loss'].includes(scenario)?'ABORTED':'COMPLETE');
 }
 await page.locator('#forest-scenario').selectOption('branch');
 await page.locator('#forest-site').selectOption('fushan-ridge');
 assert.equal(await page.evaluate(()=>__forestDemo.state.world.variant),'fushan-ridge');
 const ridge=await page.evaluate(()=>{__forestDemo.advance(120);return __forestDemo.state.phase;});assert.equal(ridge,'COMPLETE');
 await page.locator('#forest-site').selectOption('baseline');assert.equal(await page.evaluate(()=>__forestDemo.state.world.trees),undefined);
 await page.locator('#forest-site').selectOption('fushan');
 await page.locator('#forest-scenario').selectOption('branch');
 await page.evaluate(()=>__forestDemo.advance(19));
 // A step refreshes the rendered state after deterministic model advancement.
 await page.locator('#forest-step').click();
 await page.locator('#forest-dialog').evaluate(d=>d.scrollTop=0);
 await page.locator('#forest-dialog').screenshot({path:__dirname+'/../docs/images/forest-navigation.png'});
 await page.locator('#forest-cutaway').check();await canvas.screenshot({path:__dirname+'/../docs/images/forest-cutaway.png'});await page.locator('#forest-cutaway').uncheck();
 await page.locator('#forest-view-pov').click();await canvas.screenshot({path:__dirname+'/../docs/images/forest-drone-view.png'});await page.locator('#forest-view-orbit').click();
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.locator('#forest-dialog').evaluate(d=>d.scrollWidth>d.clientWidth+1),false);
 await page.locator('#forest-reset').click();await page.locator('#forest-step').click();
 // Browser-generated touch events exercise pointer capture and pinch/pan.
 const touchStart=await camera();
 await canvas.scrollIntoViewIfNeeded();const touchBox=await canvas.boundingBox();
 const client=await page.context().newCDPSession(page);
 const ty=touchBox.y+touchBox.height/2,tx=touchBox.x+touchBox.width/2;
 await client.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:tx-40,y:ty,id:1},{x:tx+40,y:ty,id:2}]});
 await client.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:tx-40,y:ty,id:1},{x:tx+65,y:ty+15,id:2}]});
 await client.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 assert((await camera()).zoom>touchStart.zoom);assert.notEqual((await camera()).panX,touchStart.panX);
 await page.locator('#forest-view-reset').click();
 await page.screenshot({path:__dirname+'/../docs/images/forest-navigation-mobile.png'});
 await page.locator('#forest-close').click();assert.equal(await page.locator('#forest-dialog').evaluate(d=>d.open),false);
 await page.locator('#forest-open').click();assert(await page.locator('#forest-dialog').evaluate(d=>d.open));
 for(const width of [320,375,414,768]){await page.setViewportSize({width,height:950});assert.equal(await page.locator('#forest-dialog').evaluate(d=>d.scrollWidth>d.clientWidth+1),false,'width '+width);}
 // Verify insertion into the existing simulator too, without continuous 3D rendering.
 await page.addInitScript(()=>{const raf=requestAnimationFrame.bind(window);window.requestAnimationFrame=cb=>/animate/.test(cb.toString())?0:raf(cb);});
 await page.goto('http://127.0.0.1:8770/simulator/',{waitUntil:'domcontentloaded',timeout:90000});
 await page.waitForFunction(()=>window.__navigationDemo&&window.__forestDemo,{timeout:60000});
 await page.locator('[data-tab="right"]').click();await page.locator('#forest-open').click();
 assert(await page.locator('#forest-dialog').evaluate(d=>d.open));
 assert.equal(await page.locator('#forest-dialog').evaluate(d=>d.scrollWidth>d.clientWidth+1),false);
 assert.deepEqual(errors,[]);await browser.close();console.log('PASS forest browser: orbit pixels, wheel zoom, shift-pan, presets, follow, keyboard, touch pinch/pan, playback, six scenarios, integration and mobile layout; screenshots saved');
})().catch(e=>{console.error(e);process.exit(1)});
