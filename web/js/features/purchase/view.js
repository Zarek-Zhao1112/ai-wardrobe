/** §37：首页保持稳定，真实分析、选方向与结果均在B玻璃助手内。 */
import {api} from '../../core/api.js';
import {esc,icon,toast} from '../../core/ui.js';
import {createAestheticSheet} from '../outfits/aesthetic-sheet.js';
import {coverImage} from '../outfits/aesthetic-ui.js';
import {mountStyleDeck} from '../../components/style-deck.js';
import {mountPurchaseLookbook} from './lookbook.js';
import {createAssistantPanel} from './assistant-panel.js';
import {createPurchaseBuilder} from './builder.js';
import {intentSession as session} from './intent-session.js';
import {seedReferences,subscribeReferenceMedia} from './reference-store.js';
import {purchaseReference,referenceStatus} from './reference.js';
import {openPurchaseSheet,refreshPurchaseSheetReferences} from './sheet.js';
import {initialHtml,assistantHtml,analyzingHtml,preparingHtml,productCardHtml,resultsHtml,analysisNeedsQuery} from './intent-ui.js';
export const label='添置';
let active=null,lastReady=null;
export function unmount(root){if(active?.root===root){active.dispose();active=null}}
const blocked=()=>Boolean(document.querySelector('.purchase-layer,.aesthetic-layer,.bridge-modal,.import-modal'));
export async function mount(root,ctx){
 if(active)unmount(active.root);
 let disposed=false,ready=false,mode='home',draftId=null,resultPlanId=null,profiles=[],sheet=null,timer=null,ticket=0,dirty=false,composing=false,assistantId=null,depth=0,pending=null,returning=false,homeHtml='',starting=false;
 // §50：首页会重绘，保留上次选中的风格，交给新叠卡恢复。
 let aestheticIndex=0,styleDeck=null,lookbook=null,wantPending=false;
 let querying=null,queryNotice=null;
 const hash=location.hash,abort=new AbortController(),positions=new Map(),lookPositions=new Map(),alive=()=>!disposed&&root.isConnected;
 const syncViewport=()=>root.style.setProperty('--purchase-home-vh',(window.visualViewport?.height||window.innerHeight)+'px');
 syncViewport();window.addEventListener('resize',syncViewport,{signal:abort.signal});window.visualViewport?.addEventListener('resize',syncViewport,{signal:abort.signal});
 const currentDraft=()=>session.draft(draftId)||session.data?.analysis;
 const profile=()=>profiles.find(p=>p.id===session.style)||profiles.find(p=>p.id==='natural')||profiles[0];
 const analysisProfile=()=>profiles.find(p=>p.id===session.data?.analysis_request?.aesthetic?.id)||profile();
 const stateKey=()=>JSON.stringify([history.state?.purchasePage,history.state?.purchaseDraft,history.state?.purchasePlan,history.state?.purchaseRequest,history.state?.purchaseAssistant]);
 const editing=()=>Boolean(panel.content?.contains(document.activeElement)&&document.activeElement?.matches('[data-intent-note]'));
 const baseState=()=>{const value={...history.state};for(const k of ['purchasePage','purchaseDraft','purchasePreparation','purchaseRequest','purchasePlan','purchaseOrigin','purchaseDetail','aestheticSheet','bridgeSettings','purchaseAssistant'])delete value[k];return value};
 const savedIds=()=>new Set((session.data?.saved||[]).flatMap(p=>p.directions.map(d=>d.id)));
 const key=()=>mode+':'+(draftId||resultPlanId||'');
 const panel=createAssistantPanel({root,onClose:closeAll,onBack:back,isBlocked:blocked});
 const builder=createPurchaseBuilder({alive,onChange:changed,onSaved:next=>session.acceptSaved(next)});
 function fullPlan(){for(const plan of [session.data?.result,...[...(session.data?.result_history||[])].reverse(),lastReady].filter(Boolean)){if(resultPlanId&&plan.id!==resultPlanId)continue;if(plan.decision==='enough'||plan.directions?.length&&plan.directions.every(d=>purchaseReference(d))){lastReady=plan;return plan}}return null}
 function preparedPlan(){return [session.data?.result,...(session.data?.result_history||[])].find(p=>p?.id===session.data?.preparation?.plan_id)}
 function readyPreparation(){const p=preparedPlan();return Boolean(p?.directions?.length&&p.directions.every(d=>purchaseReference(d)))}
 function readyDraft(){const draft=currentDraft();return draft?.workflow_version===2?{...draft,directions:draft.directions.filter(d=>purchaseReference(d))}:null}
 function selection(){const d=currentDraft();return d?session.selection(d.id):{ids:[],note:'',expanded:false}}
 function acceptRefs(){seedReferences(session.data?.references||{})}
 function defer(){dirty=true;clearTimeout(timer);timer=setTimeout(()=>{if(alive())changed()},350)}
 function header(){
  const isBuilder=mode.startsWith('builder-');
  const p=isBuilder?profiles.find(row=>row.id===builder.style(mode))||profile()
   :mode==='result'?fullPlan()?.aesthetic:mode==='assistant'?currentDraft()?.aesthetic:mode==='analyzing'?analysisProfile():profile();
  const match=profiles.find(row=>row.id===p?.id),cover=match?coverImage(match):'';
  const showBack=isBuilder||['result','saved','processing'].includes(mode);
  return `${showBack?`<button type="button" class="pi-head-back" data-assistant-back aria-label="返回上一层">${icon('back')}</button>`:''}
   <div class="pi-head-style">${cover?`<img src="${esc(cover)}" alt="">`:icon('spark')}<span><strong>${esc(p?.name||'添置助手')}</strong>
   <small>${isBuilder?'生成添置推荐':`结合 ${session.data?.total||0} 件已有衣物`}</small></span></div>
   <button type="button" class="pi-head-close" data-assistant-close aria-label="关闭添置助手">${icon('close')}</button>`;
 }
 function queryFeedback(){
  const kind=mode==='processing'?'prepare':['assistant','analyzing'].includes(mode)?'analysis':null;
  if(!kind)return '';
  const request=kind==='prepare'?session.data?.preparation:session.data?.analysis_request;
  if(!request)return '';
  const notice=queryNotice?.id===request.id&&queryNotice.kind===kind?queryNotice:null;
  const checking=querying?.id===request.id&&querying.kind===kind;
  const partial=mode==='assistant'&&currentDraft()?.id===request.draft_id&&request.status==='completed'&&request.completed<request.total;
  const text=checking?'正在查询原任务…':notice?.text||(partial?`这次有 ${request.completed} 个方向可查看，另有 ${request.total-request.completed} 个未加入建议。`:'');
  return text?`<p class="pi-query-feedback${!checking&&notice?.error?' is-error':''}" role="status">${esc(text)}</p>`:'';
 }
 function body(){
  if(mode.startsWith('builder-'))return builder.html(mode,savedIds());
  const data=session.data,draft=currentDraft(),ready=readyDraft();
  if(mode==='analyzing')return analyzingHtml(analysisProfile(),data?.analysis_request);
  if(mode==='assistant'){
   if(draft?.workflow_version!==2)return '<div class="pi-empty"><h2>先为衣物方向生成示意。</h2><p>重新分析后，款式示意与衣物方向会一起给你。</p><button type="button" class="purchase-primary primary" data-purchase-analyze>按这个审美重新分析</button></div>';
   if(draft.decision!=='enough'&&!ready?.directions.length){const failed=draft.directions.some(d=>referenceStatus(d).status==='failed');return failed?'<div class="pi-empty"><h2>这组款式图片暂时无法读取。</h2><p>已有建议仍然保留，可以重新分析其他款式。</p><button type="button" class="purchase-primary primary" data-purchase-analyze>重新分析</button></div>':analyzingHtml(profile(),{status:'running',phase:'decoding'})}
   const waiting=data.analysis_request?.pending_reference_count?`<p class="pi-analysis-hint" role="status">还有 ${data.analysis_request.pending_reference_count} 个 AI 款式示意待确认。<button type="button" class="quiet blue" data-query-analysis>查询原任务</button></p>`:'';
   const last=data.result&&data.result.source_draft_id!==draft.id?'<button type="button" class="quiet blue home-return" data-purchase-last>回看上次完整建议</button>':'';
   return waiting+assistantHtml({draft:ready,selection:{...selection(),ids:selection().ids.filter(id=>ready.directions.some(d=>d.id===id))},items:data.items||{},preparing:session.preparing})+last;
  }
  if(mode==='processing'){const p=preparedPlan();return preparingHtml(data?.preparation,{photosReady:readyPreparation(),photosFailed:Boolean(p?.directions.some(d=>referenceStatus(d).status==='failed'))})}
  if(mode==='saved'){
   const cards=[...(data?.saved||[])].reverse().flatMap(plan=>plan.directions.map(direction=>productCardHtml(plan,direction,true)));
   return `<h2 class="screen-title">我想要的添置</h2>${cards.length?cards.join(''):'<p class="saved-empty">还没有想要的款式。</p>'}`;
  }
  return resultsHtml(fullPlan(),savedIds());
 }
/** 顶栏右侧的「已想要 N」。它原来独占首页首行（min-height 44 + margin 14），
 *  §42 把那 58px 整条删掉让给封面，按钮搬到顶栏的 #topbar-action。
 *  只在首页（mode==='home'）出现；进助手/结果帧就清空，顶栏回到「标题 + 齿轮」。
 *  ⚠️ 按钮不在 #view 里，本模块绑在 root 上的 click 委托收不到 ——
 *     要在 bind 那一段给 #topbar-action 单独挂同一个 click。 */
function syncTopbar(){
 const slot=document.getElementById('topbar-action');
 if(!slot)return;
 if(!alive()||mode!=='home'){if(slot.innerHTML)slot.innerHTML='';return}
 const html=`<button type="button" class="purchase-saved-button saved-link" data-purchase-saved>${icon('bookmark')}已想要 ${savedIds().size}</button>`;
 if(slot.innerHTML!==html)slot.innerHTML=html;
}
 function renderHome(){
  const focus=root.contains(document.activeElement),focused=document.activeElement,data=session.data;
  const resumeSelector=focused?.matches('[data-builder-action="resume"]')?'[data-builder-action="resume"]'
   :focused?.matches('.purchase-cover-resume')?'.purchase-cover-resume:not([data-builder-action])':null;
  const content=ready?initialHtml({data,profiles,analyzing:session.analyzing,preparation:data?.preparation,photosReady:readyPreparation(),builderRequest:builder.hasRequest}):'<p class="faint">正在读取添置建议…</p><button type="button" class="quiet blue" data-purchase-query>重新读取</button>';
  const html=`<section class="purchase-view purchase-intent" data-purchase-page="home"><div class="purchase-content is-aesthetic-home">${session.message?`<p class="global-notice" role="status">${esc(session.message)}</p>`:''}${content}</div></section>`;
  if(homeHtml===html)return;
  styleDeck?.dispose();
  lookbook?.dispose();
  root.innerHTML=html;
  homeHtml=html;
  syncTopbar();
  const deckEl=root.querySelector('[data-aesthetic-deck]');
  const page=root.querySelector('[data-aesthetic-page]');
  lookbook=deckEl?mountPurchaseLookbook(deckEl,{
   positions:lookPositions,
   isCovered:()=>mode!=='home'||blocked()||root.getAttribute('aria-hidden')==='true'
  }):null;
  styleDeck=deckEl?mountStyleDeck(deckEl,{
   index:aestheticIndex,
   enabled:()=>alive()&&mode==='home'&&!blocked(),
   onMoveStart:()=>lookbook?.pauseForMove(),
   onChange(index){
    aestheticIndex=index;
    page.textContent=(index+1)+' / '+profiles.length;
    lookbook?.select(index);
   }
  }):null;
  if(focus)((resumeSelector&&root.querySelector(resumeSelector))||root.querySelectorAll('[data-aesthetic-open]')[aestheticIndex]||root.querySelector('[data-purchase-query]'))?.focus({preventScroll:true});
 }
 function paint({animate=false,position}={}){
  if(!alive())return;if(blocked()||composing||editing()||panel.dragging){defer();return}clearTimeout(timer);dirty=false;acceptRefs();
  syncTopbar();
  if(mode==='home'){if(panel.open){void panel.close().then(()=>{if(alive()&&mode==='home')paint()});return}renderHome();return}
  const content=queryFeedback()+body(),head=header();
  if(!panel.open){
   const faces=root.querySelectorAll('[data-aesthetic-open]'),focused=document.activeElement;
   const resume=root.contains(focused)&&focused.matches('.purchase-cover-resume')?focused:null;
   panel.show({header:head,content,anchor:resume||faces[aestheticIndex]||faces[0]||root.querySelector('[data-purchase-query]')});bind(panel.content)
  }
  else if(!panel.render({header:head,content,animate})){defer();return}
  panel.layer.dataset.purchasePage=mode;
  if(querying){
   const button=panel.content.querySelector(querying.kind==='prepare'?'[data-query-preparation]':'[data-query-analysis]');
   if(button){button.disabled=true;(button.querySelector('span')||button).textContent='查询中…'}
  }
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
  const run=++ticket,state=history.state||{},identity=stateKey(),next=['assistant','analyzing','processing','result','saved','builder-pick','builder-format','builder-status'].includes(state.purchasePage)?state.purchasePage:'home';let wanted=state.purchaseDraft||null;
  if(wanted&&!next.startsWith('builder-')&&!session.draft(wanted)){try{await session.loadDraft(wanted)}catch{wanted=null}}
  if(!alive()||run!==ticket||identity!==stateKey()||location.hash!==hash)return;
  draftId=wanted;resultPlanId=state.purchasePlan||null;assistantId=state.purchaseAssistant?.id||null;depth=state.purchaseAssistant?.depth||0;change(next,{fromHistory:true});
  if(next==='builder-pick'||next==='builder-format')builder.ensureWardrobe();
 }
 function changed(){
  if(!alive())return;if(starting){dirty=true;return}acceptRefs();if(blocked()||composing){defer();return}
  if(pending){const p=pending;pending=null;if(p.ticket===ticket&&p.key===stateKey())change(p.next,p.options);else void syncHistory();return}
  if(history.state?.purchasePage&&history.state.purchasePage!==mode){void syncHistory();return}
  const request=session.data?.analysis_request;
  if(mode==='analyzing'&&request?.status==='completed'&&!request.pending_reference_count&&request.draft_id&&history.state?.purchaseRequest===request.id){draftId=request.draft_id;change('assistant',{replace:true});return}
  if(mode==='processing'&&history.state?.purchasePreparation===session.data?.preparation?.id&&readyPreparation()){change('result',{replace:true,extra:{purchasePlan:session.data.preparation.plan_id}});return}
  paint();
 }
 async function analyze(options={}){
  if(analysisNeedsQuery(session.data?.analysis_request)){
   // 未决的生图只能查询原任务；把拦截原因放进助手面板，不能用被面板遮住的全局 toast。
   change('analyzing',{extra:{purchaseRequest:session.analysisRequestId}});return
  }
  if(session.analyzing||!ready||!session.data?.total)return;session.clearMessage();starting=true;let promise;try{promise=session.analyze(options)}finally{starting=false}if(!session.analyzing){void promise.catch(e=>toast(e.message,'error'));return}
  change('analyzing',{extra:{purchaseRequest:session.analysisRequestId}});try{await promise}catch{}if(alive())changed();
 }
 function openAesthetic(id){
  if(!profiles.some(row=>row.id===id))return;
  session.setStyle(id);
  builder.open(id);
  change('builder-pick');
  void builder.loadWardrobe();
 }
 async function submitBuilder(){
  const current=mode,run=ticket;
  if(await builder.submit(current==='builder-status')&&alive()&&mode===current&&ticket===run&&current==='builder-format')change('builder-status');
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
 async function card(button){
  const {plan:planId,direction:directionId}=button.dataset;
  const data=session.data;
  const plans=mode==='saved'?data.saved||[]:[fullPlan()];
  const plan=plans.find(row=>row?.id===planId&&row.directions.some(direction=>direction.id===directionId));
  const direction=plan?.directions.find(row=>row.id===directionId);
  if(!direction||!purchaseReference(direction)||wantPending)return;
  await openPurchaseSheet({
   root:panel.panel,anchor:button,direction,plan,items:data.items,taxonomy:ctx.taxonomy,
   saved:savedIds().has(direction.id),
   onChoice:(action,referenceId)=>session.choice(plan.id,direction.id,action,referenceId),
   onClose:()=>{
    if(history.state?.purchasePage!==mode)void syncHistory();
    else{paint();panel.content?.querySelector(`[data-direction="${CSS.escape(direction.id)}"]`)?.focus({preventScroll:true})}
   },
   onDismiss:()=>void returnToAnalysis(plan.source_draft_id),changeWithoutDismiss:true
  });
 }
 async function toggleWant(button){
  if(wantPending)return;
  const {plan,wantDirection,referenceId}=button.dataset;
  const wanted=savedIds().has(wantDirection);
  if(!wanted&&!referenceId)return;
  const from=mode,focused=document.activeElement===button;
  const message=button.closest('.purchase-choice-copy').querySelector('.purchase-want-error');
  message.textContent='';
  wantPending=true;
  button.disabled=true;
  try{
   await session.choice(plan,wantDirection,wanted?'unsave':'save',wanted?undefined:referenceId);
   if(!alive())return;
   toast(wanted?'已取消想要':'已加入首页想要清单','ok');
   if(focused&&mode===from&&!button.isConnected){
    const next=panel.content?.querySelector(`[data-want-direction="${CSS.escape(wantDirection)}"]`);
    if(next)next.focus({preventScroll:true});else panel.focus();
   }
  }catch(error){
   if(alive()&&mode===from){
    const target=message.isConnected?message:panel.content?.querySelector(`[data-want-direction="${CSS.escape(wantDirection)}"] + .purchase-want-error`);
    if(target)target.textContent=error.message||'暂时无法更新想要状态，请重试。';
   }
  }
  finally{wantPending=false;if(button.isConnected){button.disabled=false;if(focused&&mode===from)button.focus({preventScroll:true})}}
 }
 async function queryOriginal(kind){
  if(querying)return;
  const id=kind==='prepare'?session.preparationId:session.analysisRequestId;
  if(!id)return;
  const query={kind,id};
  querying=query;
  queryNotice=null;
  paint();
  try{
   const data=await (kind==='prepare'?session.queryPreparation():session.queryAnalysis());
   const request=kind==='prepare'?data?.preparation:data?.analysis_request;
   if(request?.id!==id)throw Error('暂时没有读到这次任务的状态。');
   let text;
   if(data.query_error)text='暂时查不到原任务：'+data.query_error;
   else if(request.pending_reference_count)text=`已查询，仍有 ${request.pending_reference_count} 个款式示意待确认。稍后可再查。`;
   else if(request.status==='completed'){
    text=kind==='prepare'?'查询完成，这次款式参考已准备好。'
     :request.completed<request.total?`已确认 ${request.completed} 个方向可查看，另有 ${request.total-request.completed} 个未加入建议。`:'查询完成，这次分析可以查看了。';
   }else if(request.status==='running'||request.status==='unknown')text='已查询，原任务还没有最终结果。稍后可再查。';
   else text=request.error||'原任务已结束，暂时没有可查看的结果。';
   queryNotice={...query,text,error:Boolean(data.query_error)};
  }catch(error){
   queryNotice={...query,text:error.message||'暂时查不到原任务，请稍后再试。',error:true};
  }finally{
   querying=null;
   if(alive())changed();
  }
 }
 const click=event=>{const b=event.target.closest('button');if(!b||b.disabled)return;const d=b.dataset;
  if(d.aestheticOpen){openAesthetic(d.aestheticOpen);return}
  if(d.builderItem){builder.toggle(d.builderItem);return}
  if(d.builderFormat){builder.choose(d.builderFormat);return}
  if(d.builderAction){
   if(d.builderAction==='load')void builder.loadWardrobe();
   if(d.builderAction==='refresh')void builder.refresh();
   if(d.builderAction==='next'&&builder.picked)change('builder-format');
   if(d.builderAction==='generate')void submitBuilder();
   if(d.builderAction==='query')void builder.query();
   if(d.builderAction==='save')void builder.save();
   if(d.builderAction==='prior')builder.prior();
   if(d.builderAction==='resume'){builder.resume();change('builder-status');void builder.refresh()}
   return;
  }
  if(d.aestheticGuide){const row=profiles.find(p=>p.id===d.aestheticGuide);if(row)sheet?.openGuide(row,b);return}
  if('purchaseAnalyze'in d){void analyze();return}if(d.intentDirection){toggle(d.intentDirection);return}if('purchasePrepare'in d){void choose();return}
  if('openAnalysis'in d){change('analyzing',{extra:{purchaseRequest:session.analysisRequestId}});return}
  if('purchaseHome'in d){closeAll();return}if('purchaseReturn'in d){back();return}if('purchaseSaved'in d){change('saved');return}if('openPreparation'in d){change('processing',{extra:{purchasePreparation:session.preparationId}});return}
  if('resumeAnalysis'in d){draftId=session.data?.analysis?.id;change('assistant');return}if('backToAnalysis'in d){void returnToAnalysis(d.sourceDraft||session.data?.preparation?.draft_id||draftId);return}
  if('purchaseLast'in d){resultPlanId=null;const p=fullPlan();change('result',{extra:p?{purchasePlan:p.id}:{}});return}if('viewReadyResult'in d){if(readyPreparation())change('result',{extra:{purchasePlan:session.data.preparation.plan_id}});return}
  if('queryAnalysis'in d){void queryOriginal('analysis');return}if('queryPreparation'in d){void queryOriginal('prepare');return}
  if('purchaseQuery'in d){void session.refresh().then(()=>{if(alive()){ready=true;paint()}}).catch(e=>toast(e.message,'error'));return}
  if(d.wantDirection){void toggleWant(b);return}
  if(d.direction)void card(b);
 };
 function bind(container){container.addEventListener('click',click,{signal:abort.signal});container.addEventListener('input',event=>{if(event.target.matches('[data-intent-note]')&&currentDraft()){session.setNote(currentDraft().id,event.target.value);const button=panel.content?.querySelector('[data-purchase-prepare] span');if(button)button.textContent=event.target.value.trim()?'按补充偏好重新分析':`查看 ${selection().ids.length} 类款式`}},{signal:abort.signal});container.addEventListener('toggle',event=>{if(event.target.matches('[data-intent-extra]')&&currentDraft())session.setExpanded(currentDraft().id,event.target.open)},{capture:true,signal:abort.signal});container.addEventListener('compositionstart',()=>composing=true,{signal:abort.signal});container.addEventListener('compositionend',()=>{composing=false},{signal:abort.signal});container.addEventListener('focusout',()=>{if(dirty){clearTimeout(timer);timer=setTimeout(changed,160)}},{signal:abort.signal})}
 bind(root);
 // 顶栏那枚「已想要」在 #view 外面，root 的委托收不到，这里补一份同样的 click。
 const topbarSlot=document.getElementById('topbar-action');
 if(topbarSlot)topbarSlot.addEventListener('click',click,{signal:abort.signal});
 window.addEventListener('popstate',()=>void syncHistory(),{signal:abort.signal});
 const unsubscribe=session.subscribe(changed),unmedia=subscribeReferenceMedia(()=>{if(!alive())return;if(document.querySelector('.purchase-layer')){refreshPurchaseSheetReferences();defer()}else changed()});
 active={root,dispose(){disposed=true;abort.abort();styleDeck?.dispose();lookbook?.dispose();root.style.removeProperty('--purchase-home-vh');document.getElementById('topbar-action')?.replaceChildren();unsubscribe();unmedia();sheet?.dispose();panel.dispose();builder.dispose();clearTimeout(timer)}};paint();
 const responses=await Promise.allSettled([api.aesthetics(),session.refresh()]);if(!alive())return;if(responses[0].status==='fulfilled')profiles=responses[0].value.aesthetics;ready=Boolean(session.data);draftId=history.state?.purchaseDraft||session.data?.analysis?.id||null;
 aestheticIndex=Math.max(0,profiles.findIndex(row=>row.id===session.style));
 // §41：sheet 只剩「认识这个」一种用途了。选择帧（openSelector / onSelect / onGenerate）
 //   在 §40 收窄签名时被删掉，但这里的调用没跟着改 → 点「审美方向」报
 //   `TypeError: sheet.openSelector is not a function`，面板根本不弹。
 //   统一成一屏一张之后不再需要选择帧，**这条调用点连同上一条 bug 一起消失**。
 sheet=createAestheticSheet({root,profiles,
  getBackground:()=>[document.querySelector('.topbar'),root,document.querySelector('#tabbar'),panel.panel]});
 acceptRefs();if(history.state?.purchasePage&&history.state.purchasePage!=='home')await syncHistory();else{history.replaceState({...baseState(),purchasePage:'home'},'',location.href);paint()}
 void builder.refresh();
}
