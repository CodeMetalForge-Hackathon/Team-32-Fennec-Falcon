const {chromium}=require('C:/Users/ellis/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8770/simulator/forest.html');
 assert(await page.locator('#forest-dialog').evaluate(d=>d.open));
 await page.locator('#forest-step').click();assert.equal(await page.evaluate(()=>Math.round(__forestDemo.state.t)),1);
 await page.locator('#forest-play').click();await page.waitForFunction(()=>__forestDemo.state.t>1.2);await page.locator('#forest-play').click();
 for(const scenario of ['branch','moving','grip-failure','landing','blocked','sensor-loss']){
  await page.locator('#forest-scenario').selectOption(scenario);
  const result=await page.evaluate(()=>{__forestDemo.advance(120);return {phase:__forestDemo.state.phase,target:__forestDemo.state.target?.id};});
  assert.equal(result.phase,['blocked','sensor-loss'].includes(scenario)?'ABORTED':'COMPLETE');
 }
 await page.locator('#forest-scenario').selectOption('branch');
 await page.evaluate(()=>__forestDemo.advance(19));
 // A step refreshes the rendered state after deterministic model advancement.
 await page.locator('#forest-step').click();
 await page.locator('#forest-dialog').screenshot({path:__dirname+'/../docs/images/forest-navigation.png'});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.locator('#forest-dialog').evaluate(d=>d.scrollWidth>d.clientWidth+1),false);
 await page.locator('#forest-reset').click();await page.locator('#forest-step').click();
 await page.screenshot({path:__dirname+'/../docs/images/forest-navigation-mobile.png'});
 await page.locator('#forest-close').click();assert.equal(await page.locator('#forest-dialog').evaluate(d=>d.open),false);
 await page.locator('#forest-open').click();assert(await page.locator('#forest-dialog').evaluate(d=>d.open));
 // Verify insertion into the existing simulator too, without continuous 3D rendering.
 await page.addInitScript(()=>{const raf=requestAnimationFrame.bind(window);window.requestAnimationFrame=cb=>/animate/.test(cb.toString())?0:raf(cb);});
 await page.goto('http://127.0.0.1:8770/simulator/',{waitUntil:'domcontentloaded',timeout:90000});
 await page.waitForFunction(()=>window.__navigationDemo&&window.__forestDemo,{timeout:60000});
 await page.locator('[data-tab="right"]').click();await page.locator('#forest-open').click();
 assert(await page.locator('#forest-dialog').evaluate(d=>d.open));
 assert.equal(await page.locator('#forest-dialog').evaluate(d=>d.scrollWidth>d.clientWidth+1),false);
 assert.deepEqual(errors,[]);await browser.close();console.log('PASS forest browser: playback, stepping, six scenarios, reset, modal, main-simulator integration and mobile overflow; screenshots saved');
})().catch(e=>{console.error(e);process.exit(1)});
