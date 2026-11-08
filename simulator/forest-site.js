/* Fushan-informed procedural reconstruction. Tree positions/terrain are NOT survey data. */
(function(root){
  'use strict';
  const profile={id:'fushan',name:'Fushan Experimental Forest · 福山',bounds:[48,40,38],cell:2,
    evidence:[
      {label:'Forest type',value:'Evergreen broadleaf · Machilus–Castanopsis',source:'https://www.tfri.gov.tw/en/cp.aspx?n=7574'},
      {label:'Surveyed plot',value:'500 × 500 m · elevation 600–733 m',source:'https://www.tfri.gov.tw/en/cp.aspx?n=7574'},
      {label:'Canopy variation',value:'Valleys 15–20 m · ridges 5–10 m',source:'https://www.columbia.edu/~mu2126/publications_files/Hogan%20et%20al%202018%20Forests.pdf'},
      {label:'Wet conditions',value:'227 rainy days/year · 95% mean RH (historical)',source:'https://www.tfri.gov.tw/en/cp.aspx?n=7574'}
    ]};
  function ground(x,y){return 0.10*x+0.065*y+0.65*Math.sin(x/8)+0.45*Math.cos(y/6)+1.0;}
  function build(scenario,variant='fushan'){
    const ridge=variant==='fushan-ridge';
    let seed=73219;const rand=()=>{seed=(Math.imul(1664525,seed)+1013904223)>>>0;return seed/4294967296;};
    const trees=[],obstacles=[],ferns=[],logs=[];
    const addBox=(id,min,max,kind)=>obstacles.push({id,min,max,kind});
    const limb=(id,a,b,r)=>{const min=a.map((v,i)=>Math.min(v,b[i])-r),max=a.map((v,i)=>Math.max(v,b[i])+r);addBox(id,min,max,'branch');return {a,b,r};};
    const reserved=(x,y)=>Math.hypot(x-4,y-4)<3||Math.hypot(x-41,y-5)<3||(x>27&&Math.abs(y-28)<5.8)||(x>27&&Math.abs(y-12)<5.8);
    function tree(x,y,h,r,support){
      const z=ground(x,y),tr={x,y,z,h,r,branches:[],crown:[x,y,z+h-1],crownScale:[2.2+rand(),2+rand(),2.4],support};trees.push(tr);
      addBox('trunk '+trees.length,[x-r,y-r,z],[x+r,y+r,z+h],'trunk');
      if(!support){
        for(let j=0;j<2;j++){const angle=rand()*Math.PI*2,l=1.5+rand()*2,a=[x,y,z+h*(0.4+j*.19)],b=[x+Math.cos(angle)*l,y+Math.sin(angle)*l,a[2]+0.6];tr.branches.push(limb('branch '+trees.length+'-'+j,a,b,0.10));}
      }
      const c=tr.crown,sz=tr.crownScale;addBox('crown '+trees.length,c.map((v,i)=>v-sz[i]),c.map((v,i)=>v+sz[i]),'foliage');
      return tr;
    }
    // Major stems only. This is not a conversion of all >=1 cm census individuals into canopy trees.
    for(let i=0;i<180&&trees.length<52;i++){
      const x=3+rand()*42,y=3+rand()*34;if(reserved(x,y)||trees.some(t=>Math.hypot(t.x-x,t.y-y)<2.7))continue;
      tree(x,y,(ridge?4.5:12)+rand()*(ridge?3:5),0.14+rand()*0.23);
    }
    const A=tree(39,28,ridge?8.5:15,0.32,true),B=tree(39,12,ridge?8:14,0.27,true);
    const za=A.z+(ridge?3:7),zb=B.z+(ridge?2.5:6);
    A.branches.push(limb('support A',[35,28,za],[39,28,za],0.13));
    B.branches.push(limb('support B',[35,12,zb],[39,12,zb],0.12));
    for(let i=0;i<95;i++){
      const x=2+rand()*44,y=2+rand()*36;if(reserved(x,y))continue;const z=ground(x,y),h=1+rand()*2.5;
      ferns.push({x,y,z,h,angle:rand()*6.28});
      if(i%3===0)addBox('understory '+i,[x-.3,y-.3,z],[x+.3,y+.3,z+h],'understory');
    }
    for(const [x,y,dx,dy] of [[10,17,5,2],[22,31,4,-2],[24,9,3,1]]){
      const a=[x,y,ground(x,y)+.3],b=[x+dx,y+dy,ground(x+dx,y+dy)+.3];logs.push(limb('fallen wood '+x,a,b,.24));
    }
    if(scenario==='blocked')addBox('unvalidated transit boundary',[24,0,0],[25,40,38],'boundary');
    const candidates=[
      {id:'Leaf crown',kind:'foliage',p:A.crown,reason:'Leaf cover gives no evidence of load-bearing support.'},
      {id:'Crowded fork',kind:'branch',p:[39,28,za+1],reason:'Trunk intersects the modeled rotor-clearance envelope.'},
      {id:'Branch A',kind:'branch',p:[36,28,za+1],approach:[32,28,za+3],reason:scenario==='moving'?'Injected excessive support motion: reject before contact.':null},
      {id:'Branch B',kind:'branch',p:[36,12,zb+1],approach:[32,12,zb+3],reason:null}
    ];
    if(scenario==='landing')candidates.splice(2,2,{id:'Clearing',kind:'ground',p:[41,5,ground(41,5)+1],approach:[41,5,ground(41,5)+4],reason:null});
    const sx=ridge?2:4,sy=ridge?2:4;
    return {profile,variant,obstacles,candidates,trees,ferns,logs,ground,bounds:profile.bounds,cell:2,start:[sx,sy,ground(sx,sy)+(ridge?2:3)],speed:1.2};
  }
  const api={profile,ground,build};if(typeof module!=='undefined')module.exports=api;else root.EagleForestSite=api;
})(globalThis);
