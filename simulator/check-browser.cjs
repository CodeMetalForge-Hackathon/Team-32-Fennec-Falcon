const {chromium} = require('C:/Users/ellis/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true,
    args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page = await browser.newPage({viewport:{width:1440,height:1000}, reducedMotion:'reduce'});
  const errors=[];page.on('pageerror', e=>errors.push(e.message));
  // Avoid continuous software 3D rendering, but retain scheduled UI/mission callbacks.
  await page.addInitScript(()=>{const raf=requestAnimationFrame.bind(window);window.requestAnimationFrame=cb=>{
    if(/animate/.test(cb.toString())) { window.__renderOnce=()=>cb(performance.now()); return 0; }
    return raf(cb);
  };});
  await page.goto(process.env.EAGLE_TEST_URL || 'http://127.0.0.1:8771/simulator/', {waitUntil:'domcontentloaded',timeout:90000});
  await page.waitForFunction(()=>window.__sim?.ready && window.__navigationDemo, {timeout:60000});
  await page.evaluate(()=>window.__sim.quality('low'));
  if(await page.locator('#c-signal').inputValue() !== 'none')throw Error('default person is not silent');
  const body=await page.locator('body').innerText();
  if(/carries the tag|tag buzzer|comms pellet|cooperative swir/i.test(body))throw Error('obsolete person equipment in UI');
  if(!body.includes('UNVERIFIED'))throw Error('identity state missing');
  const out=name=>path.join(__dirname,'../docs/images',name);
  const snap=async name=>{await page.evaluate(()=>window.__renderOnce?.());await page.screenshot({path:out(name)});};
  await snap('overview-night.jpg');
  await page.locator('#run').click();
  await page.waitForFunction(()=>document.querySelector('#run').textContent==='Run again',{timeout:25000});
  await snap('mission-complete.jpg');
  await page.evaluate(()=>window.__sim.cam('eagle'));
  await snap('eagle-eye-view.jpg');
  await page.evaluate(()=>window.__sim.preset('cicadaDay'));
  if(await page.evaluate(()=>window.__sim.R.find.status)!=='NOT FOUND')throw Error('silent afternoon incorrectly found');
  await snap('cicada-day-thermal-lost.jpg');
  await page.evaluate(()=>window.__sim.preset('night'));
  await page.locator('#navigation-demo').screenshot({path:out('navigation-demo.png')});
  await page.setViewportSize({width:390,height:844});
  await page.locator('[data-tab="right"]').click();
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('mobile overflow');
  await snap('phone-layout.jpg');
  if(errors.length)throw Error(errors.join('\n'));
  await browser.close();
  console.log('PASS browser: sensor-only UI, silent default, mission playback, daytime failure, navigation panel and mobile layout; refreshed screenshots');
})().catch(e=>{console.error(e);process.exit(1)});
