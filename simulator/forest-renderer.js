/* Hallmark · pre-emit critique: P4 H4 E4 S5 R4 V4
 * Perspective forest renderer; ecological reconstruction, not a photogrammetric survey. */
(function(root){
 'use strict';
 const C={sky:0x82958a,earth:0x333627,bark:0x443a2b,moss:0x59623b,leaf:0x304b29,leafLight:0x50683b,fern:0x42652f,route:0x62e1ee,trail:0xfff2cf,gold:0xffbe54,drone:0xdbdbcb,wire:0xe17b59,skyLight:0xd7e7e4,groundLight:0x343721,sun:0xffeac4,frame:0x303b37,barkBase:0x514632,barkGroove:0x393c29,barkLichen:0x70704b};
 function create(canvas){
  const T=root.THREE;if(!T)throw Error('Three.js unavailable');
  const renderer=new T.WebGLRenderer({canvas,antialias:true,preserveDrawingBuffer:true});renderer.setSize(1000,620,false);renderer.setPixelRatio(1);renderer.outputEncoding=T.sRGBEncoding;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=.85;
  const scene=new T.Scene();scene.background=new T.Color(C.sky);scene.fog=new T.Fog(C.sky,55,150);
  const camera=new T.PerspectiveCamera(48,1000/620,.08,400);camera.up.set(0,0,1);
  const hemi=new T.HemisphereLight(C.skyLight,C.groundLight,.8);hemi.position.set(0,0,70);scene.add(hemi);
  const sun=new T.DirectionalLight(C.sun,1);sun.position.set(25,-20,55);scene.add(sun);
  let terrainGroup=new T.Group(),canopy=new T.Group(),colliders=new T.Group();scene.add(terrainGroup,canopy,colliders);
  const dynamic=new T.Group();scene.add(dynamic);
  const drone=new T.Group(),body=new T.Mesh(new T.SphereGeometry(.35,12,8),new T.MeshStandardMaterial({color:C.drone,roughness:.6}));body.scale.set(1.25,.7,.5);drone.add(body);
  const motorMat=new T.MeshBasicMaterial({color:C.route}),frameMat=new T.MeshStandardMaterial({color:C.frame});
  for(const x of [-.55,.55])for(const y of [-.55,.55]){
   const arm=new T.Mesh(new T.CylinderGeometry(.035,.035,.79,6),frameMat);arm.position.set(x/2,y/2,0);arm.quaternion.setFromUnitVectors(new T.Vector3(0,1,0),new T.Vector3(x,y,0).normalize());drone.add(arm);
   const ring=new T.Mesh(new T.TorusGeometry(.25,.028,4,20),motorMat);ring.position.set(x,y,0);drone.add(ring);
  }scene.add(drone);
  const marker=new T.Mesh(new T.TorusGeometry(.85,.07,5,32),new T.MeshBasicMaterial({color:C.gold,depthTest:false}));marker.renderOrder=9;scene.add(marker);
  let world=null,route=null,trail=null,lastTrailLength=-1,lastPath=null;
  const v=p=>new T.Vector3(...p);
  function barkTexture(){
   const c=document.createElement('canvas');c.width=128;c.height=256;const g=c.getContext('2d');g.fillStyle=new T.Color(C.barkBase).getStyle();g.fillRect(0,0,128,256);
   for(let i=0;i<750;i++){const x=(i*73.91)%128,y=(i*37.71)%256;g.strokeStyle=new T.Color(i%4?C.barkGroove:C.barkLichen).getStyle();g.lineWidth=i%3?.7:1.4;g.beginPath();g.moveTo(x,y);g.lineTo(x+Math.sin(i)*3,y+5+i%22);g.stroke();}
   const t=new T.CanvasTexture(c);t.wrapS=t.wrapT=T.RepeatWrapping;t.repeat.set(2,3);t.encoding=T.sRGBEncoding;return t;
  }
  const bark=barkTexture();
  function disposeGroup(group){group.traverse(o=>{o.geometry?.dispose();if(Array.isArray(o.material))o.material.forEach(m=>m.dispose());else o.material?.dispose();});group.clear();}
  function tubes(items,parent,color){
   if(!items.length)return;const geometry=new T.CylinderGeometry(1,1,1,8),material=new T.MeshStandardMaterial({color,roughness:1});
   material.map=bark;const mesh=new T.InstancedMesh(geometry,material,items.length),dummy=new T.Object3D();
   items.forEach((t,i)=>{const a=v(t.a),b=v(t.b),d=b.clone().sub(a);dummy.position.copy(a).add(b).multiplyScalar(.5);dummy.quaternion.setFromUnitVectors(new T.Vector3(0,1,0),d.clone().normalize());dummy.scale.set(t.r,d.length(),t.r*.9);dummy.updateMatrix();mesh.setMatrixAt(i,dummy.matrix);});parent.add(mesh);
  }
  function rebuild(w){
   disposeGroup(terrainGroup);disposeGroup(canopy);disposeGroup(colliders);world=w;
   const bounds=w.bounds||root.EagleForest.bounds,ground=w.ground||(()=>0),[W,H]=bounds;
   const terrain=new T.PlaneGeometry(W,H,64,56),pos=terrain.attributes.position;
   const colors=[];
   for(let i=0;i<pos.count;i++){const x=pos.getX(i)+W/2,y=pos.getY(i)+H/2,z=ground(x,y);pos.setXYZ(i,x,y,z);const c=new T.Color(C.earth),near=Math.min(3,...(w.trees||[]).map(t=>Math.hypot(t.x-x,t.y-y)));c.multiplyScalar((.85+.18*Math.sin(x*1.7+y*.7)+.08*Math.cos(y*2))*(.6+near*.13));colors.push(c.r,c.g,c.b);}
   terrain.setAttribute('color',new T.Float32BufferAttribute(colors,3));terrain.computeVertexNormals();terrainGroup.add(new T.Mesh(terrain,new T.MeshStandardMaterial({vertexColors:true,roughness:1,side:T.DoubleSide})));
   const woody=[];for(const t of w.trees||[]){woody.push({a:[t.x,t.y,t.z],b:[t.x,t.y,t.z+t.h],r:t.r});woody.push(...t.branches);}woody.push(...w.logs||[]);tubes(woody,terrainGroup,C.bark);
   // Leaf clusters use instancing: a varied evergreen crown, not one solid block.
   const trees=w.trees||[],leafGeometry=new T.IcosahedronGeometry(1,2),leafMaterial=new T.MeshStandardMaterial({color:C.leaf,roughness:1,flatShading:true});
   const crowns=new T.InstancedMesh(leafGeometry,leafMaterial,trees.length*7),dummy=new T.Object3D();
   let k=0;for(const t of trees)for(let j=0;j<7;j++){
    const a=j*Math.PI*2/6,sz=t.crownScale;dummy.position.set(t.x+Math.cos(a)*sz[0]*.32,t.y+Math.sin(a)*sz[1]*.32,t.crown[2]+(j===6?sz[2]*.2:-.3));dummy.scale.set(sz[0]*.65,sz[1]*.65,sz[2]*(j===6?.65:.7));dummy.rotation.set(0,0,j*.7);dummy.updateMatrix();crowns.setMatrixAt(k,dummy.matrix);crowns.setColorAt(k,new T.Color(j%3?C.leaf:C.leafLight));k++;
   }if(trees.length)canopy.add(crowns);
   // Individual broadleaf silhouettes break up crown edges; distribution is procedural.
   const leafShape=new T.Shape();leafShape.moveTo(0,-1);leafShape.quadraticCurveTo(-.7,-.1,0,1);leafShape.quadraticCurveTo(.7,-.1,0,-1);
   const leafGeo=new T.ShapeGeometry(leafShape,3),leafMat=new T.MeshStandardMaterial({color:C.leafLight,side:T.DoubleSide,roughness:1});
   const leaves=new T.InstancedMesh(leafGeo,leafMat,trees.length*100);
   k=0;for(const t of trees)for(let j=0;j<100;j++){
    const a=j*2.399963,zz=2*(j+.5)/100-1,rr=Math.sqrt(1-zz*zz);dummy.position.set(t.x+Math.cos(a)*rr*t.crownScale[0]*.93,t.y+Math.sin(a)*rr*t.crownScale[1]*.93,t.crown[2]+zz*t.crownScale[2]*.9);dummy.rotation.set(Math.sin(j)*1.8,Math.cos(j*3)*2,a);dummy.scale.set(.22,.30,.22);dummy.updateMatrix();leaves.setMatrixAt(k++,dummy.matrix);
   }if(trees.length)canopy.add(leaves);
   const shrubs=(w.ferns||[]).filter((_,i)=>i%3===0);tubes(shrubs.map(f=>({a:[f.x,f.y,f.z],b:[f.x,f.y,f.z+f.h*.9],r:.025})),terrainGroup,C.bark);
   const understory=new T.InstancedMesh(leafGeo.clone(),leafMat.clone(),shrubs.length*36);k=0;
   for(const f of shrubs)for(let j=0;j<36;j++){const a=j*2.4,h=(j%9)/9*f.h,rr=.25+.45*Math.sin(h/f.h*Math.PI);dummy.position.set(f.x+Math.cos(a)*rr,f.y+Math.sin(a)*rr,f.z+h+.2);dummy.rotation.set(.3*Math.sin(j),.7*Math.cos(j),a);dummy.scale.set(.18,.32,.18);dummy.updateMatrix();understory.setMatrixAt(k++,dummy.matrix);}if(shrubs.length)terrainGroup.add(understory);
   const fernVerts=[];for(const f of w.ferns||[]){for(let j=0;j<8;j++){
    const a=f.angle+j*Math.PI/4,dx=Math.cos(a),dy=Math.sin(a);
    for(let n=1;n<7;n++){const u=n/7,l=f.h*.6*u,zz=f.z+f.h*Math.sin(u*Math.PI*.75)*.6,wide=f.h*.13*(1-u);
     for(const side of [-1,1])fernVerts.push(f.x+dx*l,f.y+dy*l,zz,f.x+dx*(l+.2)-dy*wide*side,f.y+dy*(l+.2)+dx*wide*side,zz-.08,f.x+dx*(l+.32),f.y+dy*(l+.32),zz-.03);
    }
   }}const fg=new T.BufferGeometry();fg.setAttribute('position',new T.Float32BufferAttribute(fernVerts,3));fg.computeVertexNormals();terrainGroup.add(new T.Mesh(fg,new T.MeshStandardMaterial({color:C.fern,side:T.DoubleSide,roughness:1})));
   // Fallen leaves and rocks provide local scale; decorative, not a surface survey.
   const pebbles=new T.InstancedMesh(new T.IcosahedronGeometry(1,0),new T.MeshStandardMaterial({color:C.moss,roughness:1}),450);
   for(let i=0;i<450;i++){const x=(Math.sin(i*78.23)*234.7%1+1)%1*W,y=(Math.sin(i*21.78)*921.3%1+1)%1*H;dummy.position.set(x,y,ground(x,y)+.03);dummy.rotation.set(0,0,i*1.6);dummy.scale.set(.12+(i%4)*.08,.06+(i%3)*.05,.035);dummy.updateMatrix();pebbles.setMatrixAt(i,dummy.matrix);}terrainGroup.add(pebbles);
   for(const o of w.obstacles){
    const size=o.max.map((a,i)=>a-o.min[i]),geo=new T.BoxGeometry(...size),edges=new T.EdgesGeometry(geo);geo.dispose();const line=new T.LineSegments(edges,new T.LineBasicMaterial({color:C.wire,transparent:true,opacity:.35}));line.position.set(...o.min.map((a,i)=>(a+o.max[i])/2));colliders.add(line);
    if(!w.trees&&o.kind!=='boundary'){const mesh=new T.Mesh(new T.BoxGeometry(...size),new T.MeshStandardMaterial({color:o.kind==='foliage'?C.leaf:C.bark,transparent:true,opacity:.65}));mesh.position.copy(line.position);terrainGroup.add(mesh);}
   }
   lastPath=null;lastTrailLength=-1;
  }
  function setLine(old,points,color,dashed){
   if(old){dynamic.remove(old);old.geometry.dispose();old.material.dispose();}
   if(points.length<2)return null;
   const material=dashed?new T.LineDashedMaterial({color,dashSize:.7,gapSize:.35,depthTest:false}):new T.LineBasicMaterial({color,depthTest:false});
   const line=new T.Line(new T.BufferGeometry().setFromPoints(points.map(v)),material);line.renderOrder=8;line.computeLineDistances();dynamic.add(line);return line;
  }
  function render(s,view,options={}){
   if(world!==s.world)rebuild(s.world);
   if(lastPath!==s.path){route=setLine(route,s.path,C.route,true);lastPath=s.path;}
   if(lastTrailLength!==s.trail.length){trail=setLine(trail,s.trail,C.trail,false);lastTrailLength=s.trail.length;}
   canopy.visible=!options.cutaway;colliders.visible=!!options.collision;
   scene.fog.near=options.mist?12:100;scene.fog.far=options.mist?60:230;
   const bounds=s.world.bounds||root.EagleForest.bounds,target=options.follow?v(s.p):new T.Vector3(bounds[0]/2,bounds[1]/2,bounds[2]*.34);
   const dist=Math.max(...bounds)*1.52/view.zoom;
   const right=new T.Vector3(Math.cos(view.yaw),Math.sin(view.yaw),0),up=new T.Vector3(-Math.sin(view.yaw)*Math.sin(view.pitch),Math.cos(view.yaw)*Math.sin(view.pitch),Math.cos(view.pitch));
   target.addScaledVector(right,-view.panX*dist/900).addScaledVector(up,view.panY*dist/900);
   camera.position.copy(target).add(new T.Vector3(Math.sin(view.yaw)*Math.cos(view.pitch),-Math.cos(view.yaw)*Math.cos(view.pitch),Math.sin(view.pitch)).multiplyScalar(dist));
   if(options.pov){
    camera.position.copy(v(s.p)).add(new T.Vector3(0,0,.3));
    const next=s.phase==='TRANSIT'?s.path[s.next]:s.target?.p;target.copy(v(next||[s.p[0]+4,s.p[1],s.p[2]]));if(camera.position.distanceTo(target)<1)target.copy(camera.position).add(new T.Vector3(1,0,0));
   }
   camera.lookAt(target);drone.position.set(...s.p);drone.visible=!options.pov;
   motorMat.color.setHex(s.motors?C.route:C.gold);
   marker.visible=!!s.target;if(s.target)marker.position.set(...s.target.p.map((x,i)=>x+(i===2?-.9:0)));
   renderer.render(scene,camera);
  }
  return {render,get info(){return renderer.info.render;}};
 }
 root.EagleForestRenderer={create};
})(globalThis);
