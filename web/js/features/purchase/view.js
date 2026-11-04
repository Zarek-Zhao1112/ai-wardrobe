/** §37：首页保持稳定，真实分析、选方向与结果均在B玻璃助手内。 */
import {api} from '../../core/api.js';
import {esc,icon,toast} from '../../core/ui.js';
import {createAestheticSheet} from '../outfits/aesthetic-sheet.js';
import {coverImage} from '../outfits/aesthetic-ui.js';
import {createAssistantPanel} from './assistant-panel.js';
import {intentSession as session} from './intent-session.js';
import {seedReferences,subscribeReferenceMedia} from './reference-store.js';
import {purchaseReference,referenceStatus} from './reference.js';
import {openPurchaseSheet,refreshPurchaseSheetReferences} from './sheet.js';
import {initialHtml,assistantHtml,analyzingHtml,preparingHtml,productCardHtml,resultsHtml} from './intent-ui.js';
export const label='添置';
let active=null,lastReady=null;
export function unmount(root){if(active?.root===root){active.dispose();active=null}}
const blocked=()=>Boolean(document.querySelector('.purchase-layer,.aesthetic-layer,.bridge-modal,.import-modal'));
export async function mount(root,ctx){
 if(active)unmount(active.root);
 let disposed=false,ready=false,mode='home',draftId=null,resultPlanId=null,profiles=[],sheet=null,timer=null,ticket=0,dirty=false,composing=false,assistantId=null,depth=0,pending=null,returning=false,homeHtml='',starting=false;
 // §41：一屏一张审美卡，横滑到第几张由这里记 —— paint() 会整块重建首页，
 //   重建后必须把 scrollLeft 还回去，否则点卡→分析→返回会跳回第一张。
 let aestheticIndex=0,scrollTick=0;
 const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
 const hash=location.hash,abort=new AbortController(),positions=new Map(),alive=()=>!disposed&&root.isConnected;
 const currentDraft=()=>session.draft(draftId)||session.data?.analysis;
 const profile=()=>profiles.find(p=>p.id===session.style)||profiles.find(p=>p.id==='natural')||profiles[0];
 const analysisProfile=()=>profiles.find(p=>p.id===session.data?.analysis_request?.aesthetic?.id)||profile();
 const stateKey=()=>JSON.stringify([history.state?.purchasePage,history.state?.purchaseDraft,history.state?.purchasePlan,history.state?.purchaseRequest,history.state?.purchaseAssistant]);
 const editing=()=>Boolean(panel.content?.contains(document.activeElement)&&document.activeElement?.matches('[data-intent-note]'));
 const baseState=()=>{const value={...history.state};for(const k of ['purchasePage','purchaseDraft','purchasePreparation','purchaseRequest','purchasePlan','purchaseOrigin','purchaseDetail','aestheticSheet','bridgeSettings','purchaseAssistant'])delete value[k];return value};
 const savedIds=()=>new Set((session.data?.saved||[]).flatMap(p=>p.directions.map(d=>d.id)));
 const key=()=>mode+':'+(draftId||resultPlanId||'');
 const panel=createAssistantPanel({root,onClose:closeAll,onBack:back,isBlocked:blocked});
 function fullPlan(){for(const plan of [session.data?.result,...[...(session.data?.result_history||[])].reverse(),lastReady].filter(Boolean)){if(resultPlanId&&plan.id!==resultPlanId)continue;if(plan.decision==='enough'||plan.directions?.length&&plan.directions.every(d=>purchaseReference(d))){lastReady=plan;return plan}}return null}
 function preparedPlan(){return [session.data?.result,...(session.data?.result_history||[])].find(p=>p?.id===session.data?.preparation?.plan_id)}
 function readyPreparation(){const p=preparedPlan();return Boolean(p?.directions?.length&&p.directions.every(d=>purchaseReference(d)))}
 function readyDraft(){const draft=currentDraft();return draft?.workflow_version===2?{...draft,directions:draft.directions.filter(d=>purchaseReference(d))}:null}
 function selection(){const d=currentDraft();return d?session.selection(d.id):{ids:[],note:'',expanded:false}}
 function acceptRefs(){seedReferences(session.data?.references||{})}
 function defer(){dirty=true;clearTimeout(timer);timer=setTimeout(()=>{if(alive())changed()},350)}
 function header(){const d=currentDraft(),p=mode==='result'?fullPlan()?.aesthetic:mode==='assistant'?d?.aesthetic:mode==='analyzing'?analysisProfile():profile(),match=profiles.find(row=>row.id===p?.id),cover=match?coverImage(match):'';return `${['result','saved','processing'].includes(mode)?`<button type="button" class="pi-head-back" data-assistant-back aria-label="返回上一层">${icon('back')}</button>`:''}<div class="pi-head-style">${cover?`<img src="${esc(cover)}" alt="">`:icon('spark')}<span><strong>${esc(p?.name||'添置助手')}</strong><small>结合 ${session.data?.total||0} 件已有衣物</small></span></div><button type="button" class="pi-head-close" data-assistant-close aria-label="关闭添置助手">${icon('close')}</button>`}
 function body(){
  const data=session.data,draft=currentDraft(),ready=readyDraft();
  if(mode==='analyzing')return analyzingHtml(analysisProfile(),data?.analysis_request);
  if(mode==='assistant'){
   if(draft?.workflow_version!==2)return '<div class="pi-empty"><h2>先为衣物方向生成示意。</h2><p>重新分析后，款式示意与衣物方向会一起给你。</p><button type="button" class="purchase-primary primary" data-purchase-analyze>按这个审美重新分析</button></div>';
   if(draft.decision!=='enough'&&!ready?.directions.length){const failed=draft.directions.some(d=>referenceStatus(d).status==='failed');return failed?'<div class="pi-empty"><h2>这组款式图片暂时无法读取。</h2><p>已有建议仍然保留，可以重新分析其他款式。</p><button type="button" class="purchase-primary primary" data-purchase-analyze>重新分析</button></div>':analyzingHtml(profile(),{status:'running',phase:'decoding'})}
   const waiting=data.analysis_request?.pending_reference_count?'<p class="pi-analysis-hint">还有部分生成等待确认。<button type="button" class="quiet blue" data-query-analysis>查询原任务</button></p>':'';
   return waiting+assistantHtml({draft:ready,selection:{...selection(),ids:selection().ids.filter(id=>ready.directions.some(d=>d.id===id))},items:data.items||{},preparing:session.preparing});
  }
  if(mode==='processing'){const p=preparedPlan();return preparingHtml(data?.preparation,{photosReady:readyPreparation(),photosFailed:Boolean(p?.directions.some(d=>referenceStatus(d).status==='failed'))})}
  if(mode==='saved'){const rows=(data?.saved||[]).flatMap(plan=>plan.directions.filter(d=>purchaseReference(d)).map(d=>({plan,d})));return `<h2 class="screen-title">记下的方向</h2><p class="result-description">挑选时再慢慢比较。</p>${rows.length?rows.map(({plan,d},i)=>productCardHtml(plan,d,i,data.items)).join(''):'<p class="saved-empty">暂时没有可显示的款式参考，记录仍然保留。</p>'}`}
  return resultsHtml(fullPlan(),data?.items||{});
 }
/** 顶栏右侧的「已记下 N」。它原来独占首页首行（min-height 44 + margin 14），
 *  §42 把那 58px 整条删掉让给封面，按钮搬到顶栏的 #topbar-action。
 *  只在首页（mode==='home'）出现；进助手/结果帧就清空，顶栏回到「标题 + 齿轮」。
 *  ⚠️ 按钮不在 #view 里，本模块绑在 root 上的 click 委托收不到 ——
 *     要在 bind 那一段给 #topbar-action 单独挂同一个 click。 */
function syncTopbar(){
 const slot=document.getElementById('topbar-action');
 if(!slot)return;
 if(!alive()||mode!=='home'){if(slot.innerHTML)slot.innerHTML='';return}
 const html=`<button type="button" class="purchase-saved-button saved-link" data-purchase-saved>${icon('bookmark')}已记下 ${savedIds().size}</button>`;
 if(slot.innerHTML!==html)slot.innerHTML=html;
}
 function renderHome(){
  const focus=root.contains(document.activeElement),data=session.data;
  const content=ready?initialHtml({data,profiles,analyzing:session.analyzing,preparation:data?.preparation,photosReady:readyPreparation()}):'<p class="faint">正在读取添置建议…</p><button type="button" class="quiet blue" data-purchase-query>重新读取</button>';
  const html=`<section class="purchase-view purchase-intent" data-purchase-page="home"><div class="purchase-content is-aesthetic-home">${session.message?`<p class="global-notice" role="status">${esc(session.message)}</p>`:''}${content}</div></section>`;
  if(homeHtml!==html){root.innerHTML=html;homeHtml=html;syncTopbar();restoreDeck();if(focus)root.querySelector('[data-aesthetic-open],[data-purchase-query]')?.focus({preventScroll:true})}
 }
 /** 圆点与 deck 的当前页对齐。index 越界会被夹住 —— profiles 是异步来的，可能比上次少。 */
 function syncAesthetic(index){
  aestheticIndex=Math.max(0,Math.min(index,Math.max(0,profiles.length-1)));
  const dots=root.querySelector('[data-aesthetic-dots]');if(!dots)return;
  [...dots.querySelectorAll('[data-aesthetic-dot]')].forEach((dot,i)=>dot.setAttribute('aria-pressed',String(i===aestheticIndex)));
  // 页码跟着圆点走。没有这枚 span（搭配页、或 profiles 还没来）就跳过。
  const page=dots.querySelector('[data-aesthetic-page]');if(page)page.textContent=(aestheticIndex+1)+'/'+Math.max(1,profiles.length);
 }
 /** 重建首页后把横滑位置还回去。同步落一次、rAF 再对一次（首帧常常还没布局完）。 */
 function restoreDeck(){
  const deckEl=root.querySelector('[data-aesthetic-deck]');if(!deckEl)return;
  const apply=()=>{if(!alive()||!deckEl.isConnected)return;const width=deckEl.clientWidth;if(width)deckEl.scrollLeft=aestheticIndex*width;syncAesthetic(aestheticIndex)};
  apply();requestAnimationFrame(apply);
 }
 function paint({animate=false,position}={}){
  if(!alive())return;if(blocked()||composing||editing()||panel.dragging){defer();return}clearTimeout(timer);dirty=false;acceptRefs();
  syncTopbar();
  if(mode==='home'){if(panel.open){void panel.close().then(()=>{if(alive()&&mode==='home')paint()});return}renderHome();return}
  const content=body(),head=header();
  if(!panel.open){const faces=root.querySelectorAll('[data-aesthetic-open]');panel.show({header:head,content,anchor:faces[aestheticIndex]||faces[0]||root.querySelector('[data-purchase-query]')});bind(panel.content)}
  else if(!panel.render({header:head,content,animate})){defer();return}
  panel.layer.dataset.purchasePage=mode;
  if(position!==undefined)panel.content.scrollTop=position;
 }
 function change(next,{fromHistory=false,replace=false,extra={}}={}){
  if(!alive())return;if(blocked()){pending={next,options:{fromHistory,replace,extra},ticket,key:stateKey()};defer();return}
  pending=null;returning=false;positions.set(key(),panel.content?.scrollTop||0);++ticket;mode=next;if(next==='home'){resultPlanId=null;assistantId=null;depth=0}else if(next==='result')resultPlanId=extra.purchasePlan||resultPlanId;
  if(!fromHistory){if(next!=='home'){if(!assistantId){assistantId='pi-'+Date.now()+'-'+Math.random().toString(36).slice(2,7);depth=0;history.replaceState({...baseState(),purchasePage:'home'},'',location.href)}depth=replace?Math.max(1,depth):depth+1}const value={...baseState(),purchasePage:next,...(draftId?{purchaseDraft:draftId}:{}),...(assistantId?{purchaseAssistant:{id:assistantId,depth}}:{}),...extra};history[replace?'replaceState':'pushState'](value,'',location.href)}
  paint({animate:true,position:positions.get(key())||0});if(next!=='home')panel.focus();
 }
 function closeAll(){if(mode==='home'||returning)return;returning=true;const marker=history.state?.purchaseAssistant;if(marker?.id===assistantId&&Number.isInteger(marker.depth)&&marker.depth>0&&history.length>marker.depth)history.go(-marker.depth);else{history.replaceState({...baseState(),purchasePage:'home'},'',location.href);change('home',{fromHistory:true})}}
 function back(){if(returning)return;const marker=history.state?.purchaseAssistant;if(marker?.id===assistantId&&marker.depth>1){returning=true;history.back()}else closeAll()}
 async function syncHistory(){
  if(!alive()||location.hash!==hash)return;if(blocked()||history.state?.purchaseDetail){defer();return}
  const run=++ticket,state=history.state||{},identity=stateKey(),next=['assistant','analyzing','processing','result','saved'].includes(state.purchasePage)?state.purchasePage:'home';let wanted=state.purchaseDraft||null;
  if(wanted&&!session.draft(wanted)){try{await session.loadDraft(wanted)}catch{wanted=null}}
  if(!alive()||run!==ticket||identity!==stateKey()||location.hash!==hash)return;
  draftId=wanted;resultPlanId=state.purchasePlan||null;assistantId=state.purchaseAssistant?.id||null;depth=state.purchaseAssistant?.depth||0;change(next,{fromHistory:true});
 }
 function changed(){
  if(!alive())return;if(starting){dirty=true;return}acceptRefs();if(blocked()||composing){defer();return}
  if(pending){const p=pending;pending=null;if(p.ticket===ticket&&p.key===stateKey())change(p.next,p.options);else void syncHistory();return}
  if(history.state?.purchasePage&&history.state.purchasePage!==mode){void syncHistory();return}
  const request=session.data?.analysis_request;
  if(mode==='analyzing'&&request?.status==='completed'&&request.draft_id&&history.state?.purchaseRequest===request.id){draftId=request.draft_id;change('assistant',{replace:true});return}
  if(mode==='processing'&&history.state?.purchasePreparation===session.data?.preparation?.id&&readyPreparation()){change('result',{replace:true,extra:{purchasePlan:session.data.preparation.plan_id}});return}
  paint();
 }
 async function analyze(options={}){
  if(session.data?.analysis_request?.pending_reference_count){toast('先查询上次查找的结果，再开始新分析。','error');return}
  if(session.analyzing||!ready||!session.data?.total)return;session.clearMessage();starting=true;let promise;try{promise=session.analyze(options)}finally{starting=false}if(!session.analyzing){void promise.catch(e=>toast(e.message,'error'));return}
  change('analyzing',{extra:{purchaseRequest:session.analysisRequestId}});try{await promise}catch{}if(alive())changed();
 }
 /** §41：点审美卡 = 选定方向 + 立刻分析（原来要两步：先点面板选，再点底部的「按这个审美分析」）。
  *  ⚠️ 两种情况都不会静默失败：分析中 / 衣橱为空。原来那个按钮是 disabled 的，
  *    卡片没有 disabled 态，不说一声用户会以为点坏了。 */
 function openAesthetic(id){
  if(!profiles.some(row=>row.id===id))return;
  if(session.analyzing){toast('正在分析中，等这一轮回来再换方向。','info');return}
  if(!session.data?.total){toast('先录入几件已有衣物，建议才更有依据。','error');return}
  session.setStyle(id);void analyze();
 }
 async function choose(){
  const draft=readyDraft(),row=selection();if(!draft||session.preparing)return;
  const ids=row.ids.filter(id=>draft.directions.some(d=>d.id===id));row.ids=ids;session.setNote(draft.id,row.note);
  if(!ids.length){toast('先选一个可查看的衣物方向。','error');return}
  if(row.note.trim()){void analyze({aesthetic:draft.aesthetic.id,source_draft_id:draft.id,direction_ids:[...ids],preference:row.note});return}
  session.clearMessage();const promise=session.prepare(draft.id);if(!session.preparing){void promise.catch(e=>toast(e.message,'error'));return}
  change('processing',{extra:{purchasePreparation:session.preparationId}});const run=ticket;try{await promise}catch{}
  if(alive()&&ticket===run&&mode==='processing'&&!blocked()&&readyPreparation())change('result',{replace:true,extra:{purchasePlan:session.data.preparation.plan_id}});else if(alive())paint();
 }
 function toggle(id){const draft=readyDraft();if(!draft?.directions.some(d=>d.id===id))return;const row=selection();row.ids=row.ids.filter(key=>draft.directions.some(d=>d.id===key));session.toggle(draft.id,id);paint()}
 async function returnToAnalysis(id){if(!id){closeAll();return}const run=++ticket;try{await session.loadDraft(id);if(alive()&&run===ticket&&location.hash===hash){draftId=id;change('assistant')}}catch{toast('原分析暂时无法读取。','error')}}
 async function card(button){const data=session.data,plan=[data.result,...(data.saved||[]),...(data.result_history||[]),lastReady].find(p=>p?.id===button.dataset.plan),direction=plan?.directions.find(d=>d.id===button.dataset.direction);if(!direction||!purchaseReference(direction))return;await openPurchaseSheet({root:panel.panel,anchor:button,direction,plan,items:data.items,taxonomy:ctx.taxonomy,saved:savedIds().has(direction.id),onChoice:action=>session.choice(plan.id,direction.id,action),onClose:()=>{if(history.state?.purchasePage!==mode)void syncHistory();else{paint();panel.content?.querySelector(`[data-direction="${direction.id}"]`)?.focus({preventScroll:true})}},onDismiss:()=>void returnToAnalysis(plan.source_draft_id),changeWithoutDismiss:true})}
 const click=event=>{const b=event.target.closest('button');if(!b||b.disabled)return;const d=b.dataset;
  if(d.aestheticOpen){openAesthetic(d.aestheticOpen);return}
  if(d.aestheticGuide){const row=profiles.find(p=>p.id===d.aestheticGuide);if(row)sheet?.openGuide(row,b);return}
  if(d.aestheticDot){const deckEl=root.querySelector('[data-aesthetic-deck]'),index=Number(d.aestheticDot);if(deckEl){syncAesthetic(index);deckEl.scrollTo({left:index*deckEl.clientWidth,behavior:reducedMotion.matches?'auto':'smooth'})}return}
  if('purchaseAnalyze'in d){void analyze();return}if(d.intentDirection){toggle(d.intentDirection);return}if('purchasePrepare'in d){void choose();return}
  if('openAnalysis'in d){change('analyzing',{extra:{purchaseRequest:session.analysisRequestId}});return}
  if('purchaseHome'in d){closeAll();return}if('purchaseReturn'in d){back();return}if('purchaseSaved'in d){change('saved');return}if('openPreparation'in d){change('processing');return}
  if('resumeAnalysis'in d){draftId=session.data?.analysis?.id;change('assistant');return}if('backToAnalysis'in d){void returnToAnalysis(d.sourceDraft||session.data?.preparation?.draft_id||draftId);return}
  if('purchaseLast'in d){resultPlanId=null;const p=fullPlan();change('result',{extra:p?{purchasePlan:p.id}:{}});return}if('viewReadyResult'in d){if(readyPreparation())change('result',{extra:{purchasePlan:session.data.preparation.plan_id}});return}
  if('queryAnalysis'in d){void session.queryAnalysis().catch(e=>toast(e.message,'error'));return}if('queryPreparation'in d){void session.queryPreparation().catch(e=>toast(e.message,'error'));return}
  if('purchaseQuery'in d){void session.refresh().then(()=>{if(alive()){ready=true;paint()}}).catch(e=>toast(e.message,'error'));return}if(d.direction)void card(b);
 };
 function bind(container){container.addEventListener('click',click,{signal:abort.signal});container.addEventListener('input',event=>{if(event.target.matches('[data-intent-note]')&&currentDraft()){session.setNote(currentDraft().id,event.target.value);const button=panel.content?.querySelector('[data-purchase-prepare] span');if(button)button.textContent=event.target.value.trim()?'按补充偏好重新分析':`查看 ${selection().ids.length} 类款式`}},{signal:abort.signal});container.addEventListener('toggle',event=>{if(event.target.matches('[data-intent-extra]')&&currentDraft())session.setExpanded(currentDraft().id,event.target.open)},{capture:true,signal:abort.signal});container.addEventListener('compositionstart',()=>composing=true,{signal:abort.signal});container.addEventListener('compositionend',()=>{composing=false},{signal:abort.signal});container.addEventListener('focusout',()=>{if(dirty){clearTimeout(timer);timer=setTimeout(changed,160)}},{signal:abort.signal})}
 bind(root);
 // 顶栏那枚「已记下」在 #view 外面，root 的委托收不到，这里补一份同样的 click。
 const topbarSlot=document.getElementById('topbar-action');
 if(topbarSlot)topbarSlot.addEventListener('click',click,{signal:abort.signal});
 // scroll 不冒泡，但 capture 能在 root 上收到子元素的滚动 —— 首页整块会被重建，
 // 绑在 deck 元素上会随重建丢掉，绑在 capture 上不会。
 root.addEventListener('scroll',event=>{
  const deckEl=event.target;
  if(!deckEl?.matches?.('[data-aesthetic-deck]')||scrollTick)return;
  scrollTick=requestAnimationFrame(()=>{scrollTick=0;const width=deckEl.clientWidth;if(width)syncAesthetic(Math.round(deckEl.scrollLeft/width))});
 },{capture:true,passive:true,signal:abort.signal});
 window.addEventListener('popstate',()=>void syncHistory(),{signal:abort.signal});
 const unsubscribe=session.subscribe(changed),unmedia=subscribeReferenceMedia(()=>{if(!alive())return;if(document.querySelector('.purchase-layer')){refreshPurchaseSheetReferences();defer()}else changed()});
 active={root,dispose(){disposed=true;abort.abort();document.getElementById('topbar-action')?.replaceChildren();unsubscribe();unmedia();sheet?.dispose();panel.dispose();clearTimeout(timer)}};paint();
 const responses=await Promise.allSettled([api.aesthetics(),session.refresh()]);if(!alive())return;if(responses[0].status==='fulfilled')profiles=responses[0].value.aesthetics;ready=Boolean(session.data);draftId=history.state?.purchaseDraft||session.data?.analysis?.id||null;
 // §41：sheet 只剩「认识这个」一种用途了。选择帧（openSelector / onSelect / onGenerate）
 //   在 §40 收窄签名时被删掉，但这里的调用没跟着改 → 点「审美方向」报
 //   `TypeError: sheet.openSelector is not a function`，面板根本不弹。
 //   统一成一屏一张之后不再需要选择帧，**这条调用点连同上一条 bug 一起消失**。
 sheet=createAestheticSheet({root,profiles,getSettings:()=>({aesthetic:profile()?.id}),onSettings:()=>{},
  onUseProfile:row=>{
   if(!row)return;
   session.setStyle(row.id);
   // guide 是根帧，back 一次就把面板收掉。绝不能 sheet.dispose() —— 那是不可逆的，
   // 之后「认识这个」再也打不开。analyze() 撞到 blocked() 会自己 defer 重试。
   if(history.state?.aestheticSheet)history.back();
   void analyze();
  },
  onFeedback:()=>{},useLabel:'按这个审美分析衣橱',
  getBackground:()=>[document.querySelector('.topbar'),root,document.querySelector('#tabbar'),panel.panel]});
 acceptRefs();if(history.state?.purchasePage&&history.state.purchasePage!=='home')await syncHistory();else{history.replaceState({...baseState(),purchasePage:'home'},'',location.href);paint()}
}
