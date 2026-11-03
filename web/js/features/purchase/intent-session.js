/** §36：分析、准备分别跟踪；未知提交持久保存UUID，只查询原请求。 */
import { api } from '../../core/api.js';
const listeners=new Set(),drafts=new Map(),selections=new Map();
const storageKey='purchaseIntentDrafts:v1',styleKey='purchaseIntentStyle:v1',pendingKey='purchaseIntentPending:v1';
let revision=0,loading=null,timer=null,analysisPromise=null,preparePromise=null,choicePromise=null;
let data=null,message='',error=false,style='natural',pending={analysis:null,prepare:null};
const uuid=()=>crypto.randomUUID?.()||'10000000-1000-4000-8000-100000000000'.replace(/[018]/g,c=>(Number(c)^crypto.getRandomValues(new Uint8Array(1))[0]&15>>Number(c)/4).toString(16));
try{
 style=localStorage.getItem(styleKey)||style;
 const raw=JSON.parse(localStorage.getItem(storageKey)||'{}');
 if(raw&&typeof raw==='object')for(const[id,row]of Object.entries(raw).slice(-10))if(row&&Array.isArray(row.ids)&&row.ids.length<=3&&row.ids.every(v=>typeof v==='string'))selections.set(id,{ids:row.ids,note:typeof row.note==='string'?row.note.slice(0,160):'',expanded:Boolean(row.expanded)});
 const saved=JSON.parse(localStorage.getItem(pendingKey)||'{}');
 for(const kind of ['analysis','prepare']){const row=saved[kind];if(row&&typeof row.id==='string'&&/^[a-f0-9-]{36}$/.test(row.id)&&Number.isFinite(row.createdAt))pending[kind]=row;}
}catch{}
function persist(){try{localStorage.setItem(storageKey,JSON.stringify(Object.fromEntries([...selections].slice(-10))))}catch{}}
function persistPending(){try{if(pending.analysis||pending.prepare)localStorage.setItem(pendingKey,JSON.stringify(pending));else localStorage.removeItem(pendingKey);return true}catch{return false}}
function requestOf(kind){const current=kind==='analysis'?data?.analysis_request:data?.preparation,row=pending[kind];return row&&current?.id!==row.id?{...row,status:(kind==='analysis'?analysisPromise:preparePromise)?'running':'unknown',phase:kind==='analysis'?'analyzing':'starting'}:current;}
const busyStatus=value=>value?.status==='running'||value?.status==='unknown';
function schedule(){clearTimeout(timer);if(listeners.size&&(pending.analysis||pending.prepare||busyStatus(data?.analysis_request)||busyStatus(data?.preparation)||data?.preparation?.status==='completed'&&![data.result,...(data.result_history||[])].some(p=>p?.id===data.preparation.plan_id)))timer=setTimeout(()=>void refresh().catch(()=>{}),document.hidden?12000:2200);}
function notify(){listeners.forEach(fn=>fn());schedule();}
function mergeRecord(old,next){if(!next)return old;if(old?.id===next.id&&['completed','failed','interrupted'].includes(old.status)&&['running','unknown'].includes(next.status))return old;return next;}
function accept(next,lane='read'){
 const rows={...data?.items,...next.items};
 if(lane==='analysis')data={...data,analysis:next.analysis,analysis_request:mergeRecord(data?.analysis_request,next.analysis_request),references:{...data?.references,...next.references},items:rows};
 else if(lane==='prepare'){const prep=mergeRecord(data?.preparation,next.preparation);data={...data,preparation:prep,items:rows};if(next.preparation?.status==='completed'&&next.result?.request_id===next.preparation.id){data.result=next.result;data.references={...data.references,...next.references};data.result_history=next.result_history;}}
 else if(lane==='choice')data={...data,saved:next.saved,dismissed:next.dismissed,items:rows};
 else data={...data,...next,analysis_request:mergeRecord(data?.analysis_request,next.analysis_request),preparation:mergeRecord(data?.preparation,next.preparation),items:rows};
 if(next.analysis)drafts.set(next.analysis.id,next.analysis);
}
async function refresh(){
 if(loading)return loading;
 const stamp=revision;
 loading=(async()=>{
  try{
   let next=await api.purchaseIntentState();const clear=[];let terminalChanged=false;
   for(const kind of ['analysis','prepare']){
    const saved=pending[kind];if(!saved)continue;
    try{
     const record=await api.purchaseIntentRequest(saved.id);
     if(!['running','unknown'].includes(record.status)){clear.push([kind,saved.id]);terminalChanged=true;}
     if(kind==='analysis')next.analysis_request=record;else next.preparation=record;
    }catch(err){
     if(err.status===404&&Date.now()-saved.createdAt>60000){clear.push([kind,saved.id]);message='上次请求未登记，请明确重新开始。';error=true;}
    }
   }
   if(terminalChanged)next=await api.purchaseIntentState();
   if(stamp===revision){for(const[kind,id]of clear)if(pending[kind]?.id===id)pending[kind]=null;if(clear.length)persistPending();accept(next);notify();}
   return data;
  }catch(err){if(stamp===revision){message='暂时读不到添置进度：'+err.message;error=true;notify()}throw err}
  finally{loading=null;schedule()}
 })();return loading;
}
function begin(kind,record){pending[kind]=record;if(!persistPending()){pending[kind]=null;throw Error('请求标识暂未能保存到本机，请检查本机存储后再试。')}++revision;message='';error=false;}
function completed(kind,id,next){++revision;if(pending[kind]?.id===id){pending[kind]=null;persistPending()}accept(next,kind);}
async function recover(){try{await refresh()}catch{}}
export const intentSession={
 get data(){return data?{...data,analysis_request:requestOf('analysis'),preparation:requestOf('prepare')}:null},
 get style(){return style},get message(){return message},get error(){return error},
 get analysisRequestId(){return pending.analysis?.id||data?.analysis_request?.id},
 get preparationId(){return pending.prepare?.id||data?.preparation?.id},
 get analyzing(){return Boolean(analysisPromise||pending.analysis)||busyStatus(data?.analysis_request)},
 get preparing(){const r=requestOf('prepare');return Boolean(preparePromise||pending.prepare)||busyStatus(r)||r?.status==='interrupted'&&r.phase!=='finished'},
 get choosing(){return Boolean(choicePromise)},
 subscribe(fn){listeners.add(fn);schedule();return()=>{listeners.delete(fn);if(!listeners.size)clearTimeout(timer)}},refresh,
 setStyle(id){style=id;try{localStorage.setItem(styleKey,id)}catch{}notify()},
 draft(id){return drafts.get(id)||null},
 async loadDraft(id){if(!drafts.has(id)){const value=await api.purchaseAnalysis(id);drafts.set(id,value);data={...data,items:{...data?.items,...value.items},references:{...data?.references,...value.references}}}return drafts.get(id)},
 selection(id){if(!selections.has(id))selections.set(id,{ids:[],note:'',expanded:false});return selections.get(id)},
 toggle(id,direction){const row=this.selection(id);row.ids=row.ids.includes(direction)?row.ids.filter(v=>v!==direction):[...row.ids,direction];persist();return row},
 setNote(id,value){this.selection(id).note=value.slice(0,160);persist()},
 setExpanded(id,value){this.selection(id).expanded=value;persist()},
 clearMessage(){message='';error=false},
 async analyze(options={}){
  if(this.analyzing)return null;const request_id=uuid(),aesthetic=style;
  begin('analysis',{id:request_id,createdAt:Date.now(),kind:'analyze',aesthetic:{id:options.aesthetic||aesthetic}});
  analysisPromise=api.purchaseAnalyze({request_id,aesthetic,ready_references:true,...options});notify();
  try{const next=await analysisPromise;completed('analysis',request_id,next);return next}
  catch(err){if([400,401,403,404,409,422].includes(err.status)){pending.analysis=null;persistPending()}message='暂未确认这次分析：'+err.message+'。会先查询原请求。';error=true;await recover();throw err}
  finally{analysisPromise=null;notify()}
 },
 async queryAnalysis(){const id=this.analysisRequestId;if(!id)return refresh();const stamp=revision;const next=await api.purchaseAnalysisQuery(id);if(stamp===revision){++revision;if(pending.analysis?.id===id&&!['running','unknown'].includes(next.analysis_request?.status)){pending.analysis=null;persistPending()}accept(next);notify()}return data},
 async prepare(id){
  if(this.preparing)return null;const row=this.selection(id),draft=drafts.get(id);
  if(!draft||!row.ids.length)return null;const allowed=new Set(draft.directions.map(d=>d.id));
  if(row.ids.some(v=>!allowed.has(v)))throw Error('所选方向已更新，请重新勾选。');
  const request_id=uuid();begin('prepare',{id:request_id,createdAt:Date.now(),kind:'prepare',draft_id:id,total:row.ids.length,completed:0,titles:draft.directions.filter(d=>row.ids.includes(d.id)).map(d=>d.title)});
  preparePromise=api.purchasePrepare({request_id,draft_id:id,direction_ids:[...row.ids],preference:row.note});notify();
  try{const next=await preparePromise;completed('prepare',request_id,next);return next}
  catch(err){if([400,401,403,404,409,422].includes(err.status)){pending.prepare=null;persistPending()}message='暂未确认这次准备：'+err.message+'。请先查询原任务。';error=true;await recover();throw err}
  finally{preparePromise=null;notify()}
 },
 async queryPreparation(){const id=this.preparationId;if(!id)return refresh();const stamp=revision;try{const next=await api.purchasePreparationQuery(id);if(stamp===revision){++revision;if(pending.prepare?.id===id&&next.preparation?.id===id){pending.prepare=null;persistPending()}accept(next);notify()}return data}catch(err){await refresh();throw err}},
 async choice(plan_id,direction_id,action){if(choicePromise)throw Error('正在保存，请稍候。');++revision;choicePromise=api.purchaseChoice({plan_id,direction_id,action});try{const next=await choicePromise;++revision;accept(next,'choice');notify();return next}finally{choicePromise=null}},
};
