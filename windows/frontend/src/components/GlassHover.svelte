<script lang="ts">
 import {onMount} from 'svelte';
 import {numeric,compact,number,priced,type Row} from '../lib/api';
 import {tr} from '../lib/i18n';
 export let row:Row;
 export let title:string;
 export let x:number;
 export let y:number;
 export let onclose:()=>void=()=>{};
 let viewportWidth=1024,viewportHeight=768,width=250,height=156;
 $: left=Math.max(8,Math.min(x+14,viewportWidth-width-8));
 $: top=Math.max(8,y+16+height<=viewportHeight-8?y+16:y-height-14);
 onMount(()=>{const close=()=>onclose();window.addEventListener('scroll',close,true);window.addEventListener('blur',close);return()=>{window.removeEventListener('scroll',close,true);window.removeEventListener('blur',close);};});
</script>
<svelte:window bind:innerWidth={viewportWidth} bind:innerHeight={viewportHeight}/>
<div class="usage-glass-hover" role="tooltip" style:left={`${left}px`} style:top={`${top}px`} bind:clientWidth={width} bind:clientHeight={height}>
 <strong>{title}</strong>
 <div><span>Token</span><b>{compact(row.tokens)}</b></div>
 <div><span>{tr('费用')}</span><b>{priced(row,'usd')}</b></div>
 <div><span>{tr('用户请求')}</span><b>{numeric(row.user_requests)}</b></div>
 {#if row.tokens_complete===false || (number(row.skipped?.tokens)??0)>0}<small>{tr('数据不完整')}</small>{/if}
</div>
<style>
 .usage-glass-hover{position:fixed;z-index:1200;pointer-events:none;width:250px;max-width:calc(100vw - 16px);padding:18px;border-radius:18px;color:var(--text);background:color-mix(in srgb,var(--bg) 82%,transparent);backdrop-filter:blur(28px) saturate(150%);-webkit-backdrop-filter:blur(28px) saturate(150%);border:1px solid color-mix(in srgb,var(--control) 60%,transparent);box-shadow:0 12px 35px #0003,inset 0 1px 0 #ffffff30;font-size:12px;font-variant-numeric:tabular-nums;line-height:1.5}
 .usage-glass-hover>strong{display:block;font-weight:600;margin-bottom:12px}.usage-glass-hover>div{display:flex;justify-content:space-between;align-items:baseline;gap:16px;margin-top:9px}.usage-glass-hover span,.usage-glass-hover small{color:var(--muted)}.usage-glass-hover b{font-weight:400;text-align:right;overflow-wrap:anywhere}.usage-glass-hover small{display:block;margin-top:9px}
</style>
