// §32：真实媒体模块的解码屏障、旧图失败与新结果阶段，不访问网络。
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert/strict');
const root=path.resolve(__dirname,'../..');
function factory(api={}){const images=[],scope={api:{imageUrl:p=>'/images/'+p,...api},document:{hidden:false,addEventListener(){}},crypto:{randomUUID:()=> 'new-job'},Date,URL,esc:String,icon:()=>'<svg></svg>',bindHorizontalSwipe:()=>({}),setTimeout:()=>1,clearTimeout(){},Image:class{constructor(){images.push(this)}decode(){return Promise.resolve()}}};
 for(const name of ['features/purchase/reference-store.js','features/purchase/reference-media.js'])vm.runInNewContext(fs.readFileSync(path.join(root,'web/js',name),'utf8').replace(/^import .*;\r?\n/gm,'').replaceAll('export ',''),scope);
 vm.runInNewContext('globalThis.h={seedReferences,referenceState,purchaseReference,createReferenceController}',scope);return {...scope.h,images};}
const d={id:'d'},row={id:'photo',image_path:'reference-'+'1'.repeat(64)+'.png',source_url:'https://shop.example.com/item',name:'实物'},state=(id,status)=>({job_id:id,status,issued_at:1,references:[row]});
(async()=>{
 let passed=0;
 {const h=factory();h.seedReferences({d:state('original','complete')});assert.equal(h.purchaseReference(d),null);h.images[0].onload();await Promise.resolve();assert.equal(h.purchaseReference(d).id,'photo');passed++;}
 {let posts=0,gets=0;const h=factory({async purchaseReferenceStart(){posts++;return state('new-job','queued')},async purchaseReferenceState(){gets++;return state('new-job','complete')}});h.seedReferences({d:state('old','complete')});h.purchaseReference(d);h.images[0].onerror();const c=h.createReferenceController(()=>{});await c.start({id:'p'},d,true);h.purchaseReference(d);h.images[1].onerror();assert.equal(h.referenceState(d).status,'queued');await c.query();assert.equal(h.purchaseReference(d),null);assert.equal(h.images.length,3);h.images[2].onload();await Promise.resolve();assert.equal(h.purchaseReference(d).id,'photo');assert.equal(posts,1);assert.equal(gets,1);c.dispose();passed++;}
 {const h=factory();h.seedReferences({d:state('same','running')});h.purchaseReference(d);const old=h.images[0];h.seedReferences({d:state('same','complete')});h.purchaseReference(d);old.onerror();assert.equal(h.referenceState(d).status,'complete');assert.equal(h.referenceState(d).references.length,1);h.images[1].onload();await Promise.resolve();assert.equal(h.purchaseReference(d).id,'photo');passed++;}
 console.log(JSON.stringify({passed,failed:0}));
})().catch(e=>{console.error(e);process.exitCode=1});
