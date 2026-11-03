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
 function renderHome(){
  const focus=root.contains(document.activeElement),data=session.data;
  const content=ready?initialHtml({data,profile:profile(),analyzing:session.analyzing,preparation:data?.preparation,photosReady:readyPreparation()}):'<p class="faint">正在读取添置建议…</p><button type="button" class="quiet blue" data-purchase-query>重新读取</button>';
  const html=`<section class="purchase-view purchase-intent" data-purchase-page="home"><div class="purchase-heading page-actions"><div class="intent-heading-right"><button type="button" class="purchase-saved-button saved-link" data-purchase-saved>${icon('bookmark')}已记下 ${savedIds().size}</button></div></div><div class="purchase-content">${session.message?`<p class="global-notice" role="status">${esc(session.message)}</p>`:''}${content}</div></section>`;
  if(homeHtml!==html){root.innerHTML=html;homeHtml=html;if(focus)root.querySelector('[data-purchase-analyze],[data-purchase-query]')?.focus({preventScroll:true})}
 }
 function paint({animate=false,position}={}){
  if(!alive())return;if(blocked()||composing||editing()||panel.dragging){defer();return}clearTimeout(timer);dirty=false;acceptRefs();
  if(mode==='home'){if(panel.open){void panel.close().then(()=>{if(alive()&&mode==='home')paint()});return}renderHome();return}
  const content=body(),head=header();
  if(!panel.open){panel.show({header:head,content,anchor:root.querySelector('[data-purchase-analyze]')});bind(panel.content)}
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
  if('openAesthetics'in d){sheet?.openSelector(b);return}if('purchaseAnalyze'in d){void analyze();return}if(d.intentDirection){toggle(d.intentDirection);return}if('purchasePrepare'in d){void choose();return}
  if('openAnalysis'in d){change('analyzing',{extra:{purchaseRequest:session.analysisRequestId}});return}
  if('purchaseHome'in d){closeAll();return}if('purchaseReturn'in d){back();return}if('purchaseSaved'in d){change('saved');return}if('openPreparation'in d){change('processing');return}
  if('resumeAnalysis'in d){draftId=session.data?.analysis?.id;change('assistant');return}if('backToAnalysis'in d){void returnToAnalysis(d.sourceDraft||session.data?.preparation?.draft_id||draftId);return}
  if('purchaseLast'in d){resultPlanId=null;const p=fullPlan();change('result',{extra:p?{purchasePlan:p.id}:{}});return}if('viewReadyResult'in d){if(readyPreparation())change('result',{extra:{purchasePlan:session.data.preparation.plan_id}});return}
  if('queryAnalysis'in d){void session.queryAnalysis().catch(e=>toast(e.message,'error'));return}if('queryPreparation'in d){void session.queryPreparation().catch(e=>toast(e.message,'error'));return}
  if('purchaseQuery'in d){void session.refresh().then(()=>{if(alive()){ready=true;paint()}}).catch(e=>toast(e.message,'error'));return}if(d.direction)void card(b);
 };
 function bind(container){container.addEventListener('click',click,{signal:abort.signal});container.addEventListener('input',event=>{if(event.target.matches('[data-intent-note]')&&currentDraft()){session.setNote(currentDraft().id,event.target.value);const button=panel.content?.querySelector('[data-purchase-prepare] span');if(button)button.textContent=event.target.value.trim()?'按补充偏好重新分析':`查看 ${selection().ids.length} 类款式`}},{signal:abort.signal});container.addEventListener('toggle',event=>{if(event.target.matches('[data-intent-extra]')&&currentDraft())session.setExpanded(currentDraft().id,event.target.open)},{capture:true,signal:abort.signal});container.addEventListener('compositionstart',()=>composing=true,{signal:abort.signal});container.addEventListener('compositionend',()=>{composing=false},{signal:abort.signal});container.addEventListener('focusout',()=>{if(dirty){clearTimeout(timer);timer=setTimeout(changed,160)}},{signal:abort.signal})}
 bind(root);window.addEventListener('popstate',()=>void syncHistory(),{signal:abort.signal});
 const unsubscribe=session.subscribe(changed),unmedia=subscribeReferenceMedia(()=>{if(!alive())return;if(document.querySelector('.purchase-layer')){refreshPurchaseSheetReferences();defer()}else changed()});
 active={root,dispose(){disposed=true;abort.abort();unsubscribe();unmedia();sheet?.dispose();panel.dispose();clearTimeout(timer)}};paint();
 const responses=await Promise.allSettled([api.aesthetics(),session.refresh()]);if(!alive())return;if(responses[0].status==='fulfilled')profiles=responses[0].value.aesthetics;ready=Boolean(session.data);draftId=history.state?.purchaseDraft||session.data?.analysis?.id||null;
 sheet=createAestheticSheet({root,profiles,getSettings:()=>({aesthetic:profile()?.id}),onSettings:()=>{},onSelect:id=>{session.setStyle(id);if(mode!=='home'){pending={next:'home',options:{replace:true},ticket,key:stateKey()}}defer()},onGenerate:()=>void analyze(),onFeedback:()=>{},useLabel:'按这个审美分析衣橱',getBackground:()=>[document.querySelector('.topbar'),root,document.querySelector('#tabbar'),panel.panel]});
 acceptRefs();if(history.state?.purchasePage&&history.state.purchasePage!=='home')await syncHistory();else{history.replaceState({...baseState(),purchasePage:'home'},'',location.href);paint()}
}
