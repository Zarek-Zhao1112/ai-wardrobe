/** §37：玻璃助手的开合、拖拽与焦点隔离；后台请求由session独立管理。 */
const FOCUSABLE='a[href],button,input,select,textarea,summary,[tabindex]';
export function createAssistantPanel({root,onClose,onBack,isBlocked=()=>false}){
 let entry=null,sequence=0;
 const reduced=matchMedia('(prefers-reduced-motion: reduce)');
 const alive=e=>entry===e&&e.layer.isConnected;
 function restore(e){for(const row of e.background){const n=row.node;n.style.filter=row.filter;n.style.transform=row.transform;n.style.transition=row.transition;n.style.pointerEvents=row.pointer;row.aria===null?n.removeAttribute('aria-hidden'):n.setAttribute('aria-hidden',row.aria)}for(const[n,value]of e.focus)if(n.isConnected)value===null?n.removeAttribute('tabindex'):n.setAttribute('tabindex',value);document.body.classList.remove('purchase-assistant-open')}
 function finish(e,focus=true){if(entry!==e)return;e.resetDrag();clearTimeout(e.timer);e.abort.abort();restore(e);e.layer.remove();entry=null;if(focus&&root.isConnected&&!isBlocked())(e.anchor?.isConnected?e.anchor:root.querySelector('[data-aesthetic-open],[data-purchase-query]'))?.focus({preventScroll:true});e.done?.()}
 function resize(e){const v=window.visualViewport;e.layer.style.setProperty('--pi-height',(v?.height||innerHeight)+'px');e.layer.style.setProperty('--pi-offset',(v?.offsetTop||0)+'px')}
 function open({header,content,anchor}){
  if(entry)return entry;
  const layer=document.createElement('div');layer.className='pi-assistant-layer';
  layer.innerHTML='<div class="pi-assistant-veil" data-assistant-close></div><section class="pi-assistant-panel purchase-intent" role="dialog" aria-modal="true" aria-label="AI添置助手" tabindex="-1"><div class="pi-assistant-grip" aria-hidden="true"></div><header class="pi-assistant-head"></header><div class="pi-assistant-scroll"></div></section>';
  const e={id:++sequence,layer,anchor,abort:new AbortController(),phase:'opening',pointer:null,delta:0,focus:[],background:[],headerHtml:header,html:content};entry=e;
  e.panel=layer.querySelector('.pi-assistant-panel');e.head=layer.querySelector('.pi-assistant-head');e.scroll=layer.querySelector('.pi-assistant-scroll');e.veil=layer.querySelector('.pi-assistant-veil');e.head.innerHTML=header;e.scroll.innerHTML=content;document.body.append(layer);
  e.panel.focus({preventScroll:true});
  e.background=[document.querySelector('.topbar'),root,document.querySelector('#tabbar')].filter(Boolean).map(node=>({node,aria:node.getAttribute('aria-hidden'),filter:node.style.filter,transform:node.style.transform,transition:node.style.transition,pointer:node.style.pointerEvents}));
  for(const row of e.background){row.node.setAttribute('aria-hidden','true');row.node.style.pointerEvents='none';row.node.style.transition=reduced.matches?'none':'filter 560ms ease, transform 560ms cubic-bezier(.2,.8,.2,1)';e.focus.push(...[...row.node.querySelectorAll(FOCUSABLE)].map(n=>[n,n.getAttribute('tabindex')]));}
  e.focus.forEach(([n])=>n.tabIndex=-1);document.body.classList.add('purchase-assistant-open');
  const signal=e.abort.signal,listen=(target,name,fn,options={})=>target?.addEventListener(name,fn,{...options,signal});
  const requestClose=()=>{if(alive(e)&&e.phase!=='closing'&&!isBlocked())onClose()};
  listen(layer,'click',event=>{if(event.target.closest('[data-assistant-close]'))requestClose();if(event.target.closest('[data-assistant-back]')&&!isBlocked())onBack()});
  listen(document,'keydown',event=>{if(!alive(e)||e.phase==='closing'||isBlocked())return;if(event.key==='Escape'&&!event.isComposing){event.preventDefault();event.stopPropagation();requestClose()}if(event.key==='Tab'){const nodes=[...e.panel.querySelectorAll(FOCUSABLE)].filter(n=>!n.disabled&&n.tabIndex>=0&&n.getClientRects().length);const first=nodes[0],last=nodes.at(-1);if(!nodes.length){event.preventDefault();e.panel.focus()}else if(!e.panel.contains(document.activeElement)||event.shiftKey&&(document.activeElement===first||document.activeElement===e.panel)){event.preventDefault();(event.shiftKey?last:first).focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}}});
  listen(document,'focusin',event=>{if(alive(e)&&e.phase!=='closing'&&!isBlocked()&&!e.panel.contains(event.target))e.panel.focus({preventScroll:true})});
  listen(window,'resize',()=>resize(e));listen(window.visualViewport,'resize',()=>resize(e));resize(e);
  const grip=layer.querySelector('.pi-assistant-grip');
  e.resetDrag=()=>{const pointer=e.pointer;e.pointer=null;e.delta=0;e.panel.style.transition='';e.panel.style.transform='';e.veil.style.opacity='';if(pointer!==null&&grip.hasPointerCapture(pointer))grip.releasePointerCapture(pointer)};
  listen(grip,'pointerdown',event=>{if(e.pointer!==null||e.phase==='closing'||isBlocked()||event.button!==0)return;e.pointer=event.pointerId;e.origin=event.clientY;e.delta=0;grip.setPointerCapture(e.pointer);e.panel.style.transition='none'});
  listen(grip,'pointermove',event=>{if(event.pointerId!==e.pointer)return;e.delta=Math.max(0,event.clientY-e.origin);e.panel.style.transform=`translateY(${e.delta}px)`;e.veil.style.opacity=String(Math.max(0,1-e.delta/500))});
  listen(grip,'pointerup',event=>{if(event.pointerId!==e.pointer)return;const dismiss=e.delta>85;e.resetDrag();if(dismiss)requestClose()});
  for(const name of ['pointercancel','lostpointercapture'])listen(grip,name,event=>{if(event.pointerId===e.pointer)e.resetDrag()});
  layer.getBoundingClientRect();requestAnimationFrame(()=>{if(!alive(e)||e.phase==='closing')return;layer.classList.add('is-open');for(const row of e.background){row.node.style.filter='blur(10px)';if(row.node===root)row.node.style.transform='scale(.98)'}e.phase='open';e.head.querySelector('[data-assistant-close]')?.focus({preventScroll:true})});
  return e;
 }
 return{
  get open(){return Boolean(entry)},get closing(){return entry?.phase==='closing'},get dragging(){return entry?.pointer!==null&&Boolean(entry)},
  get content(){return entry?.scroll},get panel(){return entry?.panel},get layer(){return entry?.layer},
  show(options){return open(options)},
  render({header,content,animate=false}){const e=entry;if(!e||e.phase==='closing'||isBlocked()||e.pointer!==null)return false;const focus=document.activeElement,key=focus?.dataset?.intentDirection,top=e.scroll.scrollTop;if(header!==e.headerHtml){e.head.innerHTML=header;e.headerHtml=header}if(content!==e.html){e.scroll.innerHTML=content;e.html=content;e.scroll.scrollTop=top;if(animate&&!reduced.matches)e.scroll.animate([{opacity:0,transform:'translateY(10px)'},{opacity:1,transform:'none'}],{duration:270,easing:'cubic-bezier(.2,.8,.2,1)'});if(key)e.scroll.querySelector(`[data-intent-direction="${key}"]`)?.focus({preventScroll:true})}return true},
  focus(){entry?.head.querySelector('[data-assistant-close]')?.focus({preventScroll:true})},
  close(){const e=entry;if(!e)return Promise.resolve();if(e.closing)return e.closing;e.resetDrag();e.phase='closing';e.layer.classList.remove('is-open');for(const row of e.background){row.node.style.filter=row.filter;row.node.style.transform=row.transform}e.panel.style.pointerEvents='none';e.closing=new Promise(resolve=>{e.done=resolve;e.timer=setTimeout(()=>finish(e),reduced.matches?1:500)});return e.closing},
  dispose(){if(entry)finish(entry,false)}
 };
}
