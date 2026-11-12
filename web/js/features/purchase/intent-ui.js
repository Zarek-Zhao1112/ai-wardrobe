/** §36：添置四阶段的只读呈现；正式商品卡由调用方确认图片就绪。 */
import { api } from '../../core/api.js';
import { esc,icon } from '../../core/ui.js';
import { purchaseReference, referencePhoto, referenceLabel } from './reference.js';
import { outfitCoverCardHtml } from '../outfits/aesthetic-ui.js';
import { purchaseLookPresentation } from './lookbook.js';
export function photo(item,{lazy=false}={}){const url=item&&api.presentationUrl(item);return url?`<img src="${esc(url)}" data-flatlay-source="${esc(url)}" alt="${esc(item.name||item.type_label||'已有衣物')}" ${lazy?'loading="lazy" decoding="async"':''}>`:`<span class="purchase-ph">${esc(item?.type_label||'已有衣物')}</span>`;}
const primary=(action,text,disabled=false)=>`<button type="button" class="purchase-primary primary" ${action} ${disabled?'disabled':''}>${icon('spark')}<span>${text}</span></button>`;
export function analysisNeedsQuery(request){
 return Boolean(request?.pending_reference_count||request?.status==='unknown'||request?.status==='interrupted'&&request.phase!=='finished');
}
export function latestAnalysisToResume(data){
 const request=data?.analysis_request,draft=data?.analysis;
 return draft&&request?.status==='completed'&&!request.pending_reference_count&&draft.id===request.draft_id&&
  draft.id!==data.result?.source_draft_id?draft:null;
}
/* §46：首页与搭配共用平铺整图卡，主动作按风格分析已有衣橱。 */
export function initialHtml({data,profiles=[],analyzing,preparation,photosReady=false,builderRequest=false}){
 const pending=preparation&&['running','unknown','interrupted'].includes(preparation.status)&&preparation.phase!=='finished';
 const resume=analyzing;
 const needsQuery=analysisNeedsQuery(data?.analysis_request);
 const next=pending?'preparation':data?.result?'last':'';
 // 风格页码旁的回看入口优先显示待确认任务，其次是分析、准备、上次建议。
 const back=needsQuery?`<button type="button" class="purchase-cover-resume" data-open-analysis aria-label="确认未完成的 AI 款式示意">${icon('history')}</button>`
  :resume?`<button type="button" class="purchase-cover-resume" data-open-analysis aria-label="看分析进度">${icon('history')}</button>`
  :next==='preparation'?`<button type="button" class="purchase-cover-resume" data-open-preparation aria-label="看本次准备">${icon('history')}</button>`
  :latestAnalysisToResume(data)?`<button type="button" class="purchase-cover-resume" data-resume-analysis aria-label="查看最新添置分析">${icon('history')}</button>`
  :next==='last'?`<button type="button" class="purchase-cover-resume" data-purchase-last aria-label="看上次建议">${icon('history')}</button>`:'';
 const empty=!data?.total?'<p class="selection-note aesthetic-home-empty">先录入几件已有衣物，建议才更有依据。<br><a href="#add">去录入衣物</a></p>':'';
 if(!profiles.length)return `<div class="aesthetic-home">${data?.total?'<p class="faint aesthetic-home-empty">审美方向读取中…</p>':empty}</div>`;
 const cards=profiles.map((row,index)=>outfitCoverCardHtml(row,profiles,index,{
  openLabel:'按这个风格生成添置推荐',showSummary:false,showLabel:false,guideAtTop:true,presentation:purchaseLookPresentation(row.id)
 })).join('');
 return `<div class="aesthetic-home">
   <div class="outfit-season-line${back||builderRequest?' has-purchase-resume':''}"><span>结合 ${data?.total||0} 件已有衣物</span><span class="purchase-style-meta"><span class="outfit-season-count" data-aesthetic-page></span>${builderRequest?'<button type="button" class="purchase-cover-resume" data-builder-action="resume" aria-label="查看本轮添置推荐">'+icon('layers')+'</button>':''}${back}</span></div>
   <div class="outfit-aesthetic-deck" data-aesthetic-deck role="region" aria-roledescription="轮播" aria-label="审美方向，左右滑动或用方向键切换" tabindex="0">${cards}</div>${empty}
  </div>`;
}
function proposal(draft,direction,index,selection,items){return `<button type="button" class="proposal" role="checkbox" aria-checked="${selection.ids.includes(direction.id)}" data-intent-direction="${esc(direction.id)}"><span class="pi-direction-photo">${referencePhoto(direction)}</span><span class="proposal-body"><span class="proposal-title"><strong>${esc(direction.title)}</strong></span><span class="proposal-description">${esc(direction.reason)}</span><span class="proposal-match">${direction.owned.slice(0,2).map(id=>`<span class="intent-owned-mini">${photo(items[id])}</span>`).join('')}<span>和已有衣物一起搭</span></span></span><span class="choice-mark">${icon('check')}</span></button>`;}
export function assistantHtml({draft,selection,items,preparing}){
 if(!draft)return '<p class="faint">正在读取这次分析…</p>';
 if(draft.decision==='enough')return `<p class="section-eyebrow">${icon('spark')}AI 的添置建议</p><h2 class="screen-title">${esc(draft.headline)}</h2><p class="assistant-copy">${esc(draft.summary)}</p><a class="purchase-primary primary" href="#outfits">看看已有衣服怎么搭</a><button type="button" class="quiet blue home-return" data-purchase-analyze>重新分析</button>`;
 const dirs=draft.directions,other=dirs.slice(2),open=selection.expanded||other.some(d=>selection.ids.includes(d.id));
 const action=selection.note.trim()?'按补充偏好重新分析':selection.ids.length?`查看 ${selection.ids.length} 类款式`:'先选感兴趣的衣物方向';
 return `<p class="section-eyebrow">${icon('spark')}AI 的添置建议</p><h2 class="screen-title">${esc(draft.headline)}</h2><p class="assistant-copy">先选想查看的方向，在款式参考里可以点「我想要」。</p><div class="proposal-list" role="group" aria-label="建议的衣物方向">${dirs.slice(0,2).map((d,i)=>proposal(draft,d,i,selection,items)).join('')}</div><details class="assistant-extra" data-fold="1" data-intent-extra ${open?'open':''}><summary>${other.length?'其他方向与补充偏好':'补充一点偏好'}${icon('chevron')}</summary><div class="extra-content">${other.length?`<div class="proposal-list" role="group" aria-label="其他衣物方向">${other.map((d,i)=>proposal(draft,d,i+2,selection,items)).join('')}</div>`:''}<label class="intent-note-label" for="purchase-intent-note">再补充一点偏好（选填）</label><textarea id="purchase-intent-note" data-intent-note maxlength="160" placeholder="例如：裤腿想再宽松一些。">${esc(selection.note)}</textarea><p class="selection-note">补充后会重新分析款式，再供你选择。</p></div></details>${primary('data-purchase-prepare',action,!selection.ids.length||preparing)}<p class="selection-note" data-intent-selection-note>${selection.ids.length?`已选 ${selection.ids.length} 类 · 也可以先看看，不急着买`:'感兴趣的再选，也可以暂时不添置'}</p>`;
}
export function analyzingHtml(profile,request={}){
 const phase=request?.phase,interrupted=request?.status==='interrupted';
 const uncertain=request?.status==='unknown'||interrupted&&phase!=='finished';
 const failed=request?.status==='failed'||interrupted&&phase==='finished';
 if(request?.pending_reference_count)return `<div class="pi-empty"><p class="section-eyebrow">${icon('spark')}上次分析仍有待确认项</p><h2>先确认已有的款式示意。</h2><p>还有 ${Number(request.pending_reference_count)} 个 AI 款式示意未确认。查询只检查原任务，不会重新提交生成；确认完成后就能开始新的分析。</p><button type="button" class="purchase-primary primary" data-query-analysis>${icon('spark')}<span>查询原任务</span></button><button type="button" class="quiet blue home-return" data-purchase-home>稍后再看</button></div>`;
 if(failed||uncertain)return `<div class="pi-empty"><p class="section-eyebrow">${icon('spark')}这次分析</p><h2>${uncertain?'先确认这次分析。':'这次暂时没有新的建议。'}</h2><p>${esc(request.error||'已有建议仍然保留，可以先回看。')}</p>${uncertain?primary('data-query-analysis','查询原任务'):primary('data-purchase-analyze','重新分析')}<button type="button" class="quiet blue home-return" data-purchase-home>收起，稍后再看</button></div>`;
 if(request?.status==='completed'&&request.draft_id)return `<div class="pi-empty"><h2>这次分析可以查看了。</h2>${primary('data-resume-analysis','查看这次分析')}<button type="button" class="quiet blue home-return" data-purchase-home>返回添置页</button></div>`;
 const step=phase==='decoding'?2:phase==='searching'?1:0,label=phase==='decoding'?'载入款式示意图':phase==='searching'?'生成适合这个方向的款式示意':phase==='refining'?'按补充偏好调整衣物方向':'整理已有衣物与配色';
 return `<p class="section-eyebrow">${icon('spark')}AI 正在看你的衣橱</p><h2 class="screen-title">把你的审美，<br>和已有衣服放在一起。</h2><p class="pi-analysis-status" role="status"><span class="pi-analysis-orb"></span>${label}</p>${[1,2].map(()=>'<div class="loading-skeleton" aria-hidden="true"></div>').join('')}<div class="pi-analysis-steps">${['整理衣橱','生成示意','整理建议'].map((label,i)=>`<span class="${i===step?'current':''}">${label}</span>`).join('')}</div><p class="pi-analysis-hint">先把款式示意与添置建议放在一起。<br>离开后可以稍后回来查看。</p><button type="button" class="quiet blue home-return" data-purchase-home>收起，稍后查看</button>`;
}
export function preparingHtml(prep,{photosReady=true,photosFailed=false}={}){
 if(!prep)return '<p class="faint">正在确认准备进度…</p>';
 if(prep.workflow_version===2&&prep.status==='failed')return `<div class="pi-empty"><h2>这组款式需要重新分析。</h2><p>${esc(prep.error||'图片暂时无法读取，已有建议仍然保留。')}</p>${primary('data-purchase-analyze','重新分析款式')}<button type="button" class="quiet blue home-return" data-purchase-home>返回添置初始页</button></div>`;
 const decoding=prep.status==='completed'&&!photosReady&&!photosFailed,ready=prep.status==='completed'&&photosReady,running=prep.status==='running'||decoding,uncertain=['unknown','interrupted'].includes(prep.status)&&prep.phase!=='finished';
 return `<div class="preparing"><div class="prep-orb">${icon(ready?'check':'spark')}</div><h1 class="screen-title">${ready?'款式参考准备好了。':photosFailed?'这批图片暂时无法完整显示。':decoding?'正在载入款式参考。':running?'为选中的方向，<br>准备款式参考。':uncertain?'先确认这次准备。':'这次暂未配齐参考。'}</h1><p>${ready?'衣物方向与参考图片已放在一起，<br>可以慢慢看看，再选想要的款式。':photosFailed?'上次完整建议仍然保留，<br>可以先回看，或重新选择方向。':running?'整理款式、核对图片，<br>把完整的选购建议一起给你。':esc(prep.error||'上次完整建议已保留，可以调整方向后再试。')}</p><div class="prep-selected">${(prep.titles||[]).map(title=>`<span class="chip">${esc(title)}</span>`).join('')}</div>${running?`<ol class="prep-steps"><li class="done"><span class="step-dot">${icon('check')}</span>分析衣橱与审美</li><li class="current"><span class="step-dot"></span>${prep.phase==='refining'?'整理补充偏好':prep.image_origin==='generated'?'生成 AI 款式示意':'整理实物参考'}</li><li><span class="step-dot"></span>整理完整的选购建议</li></ol>`:''}${ready?primary('data-view-ready-result','查看款式参考'):photosFailed?primary('data-purchase-last','回看上次完整建议'):uncertain?primary('data-query-preparation','查询原任务'):!running?primary('data-back-to-analysis','返回选择衣物方向'):''}<button type="button" class="quiet blue home-return" data-purchase-home>${running?'收起，稍后查看':'返回添置初始页'}</button></div>`;
}
export function productCardHtml(plan, direction, wanted) {
 const { id, title, reason } = direction;
 const reference = purchaseReference(direction);
 const label = wanted ? '已想要' : '我想要';
 return `<article class="purchase-choice-card">
  <button type="button" class="purchase-choice-photo" data-plan="${esc(plan.id)}" data-direction="${esc(id)}" aria-label="查看${esc(title)}选购建议" aria-haspopup="dialog" ${reference ? '' : 'disabled'}>${referencePhoto(direction)}<span class="media-label">${referenceLabel(direction)}</span></button>
  <div class="purchase-choice-copy">
    <h3>${esc(title)}</h3><p>${esc(reason)}</p>
    <button type="button" class="primary purchase-want" data-want-direction="${esc(id)}" data-plan="${esc(plan.id)}" data-reference-id="${esc(reference?.id || '')}" aria-pressed="${wanted}" aria-label="${wanted ? '取消想要' : label}：${esc(title)}" ${reference || wanted ? '' : 'disabled'}>${icon(wanted ? 'check' : 'bookmark')}<span>${label}</span></button>
    <p class="purchase-want-error" role="status"></p>
  </div>
 </article>`;
}
export function resultsHtml(plan, wanted) {
 if (plan?.decision === 'enough') return `<h1 class="screen-title">${esc(plan.headline)}</h1><p class="assistant-copy">${esc(plan.summary)}</p><a class="purchase-primary primary" href="#outfits">看看已有衣服怎么搭</a>`;
 if (!plan) return '<div class="saved-empty">暂时没有可展示的完整款式参考。<br>已有记录仍然保留，可以先分析想添置的方向。</div><button type="button" class="purchase-primary primary" data-purchase-home>回到添置初始页</button>';
 return `<p class="section-eyebrow">${icon('spark')}${esc(plan.aesthetic.name)}</p>
  <h1 class="screen-title">${plan.image_origin === 'generated' ? 'AI 款式示意' : '款式参考'}</h1>
  <p class="result-description">结合已有衣橱的添置建议</p>
  ${plan.directions.map(direction => productCardHtml(plan, direction, wanted.has(direction.id))).join('')}
  <button type="button" class="quiet blue home-return" data-back-to-analysis data-source-draft="${esc(plan.source_draft_id||'')}">重新选择衣物方向 →</button>`;
}
