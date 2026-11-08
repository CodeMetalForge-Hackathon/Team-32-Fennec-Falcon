/* Shared mission clock: flight, supported sensing, modeled transport, departure. */
(function(root){
 'use strict';
 const F=typeof module!=='undefined'?require('./forest-model.js'):root.EagleForest;
 function intersects(a,b,o){let lo=0,hi=1;for(let i=0;i<3;i++){const d=b[i]-a[i];if(Math.abs(d)<1e-10){if(a[i]<o.min[i]||a[i]>o.max[i])return false;}else{const t=(o.min[i]-a[i])/d,u=(o.max[i]-a[i])/d;lo=Math.max(lo,Math.min(t,u));hi=Math.min(hi,Math.max(t,u));if(lo>hi)return false;}}return lo<=hi;}
 function event(s,stage,note){s.events.push({t:s.t,stage,note});}
 function create(model,settings={},options={}){
  const config={...model.defaults(),...settings},forest=F.create(options.scenario||'branch',options.site||'fushan');forest.holdPerch=true;
  const target=forest.target?.p||[36,28,12],distance=Math.min(25,Math.max(5,+config.opDist_m||20)),direction=[24-target[0],20-target[1]],length=Math.hypot(...direction)||1;
  const op=[target[0]+direction[0]/length*distance,target[1]+direction[1]/length*distance,0];op[2]=forest.world.ground?.(op[0],op[1])||0;
  const s={model,config,forest,operator:op,reportedPosition:null,t:0,phase:forest.phase,done:forest.done,observing:false,observed:false,observationAge:0,observationDuration:0,nextEvent:0,result:null,inputs:null,fixed:false,delivered:false,stored:false,events:[],site:options.site||'fushan',scenario:options.scenario||'branch'};
  event(s,forest.phase,forest.done?'No reachable support; observation and relay withheld.':'Eagle starts transit in the same forest used by its camera and route planner.');return s;
 }
 function observe(s){
  const f=s.forest,h=s.config.opPosture==='standing'?1.1:s.config.opPosture==='sitting'?.7:.25,head=s.operator.map((v,i)=>v+(i===2?h:0)),from=f.p.map((v,i)=>v+(i===2?.35:0));
  const blocked=f.world.obstacles.filter(o=>o.kind!=='boundary').some(o=>intersects(from,head,o)),distance=Math.hypot(f.p[0]-s.operator[0],f.p[1]-s.operator[1]),height=Math.max(.5,f.p[2]-s.operator[2]);
  s.inputs={...s.config,opDist_m:distance,perchHeight_m:height,geometryLOS:!blocked};s.result=s.model.evaluate(s.inputs);s.result.geometry={horizontal_m:distance,heightAbovePersonGround_m:height,clearThermalLine:!blocked};
  if(s.result.fix.ce_m!=null){const dx=(s.operator[0]-f.p[0])/distance,dy=(s.operator[1]-f.p[1])/distance,a=(s.result.fix.rangeErr_m||0)*.35,b=(s.result.fix.crossErr_m||0)*.3;s.reportedPosition=[s.operator[0]+dx*a-dy*b,s.operator[1]+dy*a+dx*b,s.operator[2]];}
  s.observed=true;s.observing=true;s.observationAge=0;s.nextEvent=0;s.observationDuration=Math.max(4,...s.result.timeline.map(e=>Number.isFinite(e.t_s)?e.t_s+2:0));
  event(s,'OBSERVE',`Support verified; propulsion off. Same-scene thermal line ${blocked?'blocked':'clear'}. Identity remains UNVERIFIED.`);
 }
 function step(s,dt){
  if(!Number.isFinite(dt)||dt<0||dt>.25)throw Error('dt must be 0..0.25 seconds');if(s.done)return s;s.t+=dt;const f=s.forest;
  if(s.observing){
   F.step(f,dt);s.observationAge+=dt;const timeline=s.result.timeline.filter(e=>Number.isFinite(e.t_s));
   while(s.nextEvent<timeline.length&&timeline[s.nextEvent].t_s<=s.observationAge){const e=timeline[s.nextEvent++];event(s,e.step.toUpperCase(),e.note);if(e.step==='Fix')s.fixed=true;if(e.step==='Marker')s.delivered=true;if(e.step==='Store observation')s.stored=true;}
   s.phase=s.delivered?'MARKER':s.stored?'STORE':s.fixed&&s.result.timeToMarker_s!=null?'RELAY':'OBSERVE';
   if(s.observationAge>=s.observationDuration){s.observing=false;f.holdPerch=false;f.age=Math.max(4,f.age);event(s,'RELEASE','Observation/transport stage finished; depart along the checked support corridor.');}
  }else{
   const before=f.phase;F.step(f,dt);s.phase=f.phase;if(before!==f.phase)event(s,f.phase,f.events[0]?.text||f.phase);
   if(['PERCHED','LANDED'].includes(f.phase)&&f.attached&&!s.observed)observe(s);if(s.observing)s.phase='OBSERVE';if(f.done){s.done=true;s.phase=f.phase;}
  }return s;
 }
 function serializable(s){return {version:1,kind:'unified-eagle-software-model',site:s.site,scenario:s.scenario,t:s.t,phase:s.phase,operator:s.operator,reportedPosition:s.reportedPosition,pose:s.forest.p,attached:s.forest.attached,motors:s.forest.motors,delivered:s.delivered,stored:s.stored,inputs:s.inputs,result:s.result,events:s.events,trail:s.forest.trail,limits:'Procedural geometry, synthetic pose/contact, modeled sensors/transport. No flight hardware or live communications.'};}
 const api={create,step,serializable,intersects};if(typeof module!=='undefined')module.exports=api;else root.EagleMission=api;
})(globalThis);
