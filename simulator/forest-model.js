/* Synthetic geometry and kinematic contact model, independent of SITL/hardware. */
(function(root){
  'use strict';
  const bounds=[22,16,12], radius=0.45;
  const box=(id,min,max,kind='wood')=>({id,min,max,kind});
  function scene(scenario='branch',profile='baseline'){
    if(profile.startsWith('fushan'))return (typeof module!=='undefined'?require('./forest-site.js'):root.EagleForestSite).build(scenario,profile);
    const obstacles=[
      box('trunk-1',[6,0,0],[7,8,10]),box('trunk-2',[11,8,0],[12,16,9]),
      box('low limb',[7,7,4],[12,9,5]),box('overhead limb',[13,1,9],[17,9,10]),
      box('perch A trunk',[20,9,0],[21,11,10]),box('perch A branch',[17,9.8,5.7],[21,10.2,6]),
      box('perch B trunk',[20,3,0],[21,5,8]),box('perch B branch',[17,3.8,4.7],[21,4.2,5]),
      box('leaf crown',[3,2,10],[11,12,12],'foliage')
    ];
    if(scenario==='blocked')obstacles.push(box('unbroken canopy barrier',[10,0,0],[11,16,12],'foliage'));
    const candidates=[
      {id:'Leaves',kind:'foliage',p:[9,6,11],reason:'Leaves are not a verified load-bearing support.'},
      {id:'Crowded fork',kind:'branch',p:[20,10,7],reason:'Trunk intersects aircraft clearance envelope.'},
      {id:'Branch A',kind:'branch',p:[18,10,7],approach:[16,10,8],reason:scenario==='moving'?'Support motion exceeds this illustrative gripper envelope.':null},
      {id:'Branch B',kind:'branch',p:[18,4,6],approach:[16,4,7],reason:null}
    ];
    if(scenario==='landing')candidates.splice(2,2,{id:'Clearing',kind:'ground',p:[18,2,1],approach:[18,2,4],reason:null});
    return {obstacles,candidates};
  }
  function free(p,world){
    if(p.some((v,i)=>v<radius||v>(world.bounds||bounds)[i]-radius))return false;
    if(world.ground&&p[2]<world.ground(p[0],p[1])+radius)return false;
    return !world.obstacles.some(o=>p.every((v,i)=>v>=o.min[i]-radius&&v<=o.max[i]+radius));
  }
  function segment(a,b,world){
    if(!free(a,world)||!free(b,world))return false;
    if(world.ground){const n=Math.ceil(Math.hypot(...a.map((v,i)=>v-b[i]))/.25);for(let j=1;j<n;j++){const p=a.map((v,i)=>v+(b[i]-v)*j/n);if(p[2]<world.ground(p[0],p[1])+radius)return false;}}
    // Exact slab intersection avoids missing a thin corner between samples.
    return !world.obstacles.some(o=>{
      let enter=0,exit=1;
      for(let i=0;i<3;i++){
        const d=b[i]-a[i],lo=o.min[i]-radius,hi=o.max[i]+radius;
        if(Math.abs(d)<1e-12){if(a[i]<lo||a[i]>hi)return false;}
        else{const t1=(lo-a[i])/d,t2=(hi-a[i])/d;enter=Math.max(enter,Math.min(t1,t2));exit=Math.min(exit,Math.max(t1,t2));if(enter>exit)return false;}
      }return enter<=exit;
    });
  }
  // Six-connected voxel A*: collision checks use inflated synthetic boxes.
  function plan(start,goal,world){
    const realStart=start.slice(),realGoal=goal.slice(),cell=world.cell||1;
    const attach=p=>{
      const rounded=p.map(v=>Math.round(v/cell)*cell),candidates=[];
      for(const x of [-cell,0,cell])for(const y of [-cell,0,cell])for(const z of [-cell,0,cell])candidates.push([rounded[0]+x,rounded[1]+y,rounded[2]+z]);
      candidates.sort((a,b)=>Math.hypot(...a.map((v,i)=>v-p[i]))-Math.hypot(...b.map((v,i)=>v-p[i])));
      return candidates.find(q=>segment(p,q,world));
    };
    start=attach(realStart);goal=attach(realGoal);if(!start||!goal)return null;
    if(!free(start,world)||!free(goal,world))return null;
    const key=p=>p.join(','), h=p=>p.reduce((s,v,i)=>s+Math.abs(v-goal[i]),0);
    const open=[{p:start,g:0,f:h(start)}],cost=new Map([[key(start),0]]),parent=new Map();
    while(open.length){
      let k=0;for(let i=1;i<open.length;i++)if(open[i].f<open[k].f)k=i;
      const n=open.splice(k,1)[0],nk=key(n.p);
      if(n.g!==cost.get(nk))continue;
      if(nk===key(goal)){
        const path=[goal];let at=nk;while(parent.has(at)){at=parent.get(at);path.push(at.split(',').map(Number));}path.reverse();
        path.unshift(realStart);path.push(realGoal);
        const smooth=[path[0]];let i=0;while(i<path.length-1){let j=path.length-1;while(j>i+1&&!segment(path[i],path[j],world))j--;smooth.push(path[j]);i=j;}return smooth;
      }
      for(let axis=0;axis<3;axis++)for(const d of [-1,1]){
        const p=n.p.slice();p[axis]+=d*cell;const pk=key(p),g=n.g+cell;
        if(g>=(cost.get(pk)??Infinity)||!segment(n.p,p,world))continue;
        cost.set(pk,g);parent.set(pk,nk);open.push({p,g,f:g+h(p)});
      }
    }return null;
  }
  function event(s,text){s.events.unshift({t:s.t,text});s.events=s.events.slice(0,14);}
  function select(s){
    for(const c of s.world.candidates){
      if(c.reason||s.failed.includes(c.id))continue;
      const route=plan(s.p,c.approach,s.world);
      if(!route||!segment(c.approach,c.p,s.world)){c.reason='No collision-free approach in the synthetic map.';continue;}
      s.target=c;s.path=route;s.next=1;s.phase='TRANSIT';s.age=0;event(s,'Selected '+c.id+'; 3D route computed.');return;
    }
    s.phase='ABORTED';s.done=true;event(s,'No reachable support. Mission withheld; no blind climb or landing.');
  }
  function create(scenario='branch',profile='baseline'){
    const world=scene(scenario,profile),start=world.start||[2,2,3];
    const s={scenario,world,p:start.slice(),t:0,age:0,phase:'INSPECT',path:[],next:1,target:null,failed:[],events:[],trail:[start.slice()],done:false,attached:false,motors:true};
    event(s,'Synthetic metric pose and complete obstacle map supplied.');select(s);return s;
  }
  function move(s,target,dt,speed){
    const d=Math.hypot(...target.map((v,i)=>v-s.p[i])),travel=Math.min(d,dt*speed);
    if(d)s.p=s.p.map((v,i)=>v+(target[i]-v)*travel/d);
    if(!segment(s.trail.at(-1),s.p,s.world))throw Error('Synthetic clearance violated');
    s.trail.push(s.p.slice());return d<=travel+1e-8;
  }
  function step(s,dt){
    if(!Number.isFinite(dt)||dt<0||dt>0.25)throw Error('dt must be 0..0.25 seconds');
    if(s.done)return s;
    s.t+=dt;s.age+=dt;
    if(s.scenario==='sensor-loss'&&s.t>=5){s.phase='ABORTED';s.done=true;event(s,'Pose/depth feed lost. Animation stopped; physical failsafe is not modeled.');return s;}
    const change=phase=>{s.phase=phase;s.age=0;event(s,phase.replaceAll('_',' ').toLowerCase());};
    if(s.phase==='TRANSIT'){
      if(move(s,s.path[s.next],dt,s.world.speed||2)){s.next++;if(s.next===s.path.length)change('INSPECT');}
    }else if(s.phase==='INSPECT'&&s.age>=1.5)change('APPROACH');
    else if(s.phase==='APPROACH'&&move(s,s.target.p,dt,0.7))change('CONTACT');
    else if(s.phase==='CONTACT'&&s.age>=1)change('VERIFY_SUPPORT');
    else if(s.phase==='VERIFY_SUPPORT'&&s.age>=2){
      if(s.scenario==='grip-failure'&&!s.failed.length){s.failed.push(s.target.id);event(s,'Injected grip failure: motors remain on; retreat before reselection.');change('RECOVER');}
      else{s.attached=true;s.motors=false;change(s.target.kind==='ground'?'LANDED':'PERCHED');}
    }else if(s.phase==='RECOVER'&&move(s,s.target.approach,dt,0.7))select(s);
    else if(['PERCHED','LANDED'].includes(s.phase)&&s.age>=4&&!s.holdPerch){s.motors=true;s.attached=false;change('RELEASE');}
    else if(s.phase==='RELEASE'&&s.age>=1)change('DEPART');
    else if(s.phase==='DEPART'&&move(s,s.target.approach,dt,0.7)){change('COMPLETE');s.done=true;}
    return s;
  }
  const api={bounds,radius,scene,free,segment,plan,create,step};
  if(typeof module!=='undefined')module.exports=api;else root.EagleForest=api;
})(globalThis);
