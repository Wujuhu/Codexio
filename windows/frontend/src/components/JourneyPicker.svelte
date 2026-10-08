<script lang="ts">
 import {onMount,tick} from 'svelte';
 import {popover,viewportBounds} from '../lib/floating';
 export let label='';export let title='—';export let value='';
 export let options:{id:string;title:string}[]=[];
 export let onselect:(id:string)=>void;
 let open=false,active=0,left=0,top=0,width=180;
 const pickerID='journey-picker-'+crypto.randomUUID();
 let trigger:HTMLButtonElement,popup:HTMLDivElement;
 const preview=(text:string)=>{const short=text.replace(/\s+/g,' ').trim();return short.length>80?short.slice(0,80)+'…':short||'—'};
 // Reuse the detail popover's top layer and viewport bounds: scrolling cards
 // cannot clip the menu, and small windows may show fewer than ten rows.
 function place(){
  if(!open||!trigger||!popup)return;
  const rect=trigger.getBoundingClientRect(),view=viewportBounds(),edge=12,gap=4;
  if(rect.bottom<view.top||rect.top>view.bottom){open=false;return}
  width=Math.min(520,Math.max(160,rect.width),view.width-edge*2);
  popup.style.width=`${width}px`;
  popup.style.maxHeight=`${Math.min(310,view.height-edge*2)}px`;
  const height=popup.getBoundingClientRect().height;
  left=Math.max(view.left+edge,Math.min(rect.left,view.right-width-edge));
  top=Math.max(view.top+edge,Math.min(rect.bottom+gap+height<=view.bottom-edge?rect.bottom+gap:rect.top-gap-height,view.bottom-height-edge));
 }
 async function toggle(){if(open){close();return}active=Math.max(0,options.findIndex(o=>o.id===value));open=true;await tick();place();popup.focus();reveal()}
 function close(){open=false;trigger?.focus()}
 function select(index:number){const option=options[index];if(option){close();onselect(option.id)}}
 function reveal(){popup?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({block:'nearest'})}
 function key(event:KeyboardEvent){
  if(!open){if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();void toggle()}return}
  if(event.key==='Escape'){event.preventDefault();close()}
  else if(event.key==='Tab'){open=false}
  else if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
   event.preventDefault();active=event.key==='Home'?0:event.key==='End'?options.length-1:Math.max(0,Math.min(options.length-1,active+(event.key==='ArrowDown'?1:-1)));reveal();
  }else if(['Enter',' '].includes(event.key)){event.preventDefault();select(active)}
 }
 function outside(event:PointerEvent){if(open&&!popup?.contains(event.target as Node)&&!trigger?.contains(event.target as Node))open=false}
 onMount(()=>{document.addEventListener('scroll',place,true);const viewport=window.visualViewport;viewport?.addEventListener('resize',place);viewport?.addEventListener('scroll',place);return()=>{document.removeEventListener('scroll',place,true);viewport?.removeEventListener('resize',place);viewport?.removeEventListener('scroll',place)}});
</script>
<svelte:window onpointerdown={outside} onresize={place}/>
<button bind:this={trigger} class="picker" aria-label={label} aria-haspopup="listbox" aria-controls={open?pickerID:undefined} aria-expanded={open} disabled={!options.length} onclick={toggle} onkeydown={key}><span>{preview(title)}</span><span class="chevron">⌄</span></button>
{#if open}
 <div bind:this={popup} id={pickerID} class="picker-options" popover="manual" use:popover style:left={`${left}px`} style:top={`${top}px`} style:width={`${width}px`} role="listbox" aria-label={label} aria-activedescendant={`${pickerID}-${active}`} tabindex="-1" onkeydown={key}>
  {#each options as option,index(option.id)}<button id={`${pickerID}-${index}`} role="option" aria-selected={value===option.id} tabindex="-1" data-index={index} class:highlight={index===active} onpointermove={()=>active=index} onclick={()=>select(index)}><span class="check">{value===option.id?'✓':''}</span><span class="option-title">{preview(option.title)}</span></button>{/each}
 </div>
{/if}
<style>
 .picker{width:100%;height:30px;min-width:0;justify-content:space-between;background:var(--surface);border-radius:6px}.picker>span:first-child{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left}.chevron{color:var(--muted);flex:none}.picker-options{position:fixed;inset:auto;margin:0;padding:4px;max-width:calc(100vw - 24px);max-height:310px;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:8px;box-shadow:0 6px 22px #0002;box-sizing:border-box}.picker-options:not(:popover-open){display:none}.picker-options::backdrop{pointer-events:none}.picker-options button{display:flex;justify-content:flex-start;width:100%;height:30px;min-height:30px;padding:0 8px;border:0;border-radius:4px;background:transparent;text-align:left}.picker-options button.highlight{background:var(--hover)}.check{width:12px;flex:none}.option-title{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
</style>
