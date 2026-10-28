// §25：添置替代旧缺口面板；读取不收费，只有用户明确点击才创建分析。
import { api } from './api.js';
import { esc, icon, serviceSettingsHtml } from './ui.js';
import { purchaseArt } from './purchase-art.js';
import { referenceLabel, referencePreparation, referenceStatus, purchaseReference } from './purchase-reference.js';
import { createReferenceController } from './purchase-reference-store.js';
import { openPurchaseSheet, refreshPurchaseSheetReferences } from './purchase-sheet.js';
export const label='添置';
let inFlight=null,activePage=null;
const modeKey='purchasePageMode:v1';
let preferredMode='result';
function preference(){try{return localStorage.getItem(modeKey)||preferredMode;}catch{return preferredMode;}}
function remember(mode){preferredMode=mode;try{localStorage.setItem(modeKey,mode);}catch{}}
// §25.4：路由卸载时清理监听和动画，迟到的请求只更新服务端结果。
export function unmount(root){if(activePage?.root===root){activePage.dispose();activePage=null;}}
const options=[{id:'auto',name:'综合判断',summary:'结合现有衣橱，寻找值得添置的一件'}];
const uuid=()=>crypto.randomUUID?.()||'10000000-1000-4000-8000-100000000000'.replace(/[018]/g,c=>(Number(c)^crypto.getRandomValues(new Uint8Array(1))[0]&15>>Number(c)/4).toString(16));
function photo(item){const url=api.presentationUrl(item);return url?`<img src="${esc(url)}" data-flatlay-source="${esc(url)}" alt="${esc(item.name||item.type_label||'已有衣物')}">`:`<span class="purchase-ph">${esc(item.type_label||'暂无平铺图')}</span>`;}
export async function mount(root,ctx){
 if(activePage)unmount(activePage.root);
 root.innerHTML=`<section class="purchase-view"><div class="purchase-heading page-actions"><span></span><div class="page-settings status">${serviceSettingsHtml(ctx.health)}</div></div><div class="purchase-content"><p class="faint" role="status">正在读取添置建议…</p></div></section>`;
 let data=null,profiles=options,aesthetic='auto',selected=false,mode='home',savedOrigin='home';
 let busy=false,ready=false,message='',error=false,epoch=0,disposed=false,navigation=0,returning=false;
 let animations=[],ghost=null,timer=null,referencesChanged=false;
 const positions={home:{y:0,content:0},result:{y:0,content:0},saved:{y:0,content:0}},hash=location.hash;
 const alive=()=>!disposed&&root.isConnected;
 const working=()=>busy||Boolean(inFlight)||data?.request?.status==='running';
 const references=createReferenceController(()=>{
  if(!alive())return;
  if(document.querySelector('.purchase-layer')){referencesChanged=true;refreshPurchaseSheetReferences();return;}
  const node=document.activeElement,focused=node?.dataset.referenceDirection||node?.dataset.direction;
  const method=node?.dataset.method,generate=node?.matches('[data-purchase-generate]'),chooserOpen=root.querySelector('.purchase-chooser')?.open;
  render();if(chooserOpen&&root.querySelector('.purchase-chooser'))root.querySelector('.purchase-chooser').open=true;
  const target=focused?root.querySelector('[data-reference-direction="'+focused+'"],[data-direction="'+focused+'"]'):method?root.querySelector('[data-method="'+method+'"]'):generate?root.querySelector('[data-purchase-generate]'):null;
  if(target)target.focus({preventScroll:true});
 });
 function acceptData(next){data=next;references.seed(data?.references||{},[data?.result,...(data?.saved||[])].filter(Boolean).flatMap(p=>p.directions.map(d=>d.id)));}
 function prepareVisible(){
  if(!alive()||!ready||mode==='home')return;
  const plans=mode==='saved'?data?.saved||[]:[data?.result];
  plans.filter(Boolean).forEach(plan=>{if(plan.directions.some(d=>referenceStatus(d).status==='idle'))void references.ensure(plan);});
 }
 function searchReference(plan,direction){
  if(referenceStatus(direction).needsEnsure)return references.ensure(plan,true);
  if(referenceStatus(direction).busy)return references.query();
  return references.start(plan,direction,referenceStatus(direction).status!=='idle');
 }
 const referenceClick=event=>{
  const button=event.target.closest('[data-reference-action]');if(!button)return;
  const plan=[data?.result,...(data?.saved||[])].find(p=>p&&p.id===button.dataset.referencePlan&&p.directions.some(d=>d.id===button.dataset.referenceDirection));
  const direction=plan?.directions.find(d=>d.id===button.dataset.referenceDirection);
  if(direction)void searchReference(plan,direction);
 };
 root.addEventListener('click',referenceClick);
 const savedIds=()=>new Set((data?.saved||[]).flatMap(p=>p.directions.map(d=>d.id)));
 const baseState=()=>{const state={...history.state};delete state.purchasePage;delete state.purchaseOrigin;delete state.purchaseDetail;delete state.bridgeSettings;return state;};
 function stopMotion(){clearTimeout(timer);animations.forEach(a=>a.cancel());animations=[];ghost?.remove();ghost=null;}
 function showPage(next,{fromHistory=false,animate=true}={}){
  if(!alive()||next===mode)return;
  positions[mode]={y:scrollY,content:root.querySelector('.purchase-content')?.scrollTop||0};
  const old=root.querySelector('.purchase-view'),rect=old?.getBoundingClientRect(),copy=animate?old?.cloneNode(true):null;
  const previous=mode;mode=next;++navigation;returning=false;
  if(next!=='saved')remember(next);
  if(!fromHistory&&next!=='home'){
   if(next==='result'&&previous==='saved')history.replaceState({...baseState(),purchasePage:'home'},'',location.href);
   history.pushState({...baseState(),purchasePage:next,...(next==='saved'?{purchaseOrigin:savedOrigin}:{})},'',location.href);
  }
  render();window.scrollTo(0,positions[next].y);
  root.querySelector('.purchase-content').scrollTop=positions[next].content;
  const focus=root.querySelector(next==='home'?'[data-purchase-last],[data-purchase-generate]':'[data-purchase-return]');
  focus?.focus({preventScroll:true});
  const current=root.querySelector('.purchase-view');
  if(!copy||!current?.animate||matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  ghost=document.createElement('div');ghost.className='purchase-page-copy';ghost.setAttribute('aria-hidden','true');
  const nav=document.querySelector('#tabbar'),navRect=nav?.getBoundingClientRect();
  if(navRect?.height)ghost.style.bottom=Math.max(0,innerHeight-navRect.top)+'px';
  Object.assign(copy.style,{position:'absolute',top:rect.top+'px',left:rect.left+'px',width:rect.width+'px',margin:'0',pointerEvents:'none'});
  copy.querySelectorAll('button,a,summary,input,select,[tabindex]').forEach(n=>n.tabIndex=-1);
  ghost.append(copy);document.body.append(ghost);
  copy.querySelector('.purchase-content').scrollTop=positions[previous].content;
  const options={duration:300,fill:'both',easing:'cubic-bezier(.22,.75,.24,1)'};
  animations=[copy.animate([{opacity:1,transform:'none'},{opacity:0,transform:previous==='home'?'translateY(-6px) scale(1.015)':'translateY(8px) scale(.975)'}],options),current.animate([{opacity:0,transform:'translateY(8px)'},{opacity:1,transform:'none'}],options)];
  timer=setTimeout(stopMotion,320);
 }
 function goBack(){
  if(!alive()||mode==='home'||returning)return;
  if(history.state?.purchasePage===mode){returning=true;history.back();}
  else showPage(mode==='saved'?savedOrigin:'home',{fromHistory:true});
 }
 const pop=()=>{
  if(!alive()||location.hash!==hash||history.state?.purchaseDetail||document.querySelector('.purchase-layer,.bridge-modal,.import-modal'))return;
  returning=false;
  const target=history.state?.purchasePage;
  if(target==='saved')savedOrigin=history.state.purchaseOrigin==='result'&&data?.result?'result':'home';
  showPage(target==='saved'?'saved':target==='result'&&data?.result?'result':'home',{fromHistory:true});
 };
 const key=event=>{
  if(event.key!=='Escape'||event.defaultPrevented||!alive()||mode==='home'||document.querySelector('.purchase-layer,.bridge-modal,.import-modal,.status-menu[open],.purchase-chooser[open]'))return;
  event.preventDefault();goBack();
 };
 window.addEventListener('popstate',pop);document.addEventListener('keydown',key);
 activePage={root,dispose(){disposed=true;++epoch;stopMotion();references.dispose();root.removeEventListener('click',referenceClick);window.removeEventListener('popstate',pop);document.removeEventListener('keydown',key);}};
 function chooser(){return `<details class="purchase-chooser"><summary><span>审美方向</span><strong>${esc(profiles.find(p=>p.id===aesthetic)?.name||'综合判断')}${icon('chevron')}</strong></summary><div class="purchase-choices">${profiles.map(p=>`<button type="button" data-method="${esc(p.id)}" aria-pressed="${p.id===aesthetic}" ${working()?'disabled':''}><span><b>${esc(p.name)}</b><small>${esc(p.summary)}</small></span>${p.id===aesthetic?icon('check'):''}</button>`).join('')}</div></details>`;}
 function generateButton(text='AI 推荐添置'){return `<button type="button" class="purchase-primary" data-purchase-generate ${!ready||working()||!data?.total?'disabled':''}>${icon('spark')}<span>${working()?'正在分析衣橱…':text}</span></button>`;}
 function initial(){const ids=data?.hero||[];return `<div class="purchase-collage">${ids.slice(0,3).map((id,i)=>`<div class="purchase-mini mini-${i}">${photo(data.items[id])}</div>`).join('')}${!ids.length?`<div class="purchase-empty-art">${icon('wardrobe')}</div>`:''}<span class="purchase-collage-spark">${icon('spark')}</span></div><div class="purchase-hero-copy"><h2>让下一件，更好搭。</h2><p>找到值得添置的衣服方向，<br>连同真实商品图一起给你。</p></div>${chooser()}${generateButton()}<p class="purchase-micro">${data?.total?'每次最多 3 个方向，也可能建议你暂时不用买。':'先录入几件已有衣物，让建议更有依据。'}${!data?.total?'<br><a href="#add">去录入衣物</a>':''}</p>${data?.result?`<button type="button" class="purchase-last" data-purchase-last>${icon('history')}<span>查看上次建议</span></button>`:''}`;}
 function card(plan,d,index=0){
  if(!purchaseReference(d))return referencePreparation(d,plan,index>0);
  const existing=d.owned.map(id=>data.items[id]).filter(Boolean),isSaved=savedIds().has(d.id);
  const content=index?`<span class="purchase-small-art">${purchaseArt(d,ctx.taxonomy)}</span><span class="purchase-secondary-copy"><strong>${esc(d.title)}</strong><small>${esc(d.color)}</small></span>${icon('right')}`:
   `<div class="purchase-reference-hero">${purchaseArt(d,ctx.taxonomy)}<span class="purchase-reference-label">${referenceLabel(d)}</span></div><div class="purchase-reference-copy"><span class="purchase-badge">${isSaved?'已记下':plan.directions[0]?.id===d.id?'建议优先看看':'也可以考虑'}</span><div class="purchase-reference-title"><h2>${esc(d.title)}</h2><span>${icon('right')}</span></div><p class="purchase-reason">${esc(d.reason)}</p><div class="purchase-reference-tags"><span>${esc(d.color)}</span><span>${esc(d.fit)}</span></div><div class="purchase-owned-link"><span class="purchase-thumbs">${existing.slice(0,3).map(it=>`<span>${photo(it)}</span>`).join('')}</span><span>看看怎么搭${icon('right')}</span></div></div>`;
  return `<button type="button" class="purchase-card ${index?'purchase-secondary':'purchase-reference-card'}" data-plan="${esc(plan.id)}" data-direction="${esc(d.id)}" aria-haspopup="dialog" ${working()?'disabled':''}>${content}</button>`;
 }
 function results(){const plan=data.result;if(mode==='saved')return `<p class="purchase-sub">记下的选购方向，慢慢挑选就好。</p><div class="purchase-saved-list">${data.saved.length?data.saved.flatMap(p=>p.directions.map(d=>card(p,d,1))).join(''):'<p class="purchase-empty-copy">还没有记下的方向。打开建议后，点击「记下这个方向」。</p>'}</div><button class="purchase-back" data-purchase-all>${savedOrigin==='result'?'返回推荐结果':'返回添置初始页'}</button>`;
 if(plan.decision==='enough')return `<div class="purchase-meta"><span>${icon('spark')}${esc(plan.aesthetic.name)}</span><span>基于现有衣橱</span></div><div class="purchase-enough">${icon('check')}<h2>${esc(plan.headline)}</h2><p>${esc(plan.summary)}</p><div class="purchase-enough-photos">${(data.hero||[]).slice(0,3).map(id=>`<div class="purchase-pair-piece">${photo(data.items[id])}</div>`).join('')}</div><a class="purchase-primary" href="#outfits">${icon('outfits')}看看已有衣服怎么搭</a></div>${chooser()}${generateButton('重新推荐')}`;
 const complete=plan.directions.filter(d=>purchaseReference(d)),waiting=plan.directions.filter(d=>!purchaseReference(d));
 return `<p class="purchase-prepared-count" role="status">${complete.length?complete.length+' 个方向已准备好':'正在为你准备建议'}</p>${complete.map((d,i)=>(i===1?'<div class="purchase-other">也可以看看</div>':'')+card(plan,d,i)).join('')}${waiting.map((d,i)=>(complete.length||i?'<div class="purchase-other">'+(referenceStatus(d).busy||referenceStatus(d).status==='idle'?'另一条建议正在准备':'另一条建议暂未完成')+'</div>':'')+referencePreparation(d,plan,complete.length>0||i>0)).join('')}<p class="purchase-micro purchase-result-note">${complete.length?'看款式方向，具体商品由你自己挑。':'图文配齐后，建议会在这里出现。'}</p>${chooser()}${generateButton('重新推荐')}`;}
 function render(){
  if(!alive())return;stopMotion();const y=scrollY,contentY=root.querySelector('.purchase-content')?.scrollTop||0,showResult=mode==='result'&&data?.result;
  const backLabel=mode==='saved'&&savedOrigin==='result'?'返回推荐结果':'返回添置初始页';
  root.innerHTML=`<section class="purchase-view ${mode!=='home'?'has-result':''}" data-purchase-page="${mode}" aria-busy="${working()}"><div class="purchase-heading page-actions"><div class="purchase-heading-left">${mode!=='home'?`<button type="button" class="purchase-return" data-purchase-return aria-label="${backLabel}" title="${backLabel}">${icon('back')}</button>`:''}<button type="button" class="purchase-saved-button" data-purchase-saved ${working()||!ready?'disabled':''}>${icon('bookmark')}已记下 ${savedIds().size}</button></div><div class="page-settings status">${serviceSettingsHtml(ctx.health)}</div></div><div class="purchase-content"><div class="purchase-message ${error?'is-error':''}" role="status" aria-live="polite">${esc(message)}</div>${!ready&&!data?.result?'<button data-purchase-query>重新读取</button>':`${data?.stale&&showResult?'<p class="purchase-stale">衣橱已变化，以下是上次的建议；可以重新分析。</p>':''}${showResult||mode==='saved'?results():initial()}${data?.request?.status==='running'||!ready?'<button class="purchase-query" data-purchase-query>查询这次结果</button>':''}`}</div></section>`;
  window.scrollTo(0,y);root.querySelector('.purchase-content').scrollTop=contentY;
  root.querySelector('[data-purchase-return]')?.addEventListener('click',goBack);
  root.querySelector('[data-purchase-last]')?.addEventListener('click',()=>showPage('result'));
  root.querySelectorAll('[data-purchase-generate]').forEach(b=>b.onclick=generate);
  root.querySelectorAll('[data-purchase-query]').forEach(b=>b.onclick=()=>load());
  root.querySelector('[data-purchase-saved]')?.addEventListener('click',()=>{if(mode==='saved')goBack();else{savedOrigin=mode;showPage('saved');}});
  root.querySelector('[data-purchase-all]')?.addEventListener('click',goBack);
  root.querySelectorAll('[data-method]').forEach(b=>b.onclick=()=>{if(working())return;aesthetic=b.dataset.method;selected=true;render();root.querySelector('.purchase-chooser>summary')?.focus({preventScroll:true});});
  root.querySelectorAll('[data-direction]').forEach(b=>b.onclick=()=>{
   if(working())return;
   const plan=[data.result,...data.saved].find(p=>p&&p.id===b.dataset.plan&&p.directions.some(d=>d.id===b.dataset.direction)),direction=plan?.directions.find(d=>d.id===b.dataset.direction);
   if(!direction||!purchaseReference(direction))return;let changed=false;
   void openPurchaseSheet({root,anchor:b,direction,plan,items:data.items,taxonomy:ctx.taxonomy,saved:savedIds().has(direction.id),onReferenceAction:()=>searchReference(plan,direction),onReferenceSelect:()=>{referencesChanged=true;},onChoice:async(action)=>{++epoch;const next=await api.purchaseChoice({plan_id:plan.id,direction_id:direction.id,action});++epoch;acceptData(next);changed=true;},onClose:()=>{if(changed||referencesChanged){referencesChanged=false;render();(root.querySelector('[data-direction="'+direction.id+'"],[data-reference-direction="'+direction.id+'"]')||root.querySelector('[data-direction],[data-purchase-all],[data-purchase-saved],[data-purchase-generate]'))?.focus({preventScroll:true});}pop();},onDismiss:()=>{void generate();}});
  });
  prepareVisible();
 }
 function requestMessage(){const request=data?.request;message=request?.status==='running'?'正在分析衣橱，离开页面后可回来查询结果。':request?.status==='failed'||request?.status==='interrupted'?request.error||'上次分析未成功，已有建议保留。':'';error=request?.status==='failed'||request?.status==='interrupted';}
 async function load(){
  const ticket=++epoch;
  try{const next=await api.purchaseState();if(!alive()||ticket!==epoch)return;acceptData(next);ready=true;error=false;if(!selected&&data.result)aesthetic=data.result.aesthetic.id;requestMessage();}
  catch(err){if(!alive()||ticket!==epoch)return;message='暂时读不到添置建议：'+err.message;error=true;ready=false;}
  render();
 }
 async function generate(){
  if(working()||!ready||!data?.total)return;
  ++epoch;const startedAt=navigation;let reveal=false;busy=true;message='正在结合已有衣物，寻找值得添置的一件…';error=false;render();
  const requested=aesthetic,pending=api.purchaseGenerate({request_id:uuid(),aesthetic:requested});inFlight=pending;
  try{
   const next=await pending;if(!alive())return;++epoch;acceptData(next);
   message=next.request?.status==='failed'?next.request.error:'';error=next.request?.status==='failed';
   reveal=Boolean(data.result&&!error&&startedAt===navigation&&mode!=='result');
  }catch(err){
   if(alive()){++epoch;message='这次推荐未成功：'+err.message+'。已有建议保留。';error=true;
    try{const recovery=await api.purchaseState();if(!alive())return;++epoch;acceptData(recovery);if(data.request?.status==='running')message='暂未收到最终结果，请查询这次分析；不会自动重新调用。';}
    catch{ready=false;message+=' 请先重新读取，确认这次结果。';}
   }
  }finally{if(inFlight===pending)inFlight=null;busy=false;if(reveal&&startedAt===navigation)showPage('result');else render();}
 }
 const loads=await Promise.allSettled([api.aesthetics(),api.purchaseState()]);
 if(!alive())return;
 if(loads[0].status==='fulfilled')profiles=[...options,...loads[0].value.aesthetics];
 if(loads[1].status==='fulfilled'){
  acceptData(loads[1].value);ready=true;if(data.result)aesthetic=data.result.aesthetic.id;requestMessage();
  // 初始页是历史的落点；同 hash 结果层让 Android 返回先收起推荐。
  const entry=history.state?.purchasePage;
  const known=entry==='home'||entry==='saved'||entry==='result'&&data.result;
  if(!known&&history.state?.bridgeSettings){mode='home';}
  else if(known){mode=entry;if(mode==='saved')savedOrigin=history.state.purchaseOrigin==='result'&&data.result?'result':'home';}
  else{
   mode=data.result&&preference()!=='home'?'result':'home';
   history.replaceState({...baseState(),purchasePage:'home'},'',location.href);
   if(mode==='result')history.pushState({...baseState(),purchasePage:'result'},'',location.href);
  }
 }else{message='暂时读不到添置建议：'+loads[1].reason.message;error=true;}
 render();
 if(inFlight)void inFlight.catch(()=>{}).finally(()=>{if(alive())void load();});
}
