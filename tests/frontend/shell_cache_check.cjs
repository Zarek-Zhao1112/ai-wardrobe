// SW故障注入：原生内存缓存，无网络、浏览器或用户数据。
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert/strict');
const code=fs.readFileSync(path.resolve(__dirname,'../../web/sw.js'),'utf8');
function worker({fail=()=>false,revision=null}={}){
 const buckets=new Map(),events=new Map(),deleted=[];let skipped=0;
 const key=r=>typeof r==='string'?r:r.url;
 const cache=()=>{const entries=new Map();return{entries,async add(r){const u=key(r);if(fail(u))throw Error('synthetic offline');entries.set(u,new Response('network '+u))},async put(r,v){entries.set(key(r),v)},async match(r){return entries.get(key(r))?.clone()}}};
 const caches={async has(k){return buckets.has(k)},async open(k){if(!buckets.has(k))buckets.set(k,cache());return buckets.get(k)},async keys(){return[...buckets.keys()]},async delete(k){deleted.push(k);return buckets.delete(k)}};
 const self={location:{origin:'http://localhost'},clients:{async claim(){}},addEventListener(k,f){events.set(k,f)},async skipWaiting(){skipped++}};
 const scope={self,caches,Response,AbortController,setTimeout,clearTimeout,console,URL,fetch:async()=>{throw Error('offline')},Request:class{constructor(url,options){this.url=key(url);Object.assign(this,options)}}};
 vm.runInNewContext((revision?code.replace(/const ASSET_REVISION = "[a-f0-9]+"/,`const ASSET_REVISION = "${revision}"`):code)+';globalThis.h={SHELL,SHELL_ASSETS,COMPLETE,matchShellAnywhere}',scope);
 async function event(name){let promise;events.get(name)({waitUntil(p){promise=p}});return promise}
 return{...scope.h,buckets,caches,deleted,event,skipped:()=>skipped};
}
(async()=>{let passed=0;
 {const w=worker({fail:u=>u==='/js/core/app.js'});const old=await w.caches.open('wzbbti-shell-v46');await old.put('/',new Response('old HTML'));await assert.rejects(w.event('install'));assert.equal(w.skipped(),0);assert(w.buckets.has('wzbbti-shell-v46'));assert(!w.buckets.has(w.SHELL));passed++}
 {const w=worker({fail:()=>true});const active=await w.caches.open(w.SHELL);await active.put('/',new Response('active HTML'));await active.put('/sentinel',new Response('preserve'));await assert.rejects(w.event('install'));assert(w.buckets.has(w.SHELL));assert.equal(await(await active.match('/sentinel')).text(),'preserve');assert.equal(w.skipped(),0);passed++}
 {const w=worker({fail:()=>true});const active=await w.caches.open(w.SHELL);for(const u of w.SHELL_ASSETS)await active.put(u,new Response('same revision '+u));await w.event('install');assert.equal(w.skipped(),1);assert(await active.match(w.COMPLETE));assert.equal(await(await active.match('/')).text(),'same revision /');passed++}
 {const w=worker({fail:u=>u==='/js/core/app.js',revision:'different-build'});const old=await w.caches.open('wzbbti-shell-v47-previous');await old.put('/',new Response('prior HTML'));await old.put(w.COMPLETE,new Response('previous'));await assert.rejects(w.event('install'));assert.equal(await(await w.matchShellAnywhere('/')).text(),'prior HTML');passed++}
 {const w=worker();const bad=await w.caches.open('wzbbti-shell-v47-partial');await bad.put('/',new Response('incomplete HTML'));const old=await w.caches.open('wzbbti-shell-v46');await old.put('/',new Response('legacy HTML'));assert.equal(await(await w.matchShellAnywhere('/')).text(),'legacy HTML');await w.event('install');await w.event('activate');assert(await(await w.caches.open(w.SHELL)).match(w.COMPLETE));assert(!w.buckets.has('wzbbti-shell-v46'));passed++}
 console.log(JSON.stringify({passed,failed:0}));
})().catch(error=>{console.error(error);process.exitCode=1});
