<script lang="ts">
 import{onDestroy}from'svelte';import Details from './Details.svelte';import Icon from './Icon.svelte';import{tr}from'../lib/i18n';
 export let id='';export let onerror:(e:any)=>void;let open=false;let pinned=false;let left=0;let top=0;let trigger:HTMLButtonElement;let popup:HTMLDivElement;let timer:ReturnType<typeof setTimeout>;
 function show(){clearTimeout(timer);const r=trigger.getBoundingClientRect();left=Math.max(14,Math.min(window.innerWidth-455,r.right+10));top=Math.max(14,Math.min(window.innerHeight-520,r.top-12));open=true;}
 function leave(){if(!pinned){clearTimeout(timer);timer=setTimeout(()=>open=false,180)}}
 function close(){open=false;pinned=false;clearTimeout(timer)}
 function outside(e:PointerEvent){if(open&&!popup?.contains(e.target as Node)&&!trigger?.contains(e.target as Node))close()}
 function key(e:KeyboardEvent){if(e.key==='Escape'&&open){close();trigger.focus()}}
 onDestroy(()=>clearTimeout(timer));
</script>
<svelte:window onpointerdown={outside} onkeydown={key}/>
<button bind:this={trigger} class="quiet detail-trigger" aria-haspopup="dialog" aria-expanded={open} onmouseenter={show} onmouseleave={leave} onfocus={show} onblur={leave} onclick={e=>{e.stopPropagation();if(open&&pinned)close();else{pinned=true;show()}}}>{tr('详情')}</button>
{#if open}<div bind:this={popup} class="detail-popover" style:left={`${left}px`} style:top={`${top}px`} role="dialog" aria-label={tr('请求详情')} tabindex="-1" onmouseenter={()=>clearTimeout(timer)} onmouseleave={leave} onfocusin={()=>clearTimeout(timer)} onfocusout={e=>{if(!popup.contains(e.relatedTarget as Node))leave()}}><div class="detail-popover-head"><button class="quiet" onclick={()=>{close();trigger.focus()}} aria-label={tr('关闭')}><Icon name="close" size={15}/></button></div><Details {id} {onerror}/></div>{/if}
