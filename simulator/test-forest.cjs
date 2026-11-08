const assert=require('node:assert/strict');
const F=require('./forest-model.js');
for(const scenario of ['branch','moving','grip-failure','landing','blocked','sensor-loss']){
  const s=F.create(scenario),phases=new Set([s.phase]);let off=false;
  for(let i=0;i<4000&&!s.done;i++){
    F.step(s,0.05);phases.add(s.phase);
    assert(F.free(s.p,s.world),scenario+' aircraft clearance');
    if(!s.motors){off=true;assert(s.attached,'motors off requires support confirmation');}
  }
  assert(s.done,scenario+' terminates');
  if(['blocked','sensor-loss'].includes(scenario)){assert.equal(s.phase,'ABORTED');assert(!off);}
  else{assert.equal(s.phase,'COMPLETE');assert(off);assert(phases.has('VERIFY_SUPPORT'));assert(phases.has('DEPART'));assert(s.motors);assert(!s.attached);}
  if(scenario==='moving')assert.equal(s.target.id,'Branch B');
  if(scenario==='grip-failure'){assert(phases.has('RECOVER'));assert.deepEqual(s.failed,['Branch A']);assert.equal(s.target.id,'Branch B');}
  if(scenario==='landing')assert(phases.has('LANDED'));
  for(let i=1;i<s.trail.length;i++)assert(F.segment(s.trail[i-1],s.trail[i],s.world),'continuous trajectory clearance');
  console.log('PASS',scenario,s.phase,s.t.toFixed(1)+'s',s.target?.id||'no support');
}
assert.throws(()=>F.step(F.create(),NaN));
const world=F.scene('blocked');assert.equal(F.plan([2,2,3],[18,10,7],world),null);
assert(F.scene().candidates.find(c=>c.id==='Leaves').reason);
