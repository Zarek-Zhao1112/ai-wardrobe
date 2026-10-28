// §29：按衣物ID串行准备；后台处理与当前编辑位置互不驱动。
import { api } from '../../core/api.js';
import { generate, flatlayBusy } from './flatlay.js';
import { cropFile } from './crop.js';
import { loadPhoto, newToken, storageAvailable, copyData as copy } from './store.js';

const final = item => ['saved','skipped'].includes(item.status);
const scalar = new Set(['type','name','brand','notes','primary_color','material','fit','length','formality','pattern']);
const multi = new Set(['colors','style','season','scene']);
const empty = value => value == null || value === '' || Array.isArray(value) && !value.length;
const same = (a,b) => JSON.stringify(a) === JSON.stringify(b);
const ready = item => item.flatlay?.state === 'completed' && item.flatlay.sourceValid !== false && item.flatlay.revision === item.revision && item.flatlay.kind === item.fields.type;

export function batchProgress(draft) {
  const items=draft?.items||[],saved=items.filter(i=>i.status==='saved').length;
  return {total:items.length,prepared:items.filter(i=>i.status==='saved'||!final(i)&&ready(i)).length,saved,
    remaining:items.filter(i=>!final(i)).length,paused:draft?.automation?.paused||null};
}

export class ImportQueue {
  constructor({draft,ctx,persist,capture,changed,fields,error}) {
    Object.assign(this,{draft,ctx,persist,capture,changed,fields,formatError:error});
    this.live=true;this.authorized=new Set();this.intents=new Set();this.manual=[];this.job=null;this.pumping=null;this.pauseEpoch=0;
    // 恢复草稿不恢复收费授权；已有任务只查询，未开始项由用户继续。
    draft.automation={...draft.automation,enabled:false,paused:draft.automation?.paused||null};
  }
  busy(item){return this.job?.id===item?.id || this.manual.some(j=>j.id===item?.id);}
  pending(item){return this.authorized.has(item?.id)||this.manual.some(j=>j.id===item?.id);}
  notify(item,photo){if(this.live)this.changed(item,photo);}
  async save(options){return Boolean(await this.persist(options))&&storageAvailable();}
  pause(code,text,item){
    this.pauseEpoch++;
    this.draft.automation.paused={code,text,itemId:item?.id||null};
    this.draft.automation.enabled=false;
    this.notify(item);
  }
  dispose(){this.live=false;this.authorized.clear();this.manual=[];}
  async restore(){
    for(const item of this.draft.items){
      if(final(item))continue;
      if(item.assistant?.stage==='recognizing'){
        item.assistant.stage='interrupted';
        item.error={kind:'recognition',code:'recognition_interrupted',text:'上次识别中断，照片和填写内容已保留；需要重新识别时再点击。'};
      }
      if(item.flatlay?.id&&['starting','running','waiting'].includes(item.flatlay.state))this.manual.push({id:item.id,resume:true});
    }
    await this.save();this.notify();void this.pump();
  }
  async authorize(ids){
    if(!this.live)return;
    for(const id of ids){const item=this.draft.items.find(i=>i.id===id);if(item&&!final(item)){this.authorized.add(id);this.intents.add(id);item.autoRequested=true;}}
    this.draft.automation.enabled=true;
    if(!this.draft.automation.paused&&await this.save())void this.pump();
    else if(!storageAvailable())this.pause('storage','进度尚未保存到本机，自动准备已暂停。');
    this.notify();
  }
  async resume(){
    if(!this.live)return;
    this.draft.automation.paused=null;
    for(const item of this.draft.items)if(!final(item)&&item.flatlay?.id&&['starting','running','waiting'].includes(item.flatlay.state)&&!this.busy(item))this.manual.push({id:item.id,resume:true});
    const ids=this.draft.items.filter(i=>!final(i)&&!i.pending&&!i.error&&!ready(i)&&
      !['interrupted','recognition_failed'].includes(i.assistant?.stage)&&!['waiting','failed'].includes(i.flatlay?.state)).map(i=>i.id);
    await this.authorize(ids);
  }
  request(id,options={}){
    const item=this.draft.items.find(i=>i.id===id);
    if(!this.live||!item||final(item)||item.pending||this.busy(item))return;
    if(options.automatic&&(!this.intents.has(id)||this.draft.automation.paused||!this.draft.automation.enabled))return;
    // 手动重试也进同一队列；不顺带恢复其他失败项目。
    this.manual.push({id,...options,explicit:!options.automatic,epoch:this.pauseEpoch});this.notify(item);void this.pump();
  }
  next(){
    while(this.manual.length){
      const next=this.manual.shift();
      if(!next.resume&&(next.epoch!==this.pauseEpoch||next.automatic&&(!this.intents.has(next.id)||this.draft.automation.paused||!this.draft.automation.enabled)))continue;
      return next;
    }
    if(this.draft.automation.paused||!this.draft.automation.enabled)return null;
    for(const id of this.authorized){
      const item=this.draft.items.find(i=>i.id===id);
      if(!item||final(item)||item.pending||item.error||ready(item)){this.authorized.delete(id);continue;}
      if(item.assistant?.stage==='needs_type'&&!item.fields.type){this.authorized.delete(id);continue;}
      return {id,automatic:true,epoch:this.pauseEpoch};
    }
    return null;
  }
  async pump(){
    if(this.pumping||!this.live)return this.pumping;
    this.pumping=(async()=>{
      let next;
      while(this.live&&(next=this.next())){
        const item=this.draft.items.find(i=>i.id===next.id);
        if(!item||final(item)||item.pending)continue;
        this.job={id:item.id,token:newToken()};this.notify(item);
        try{await this.run(item,next);}catch(error){
          if(this.live){item.error={kind:'image',text:'这张照片暂未处理成功，照片和填写内容已保留。'};this.pause('preparation_failed','准备过程未能确认，自动准备已暂停。',item);}
        }finally{
          this.authorized.delete(item.id);this.job=null;
          if(this.live){await this.save();this.notify(item);}
        }
      }
    })();
    try{await this.pumping;}finally{this.pumping=null;this.notify();}
  }
  merge(item,response){
    this.capture(item.id);
    const before=copy(item.fields),names=[];
    for(const [name,owned]of Object.entries(item.ai||{})){
      if(!item.edited?.[name]&&same(item.fields[name],owned.after))item.fields[name]=copy(owned.before??(Array.isArray(owned.after)?[]:null));
    }
    item.ai={};const cleared=copy(item.fields);
    for(const [name,value]of Object.entries(response.tags)){
      if(item.edited?.[name]||!empty(item.fields[name]))continue;
      if(scalar.has(name)&&typeof value==='string'&&value.trim()||multi.has(name)&&Array.isArray(value)&&value.length&&value.every(v=>typeof v==='string')){
        item.fields[name]=copy(value);item.ai[name]={before:copy(cleared[name]??null),after:copy(value)};
      }
    }
    for(const name of new Set([...Object.keys(before),...Object.keys(item.fields)]))if(!same(before[name],item.fields[name]))names.push(name);
    item.recognized=true;item.status='ready';item.error=null;item.conflicts={...(response.conflicts||{})};
    if(item.fields.type)delete item.conflicts.type;
    this.fields(item,names);
  }
  async run(item,options){
    let revision=item.revision;const batch=this.draft.id,attempt=this.job.token;
    const valid=()=>this.live&&this.draft.id===batch&&this.job?.token===attempt&&this.draft.items.includes(item)&&!final(item)&&!item.pending&&item.revision===revision;
    const canStart=()=>valid()&&options.epoch===this.pauseEpoch&&(options.explicit||this.intents.has(item.id)&&this.draft.automation.enabled&&!this.draft.automation.paused);
    const photo=await loadPhoto(item.id);if(!valid())return;
    this.capture(item.id);
    const resumed=options.resume||!options.restart&&item.flatlay?.id&&['starting','running','waiting'].includes(item.flatlay.state);
    try{
      const needRecognition=!resumed&&!options.generationOnly&&!options.restart&&(options.reidentify||!item.recognized||item.error?.kind==='recognition');
      if(needRecognition){
        if(!this.ctx.health?.vision_ready){this.pause('not_configured','请先连接电脑，再继续自动准备。',item);return;}
        item.assistant={...item.assistant,stage:'recognizing',attempt,startedAt:Date.now(),autoSelect:true};item.error=null;
        if(!await this.save()){item.assistant.stage='queued';this.pause('storage','进度尚未保存到本机，自动准备已暂停。',item);return;}
        if(!canStart())return;
        this.notify(item,photo);
        let response=await api.previewTags(photo.file,item.rect?{original:photo.original,rect:item.rect}:{});
        if(!valid())return;this.check(response);
        if(!item.recovered&&!item.rect&&response.crop&&response.crop.w*response.crop.h<=.75){
          const output=await cropFile(photo.original,response.crop);if(!valid())return;
          photo.file=output.file;photo.display=null;item.flatlay=null;item.rect=output.rect;item.revision++;revision=item.revision;
          if(!await this.save({put:[photo]})){this.pause('storage','照片进度尚未保存，自动准备已暂停。',item);return;}
          if(!canStart())return;
          this.notify(item,photo);
          response=await api.previewTags(photo.file,{original:photo.original,rect:item.rect});
          if(!valid())return;this.check(response);
        }
        this.merge(item,response);await this.save();this.notify(item,photo);
      }
      if(!valid())return;
      if(!options.restart&&!options.generationOnly&&photo.display&&item.flatlay?.state==='completed'){item.assistant={...item.assistant,stage:'ready'};return;}
      if(!resumed&&!item.fields.type){item.assistant={...item.assistant,stage:'needs_type'};return;}
      if(!this.ctx.health?.flatlay_configured){this.pause('not_configured','请先连接电脑，再继续生成平铺图。',item);return;}
      if(!resumed&&!canStart())return;
      item.assistant={...item.assistant,stage:'generation',autoSelect:true};item.flatlay||={};
      const state=item.flatlay;if(!resumed)state.kind=item.fields.type;
      let recorded=true;
      this.notify(item,photo);
      await generate({state,file:photo.file,kind:state.kind||item.fields.type,resume:Boolean(resumed),valid:()=>valid()&&item.flatlay===state,
        durable:()=>storageAvailable()&&recorded,canStart:()=>canStart()&&state.kind===item.fields.type,
        changed:async()=>{if(valid()){recorded=await this.save();this.notify(item,photo);}},
        completed:async file=>{
          if(!valid()||item.flatlay!==state)return;
          photo.display=file;state.sourceValid=true;state.revision=revision;state.selected=state.kind===item.fields.type;
          item.assistant.stage='ready';await this.save({put:[photo]});this.notify(item,photo);
        }});
      if(valid()&&['waiting','failed'].includes(state.state))this.pause(state.code||'flatlay_unknown','这件平铺图的处理结果尚未确认，自动准备已暂停。请先查看或处理这件。',item);
    }catch(error){
      if(!valid())return;
      item.status='ready';item.error=this.formatError(error);item.assistant={...item.assistant,stage:'recognition_failed'};
      // 内容/格式错误留给本件；连接、鉴权、超时和未知服务错误暂停整批。
      if(!(error.local||error.status>=400&&error.status<500&&![401,402,403,408,429].includes(error.status)))this.pause(item.error.code||'recognition_unknown','识别服务暂未返回可用结果，自动准备已暂停。照片和进度已保留。',item);
    }
  }
  check(response){if(!response||!response.tags||typeof response.tags!=='object'||Array.isArray(response.tags)){const error=new Error('这张图片的识别结果格式不正确，请单独重试。');error.local=true;throw error;}}
}
