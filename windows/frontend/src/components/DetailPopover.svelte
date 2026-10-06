<script lang="ts">
  import {onDestroy,onMount,tick} from 'svelte';
  import Details from './Details.svelte';
  import Icon from './Icon.svelte';
  import {tr} from '../lib/i18n';
  import {type Row} from '../lib/api';
  import {popover,viewportBounds} from '../lib/floating';
  export let id=''; export let record:Row={}; export let onerror:(e:any)=>void;
  let open=false, pinned=false, shown=false, returningFocus=false, left=0, top=0;
  let detailRevision='';
  $: revision=JSON.stringify([id,record.model,record.models,record.reasoning_effort,record.service_tier,record.model_context_window,record.request_status??record.status,record.total_tokens,record.output_tokens,record.ttft_ms,record.cost_usd,record.duration_ms,record.duration_running,record.duration_started_at,record.duration_base_ms,record.prompt_preview,record.output_preview,record.call_count,record.upstream_observations??record.observations??record.upstream_models??record.upstream_model]);
  $: if(open&&revision!==detailRevision)detailRevision=revision;
  let trigger:HTMLButtonElement, popup:HTMLDivElement;
  let timer:ReturnType<typeof setTimeout>;
  function place(){
    if(!trigger||!popup||!open)return;
    const rect=trigger.getBoundingClientRect(), viewport=viewportBounds(), gap=10, edge=12;
    if(rect.bottom<viewport.top||rect.top>viewport.bottom||rect.right<viewport.left||rect.left>viewport.right){close();return;}
    popup.style.maxWidth=`${Math.max(0,viewport.width-edge*2)}px`;
    popup.style.maxHeight=`${Math.max(0,viewport.height-edge*2)}px`;
    const {width,height}=popup.getBoundingClientRect();
    const preferred=rect.left-width-gap;
    left=Math.max(viewport.left+edge,Math.min(viewport.right-width-edge,preferred>=viewport.left+edge?preferred:rect.right+gap));
    top=Math.max(viewport.top+edge,Math.min(viewport.bottom-height-edge,rect.top-12));
  }
  async function show(){clearTimeout(timer);shown=true;open=true;await tick();place();}
  function leave(){if(!pinned&&!popup?.contains(document.activeElement)){clearTimeout(timer);timer=setTimeout(()=>open=false,250)}}
  function close(){open=false;pinned=false;clearTimeout(timer)}
  function restoreFocus(){close();returningFocus=true;trigger.focus();queueMicrotask(()=>returningFocus=false)}
  async function toggle(event:MouseEvent){event.stopPropagation();if(open&&pinned)restoreFocus();else{pinned=true;show();await tick();popup?.focus()}}
  function outside(event:PointerEvent){if(open&&!popup?.contains(event.target as Node)&&!trigger?.contains(event.target as Node))close()}
  function key(event:KeyboardEvent){if(event.key==='Escape'&&open){event.preventDefault();restoreFocus()}}
  function reposition(){if(open)place()}
  function measure(node:HTMLElement){const observer=new ResizeObserver(reposition);observer.observe(node);return{destroy:()=>observer.disconnect()};}
  onMount(()=>{const viewport=window.visualViewport;document.addEventListener('scroll',reposition,true);viewport?.addEventListener('resize',reposition);viewport?.addEventListener('scroll',reposition);return()=>{document.removeEventListener('scroll',reposition,true);viewport?.removeEventListener('resize',reposition);viewport?.removeEventListener('scroll',reposition);};});
  onDestroy(()=>clearTimeout(timer));
</script>
<svelte:window onpointerdown={outside} onkeydown={key} onresize={reposition}/>
<button bind:this={trigger} class="quiet detail-trigger" aria-haspopup="dialog" aria-expanded={open} onmouseenter={show} onmouseleave={leave} onfocus={()=>{if(!returningFocus)show()}} onblur={leave} onclick={toggle}>{tr('详情')}</button>
{#if shown}
  <div bind:this={popup} class="detail-popover" popover="manual" use:popover={open} use:measure style:left={`${left}px`} style:top={`${top}px`} role="dialog" aria-label={tr('请求详情')} tabindex="-1" onmouseenter={()=>clearTimeout(timer)} onmouseleave={leave} onfocusin={()=>clearTimeout(timer)} onfocusout={event=>{if(!popup.contains(event.relatedTarget as Node)&&event.relatedTarget!==trigger)leave()}}>
    <button class="quiet popover-close" onclick={restoreFocus} aria-label={tr('关闭')}><Icon name="close" size={15}/></button>
    <Details {id} {onerror} reloadKey={detailRevision}/>
  </div>
{/if}
<style>
  .detail-trigger{font-size:12px;padding:1px 3px!important;color:var(--focus)!important}.detail-popover{position:fixed;inset:auto;margin:0;padding:0;color:var(--text);width:360px;height:500px;max-width:calc(100vw - 24px);max-height:calc(100vh - 24px);z-index:35;overflow:hidden;border:1px solid color-mix(in srgb,var(--border) 72%,transparent);border-radius:18px;box-shadow:0 12px 36px #0003;background:color-mix(in srgb,var(--surface) 87%,transparent);backdrop-filter:blur(24px) saturate(140%);display:flex;flex-direction:column;text-align:left;white-space:normal}.detail-popover:not(:popover-open){display:none}.detail-popover::backdrop{pointer-events:none}.popover-close{position:absolute;right:10px;top:11px;padding:4px!important;z-index:2}.detail-popover :global(.inspector){height:100%;max-height:100%;background:transparent;border:0;border-radius:0;box-shadow:none}
</style>
