// §30：验证真实流取消边界和参考照片匹配，不访问设备或网络。
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert/strict');
const ROOT=path.resolve(__dirname,'../..');let passed=0;
const check=async(name,work)=>{await work();passed++;};
function cameraFactory(){const scope={};vm.runInNewContext(fs.readFileSync(path.join(ROOT,'web/js/features/outfits/camera.js'),'utf8').replace('export function','function')+';globalThis.factory=createTryonCamera',scope);return scope.factory;}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};}
function media(facing='user'){let stops=0;const listeners={};const track={stop(){stops++;},getSettings:()=>({facingMode:facing}),addEventListener:(name,fn)=>listeners[name]=fn};return {stream:{getTracks:()=>[track],getVideoTracks:()=>[track]},stops:()=>stops,end:()=>listeners.ended?.()};}
function video(play=()=>Promise.resolve()){return {srcObject:null,play,pause(){}};}
(async()=>{
 const factory=cameraFactory();
 await check('未点击不请求摄像头',()=>{let calls=0;factory({video:video(),mediaDevices:{getUserMedia(){calls++;}}});assert.equal(calls,0);});
 await check('只请求视频并显示真实流',async()=>{const m=media(),v=video();let options;const c=factory({video:v,mediaDevices:{getUserMedia:async input=>(options=input,m.stream)}});assert.equal(await c.start(),true);assert.equal(options.audio,false);assert.equal(v.srcObject,m.stream);assert.equal(c.snapshot().state,'preview');c.dispose();assert.ok(m.stops()>0);});
 await check('等待授权时停止会释放迟到的流',async()=>{const d=deferred(),m=media(),v=video(),c=factory({video:v,mediaDevices:{getUserMedia:()=>d.promise}});const pending=c.start();c.stop();d.resolve(m.stream);assert.equal(await pending,false);assert.ok(m.stops()>0);assert.equal(v.srcObject,null);});
 await check('等待播放时关闭不复活',async()=>{const d=deferred(),m=media(),v=video(()=>d.promise),c=factory({video:v,mediaDevices:{getUserMedia:async()=>m.stream}});const pending=c.start();await Promise.resolve();c.dispose();d.resolve();assert.equal(await pending,false);assert.equal(v.srcObject,null);assert.ok(m.stops()>0);});
 await check('反序镜头请求不覆盖新流',async()=>{const first=deferred(),second=deferred(),a=media('user'),b=media('environment'),v=video();let calls=0;const c=factory({video:v,mediaDevices:{getUserMedia:()=>++calls===1?first.promise:second.promise}});const old=c.start('user'),next=c.start('environment');second.resolve(b.stream);await next;first.resolve(a.stream);await old;assert.equal(v.srcObject,b.stream);assert.ok(a.stops()>0);assert.equal(c.snapshot().facing,'environment');c.dispose();});
 await check('拒绝权限不泄漏原始错误',async()=>{const snapshots=[],error=Object.assign(Error('sensitive diagnostic'),{name:'NotAllowedError'}),c=factory({video:video(),mediaDevices:{getUserMedia:async()=>{throw error;}},onChange:s=>snapshots.push(s)});await c.start();assert.equal(c.snapshot().active,false);assert.match(snapshots.at(-1).error,/权限/);assert.ok(!snapshots.at(-1).error.includes('sensitive'));});
 await check('设备结束会释放流并通知用户',async()=>{const m=media(),c=factory({video:video(),mediaDevices:{getUserMedia:async()=>m.stream}});await c.start();m.end();assert.equal(c.snapshot().active,false);assert.equal(c.snapshot().state,'error');});
 await check('销毁后不能再次请求摄像头',async()=>{let calls=0;const c=factory({video:video(),mediaDevices:{getUserMedia:async()=>{calls++;return media().stream;}}});c.dispose();assert.equal(await c.start(),false);assert.equal(calls,0);});
 const states=new Map();let index=0;const scope={esc:s=>String(s),icon:()=>'<svg></svg>',URL,api:{imageUrl:p=>'/images/'+p},referenceState:d=>states.get(d.id)||{status:'idle',references:[]},referenceIndex:()=>index,selectReference:(_,i)=>{index=i},bindHorizontalSwipe:()=>({})};
 let code=fs.readFileSync(path.join(ROOT,'web/js/features/purchase/reference-media.js'),'utf8').replace(/^import .*;\r?\n/gm,'').replaceAll('export ','');vm.runInNewContext(code+';globalThis.refs={purchaseReference,referencePhoto,referenceCredit,referenceStatus}',scope);const {purchaseReference:match,referencePhoto:html,referenceCredit:credit,referenceStatus:status}=scope.refs;
 const d={id:'direction',image_url:'https://invented.example/photo.jpg'},row=(n=1)=>({id:'photo'+n,image_path:'reference-'+String(n).repeat(64)+'.jpg',source_url:'https://shop.example.com/item'+n,name:'真实单品'});
 await check('旧方向不强行匹配固定图库或模型网址',()=>{assert.equal(match(d),null);assert.ok(!html(d).includes('invented.example'));assert.match(html(d),/正在匹配商品图/)});
 await check('展示服务核对并缓存的实际照片',()=>{states.set(d.id,{status:'complete',references:[row()]});assert.equal(match(d).id,'photo1');assert.match(html(d),/src="\/images\/reference-/)});
 await check('不采用伪造文件路径与脚本来源',()=>{states.set(d.id,{status:'complete',references:[{...row(),image_path:'../../unsafe.jpg'},{...row(),source_url:'javascript:alert(1)'}]});assert.equal(match(d),null)});
 await check('空结果和处理中状态不同',()=>{states.set(d.id,{status:'running',references:[]});assert.equal(status(d).busy,true);assert.match(html(d),/正在匹配商品图/);states.set(d.id,{status:'empty',references:[]});assert.equal(status(d).busy,false);assert.match(html(d),/商品图匹配未成功/)});
 await check('备选图与来源保持对应',()=>{states.set(d.id,{status:'complete',references:[row(1),row(2)]});index=1;assert.equal(match(d).id,'photo2');assert.ok(credit(d).includes('https://shop.example.com/item2'))});
 await check('来源凭据与不合法缓存名不会展示',()=>{states.set(d.id,{status:'complete',references:[{...row(),source_url:'https://name:secret@shop.example.com/item'},{...row(),image_path:'reference-evil.svg'}]});index=0;assert.equal(match(d),null)});
 console.log(JSON.stringify({passed,failed:0}));
})().catch(error=>{console.error(error);process.exitCode=1;});
