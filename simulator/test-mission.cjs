const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const U=require('./mission-model.js'),F=require('./forest-model.js');
const sandbox={module:{exports:{}}};vm.runInNewContext(fs.readFileSync(__dirname+'/index.html','utf8').match(/<script id="eagle-model">([\s\S]*?)<\/script>/)[1],sandbox);const M=sandbox.module.exports;
function run(settings={},options={},seconds=220){
 const s=U.create(M,settings,options);let verified=false;
 for(let i=0;i<seconds*10&&!s.done;i++){
  U.step(s,.1);if(s.forest.attached)verified=true;
  assert(F.free(s.forest.p,s.forest.world));
  if(s.observed)assert(verified,'observation requires earlier support verification');
  if(s.observing){assert(s.forest.attached);assert(!s.forest.motors);}
  if(s.delivered){assert(s.result.fix.ce_m!=null);assert(s.result.rf.linkOK);assert(s.observed);assert(s.fixed);}
 }
 return s;
}
for(const scenario of ['branch','moving','grip-failure','landing','blocked','sensor-loss']){
 const s=run({signal:'softWhistle'},{scenario});
 console.log(scenario,s.phase,s.result?.find.status,s.delivered,s.stored,s.t.toFixed(1));
 assert(s.done);
 if(['blocked','sensor-loss'].includes(scenario)){assert(!s.observed);assert(!s.result);assert(!s.delivered);}
 else{assert(s.observed);assert.equal(s.phase,'COMPLETE');assert(s.events.some(e=>e.stage==='RELEASE'));}
 if(scenario==='grip-failure'){assert(s.events.some(e=>e.stage==='RECOVER'));assert.equal(s.forest.target.id,'Branch B');}
}
const positive=run({signal:'softWhistle',opDist_m:10},{scenario:'moving'});assert(positive.delivered);
const silent=run();assert.equal(silent.config.signal,'none');assert.equal(silent.result.find.identity,'UNVERIFIED');
if(!silent.result.geometry.clearThermalLine)assert(!silent.result.thermal.detected);
const unavailable=run({signal:'softWhistle',opDist_m:10,relayDist_m:50000},{scenario:'moving'});assert(unavailable.stored);assert(!unavailable.delivered);
const courier=run({signal:'softWhistle',opDist_m:10,longHaul:'courier'},{scenario:'moving'},180);assert(courier.observing);assert(courier.forest.attached);assert(!courier.delivered);
while(!courier.done&&courier.t<4000)U.step(courier,.1);assert(courier.done);assert(courier.delivered);
const exported=JSON.parse(JSON.stringify(U.serializable(positive)));assert.equal(exported.kind,'unified-eagle-software-model');assert(exported.events.some(e=>e.stage==='MARKER'));
assert.throws(()=>U.step(U.create(M),NaN));
console.log('PASS unified mission: support gating, shared geometry, evidence/transport gating, alternate perch, failsafe abort, courier hold, export and silent-person default');
