// §29：执行实际队列与生图编排代码，所有网络/持久化均为内存替身。
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'../..');
const {File}=require('node:buffer');
const deferred=()=>{let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j});return{promise,resolve,reject}};
const checks=[];
function setup(count=2){
 const photos=new Map(),calls={recognition:0,start:0,status:0},sample=Buffer.from('synthetic-image'),file=new File([sample],'source.png',{type:'image/png'});
 const draft={version:1,id:crypto.randomBytes(16).toString('hex'),position:0,items:Array.from({length:count},()=>({id:crypto.randomBytes(16).toString('hex'),status:'queued',fields:{colors:[],style:[],season:[],scene:[]},ai:{},revision:0,recognized:false}))};
 draft.items.forEach(i=>photos.set(i.id,{id:i.id,file,original:file}));
 let context;
 const completed=async id=>({id,state:'completed',source_hash:await context.sourceHash(file),image:'data:image/png;base64,'+sample.toString('base64')});
 const api={previewTags:async()=>{calls.recognition++;return{tags:{type:'tshirt',name:'AI名字',brand:'AI品牌'}}},startFlatlay:async id=>{calls.start++;return completed(id)},flatlayStatus:async id=>{calls.status++;return completed(id)}};
 context=vm.createContext({api,crypto:crypto.webcrypto,File,Uint8Array,atob,Map,Set,Date,JSON,Error,setTimeout,clearTimeout,loadPhoto:async id=>photos.get(id),newToken:()=>crypto.randomBytes(16).toString('hex'),copy:v=>v===undefined?undefined:JSON.parse(JSON.stringify(v)),storageAvailable:()=>true,cropFile:async()=>({file,rect:{x:0,y:0,w:.5,h:.5}}),esc:String,layoutMessage:()=>''});
 const source=['web/js/flatlay.js','web/js/import-queue.js'].map(name=>fs.readFileSync(path.join(root,name),'utf8').replace(/^import .*;\r?\n/gm,'').replace(/export /g,'')).join('\n');
 vm.runInContext(source+'\nglobalThis.Queue=ImportQueue;',context);
 const options={draft,ctx:{health:{vision_ready:true,flatlay_configured:true}},persist:async()=>true,capture:()=>{},changed:()=>{},fields:()=>{},error:e=>({kind:'recognition',code:e.code||'unknown',text:'测试错误'})};
 return{context,api,calls,draft,file,options,queue:()=>new context.Queue(options)};
}
async function test(name,fn){await fn();checks.push(name);}
(async()=>{
 await test('新选择自动准备全部但不移动编辑位置或入库',async()=>{const t=setup(3),q=t.queue();await q.authorize(t.draft.items.map(i=>i.id));await q.pumping;assert.equal(t.calls.recognition,3);assert.equal(t.calls.start,3);assert.equal(t.draft.position,0);assert(t.draft.items.every(i=>i.flatlay.state==='completed'&&i.status!=='saved'));q.dispose()});
 await test('已有手填及主动清空不被识别覆盖',async()=>{const t=setup(1),i=t.draft.items[0];i.fields.name='手填名字';i.fields.brand=null;i.edited={name:true,brand:true};const q=t.queue();await q.authorize([i.id]);await q.pumping;assert.equal(i.fields.name,'手填名字');assert.equal(i.fields.brand,null);q.dispose()});
 await test('旧草稿仅选品类不能新生图',async()=>{const t=setup(1),i=t.draft.items[0];i.recognized=true;i.fields.type='tshirt';i.assistant={stage:'needs_type'};const q=t.queue();await q.restore();await q.pumping;q.request(i.id,{generationOnly:true,automatic:true});await q.pumping;assert.equal(t.calls.start,0);q.dispose()});
 for(const action of ['dispose','pause','revision'])await test('持久化等待后'+action+'不发识别',async()=>{const t=setup(1),enter=deferred(),release=deferred(),i=t.draft.items[0];t.options.persist=async()=>{if(i.assistant?.stage==='recognizing'){enter.resolve();await release.promise}return true};const q=t.queue();q.request(i.id);await enter.promise;if(action==='dispose')q.dispose();else if(action==='pause')q.pause('test','暂停');else i.revision++;release.resolve();await q.pumping;assert.equal(t.calls.recognition,0);q.dispose()});
 await test('识别标识不能持久化不调用模型',async()=>{const t=setup(1);t.options.persist=async()=>false;const q=t.queue();q.request(t.draft.items[0].id);await q.pumping;assert.equal(t.calls.recognition,0);q.dispose()});
 await test('生图标识不能持久化不POST',async()=>{const t=setup(1),i=t.draft.items[0];i.recognized=true;i.fields.type='tshirt';t.options.persist=async()=>false;const q=t.queue();q.request(i.id,{generationOnly:true});await q.pumping;assert.equal(t.calls.start,0);q.dispose()});
 await test('生图标识落盘时变更品类不提交旧品类',async()=>{const t=setup(1),i=t.draft.items[0];i.recognized=true;i.fields.type='tshirt';t.options.persist=async()=>{if(i.flatlay?.state==='starting')i.fields.type='jacket';return true};const q=t.queue();q.request(i.id,{generationOnly:true});await q.pumping;assert.equal(t.calls.start,0);q.dispose()});
 await test('后件超时使此前排队的自动续链停下',async()=>{const t=setup(2),[a,b]=t.draft.items,enter=deferred(),answer=deferred();a.recognized=true;a.assistant={stage:'needs_type'};t.api.previewTags=async()=>{t.calls.recognition++;enter.resolve();return answer.promise};const q=t.queue();await q.authorize([a.id,b.id]);await enter.promise;a.fields.type='tshirt';q.request(a.id,{generationOnly:true,automatic:true});const e=Object.assign(new Error('timeout'),{code:'bridge_timeout',status:503});answer.reject(e);await q.pumping;assert.equal(t.calls.start,0);assert(t.draft.automation.paused);q.dispose()});
 await test('恢复已有平铺只查询原ID',async()=>{const t=setup(1),i=t.draft.items[0];i.recognized=true;i.fields.type='tshirt';i.flatlay={id:'a'.repeat(32),state:'running',kind:'tshirt',sourceHash:await t.context.sourceHash(t.file)};const q=t.queue();await q.restore();await q.pumping;assert.equal(t.calls.start,0);assert.equal(t.calls.recognition,0);assert.equal(t.calls.status,1);assert.equal(i.flatlay.state,'completed');q.dispose()});
 await test('暂停结束通知看到已释放的运行锁',async()=>{const t=setup(1);let q,seen=false;t.api.previewTags=async()=>{throw Object.assign(new Error('timeout'),{code:'bridge_timeout',status:503})};t.options.changed=()=>{if(q&&t.draft.automation.paused&&!q.pumping)seen=true};q=t.queue();await q.authorize(t.draft.items.map(i=>i.id));await q.pumping;assert(seen);q.dispose()});
 console.log(JSON.stringify({passed:checks.length,failed:0,checks}));
})().catch(e=>{console.error(e.stack);process.exitCode=1});
