<script lang="ts">
 import {onDestroy,tick} from 'svelte';
 import {tr} from '../lib/i18n';
 import JourneyIcon from './JourneyIcon.svelte';
 export let command='';export let tool='';export let onopen:(()=>void)|undefined=undefined;
 let dialog:HTMLDialogElement,field:HTMLTextAreaElement,opened=false;
 // Use the same native top-layer dialog as RequestImage; long commands stay
 // outside the timeline's layout and cannot be clipped by its scroll view.
 async function open(){onopen?.();opened=true;dialog.showModal();await tick();fit()}
 function fit(){if(!opened||!field)return;field.style.height='0px';field.style.height=`${Math.min(field.scrollHeight,Math.max(20,Math.min(360,(window.visualViewport?.height??window.innerHeight)-120)))}px`}
 function measure(node:HTMLTextAreaElement){let width=0;const observer=new ResizeObserver(()=>{if(node.clientWidth!==width){width=node.clientWidth;fit()}});observer.observe(node);return{destroy:()=>observer.disconnect()}}
 onDestroy(()=>dialog?.close());
</script>
<svelte:window onresize={fit}/>
<button class="command-button" type="button" aria-label={tr('打开命令')} title={tr('打开命令')} onclick={open}><JourneyIcon kind="command" size={14}/></button>
<dialog bind:this={dialog} aria-label={tool} onclose={()=>opened=false} onclick={event=>{if(event.target===dialog)dialog.close()}} onkeydown={event=>{if(event.key==='Escape')dialog.close()}}>
 {#if opened}<header><strong>{tool}</strong><button class="quiet" type="button" onclick={()=>dialog.close()} aria-label={tr('关闭')}><JourneyIcon kind="close" size={14}/></button></header><textarea bind:this={field} use:measure readonly value={command} aria-label={tool} spellcheck={false} wrap="soft"></textarea>{/if}
</dialog>
<style>
 .command-button{padding:3px;min-height:22px;height:22px;width:24px;flex:none;border:0;border-radius:5px;background:color-mix(in srgb,var(--text) 4%,transparent);color:var(--muted)}.command-button:hover{background:var(--hover);color:var(--text)}dialog{position:fixed;width:min(620px,calc(100vw - 40px));max-width:calc(100vw - 40px);max-height:calc(100dvh - 40px);padding:12px;border:1px solid var(--border);border-radius:12px;color:var(--text);background:var(--bg);box-shadow:0 10px 40px #0003;overflow:hidden}dialog::backdrop{background:#0004}header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding-bottom:8px;border-bottom:1px solid var(--border);font-size:12px}header strong{min-width:0;overflow-wrap:anywhere;font-weight:500}header button{font-size:20px;line-height:1;padding:3px 6px;flex:none}textarea{display:block;width:100%;height:auto;min-height:0;max-height:min(360px,calc(100dvh - 120px));margin:8px 0 0;resize:none;border:0;border-radius:0;background:transparent;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;font:12px/1.6 ui-monospace,Consolas,monospace;text-align:left;user-select:text;padding:0}
</style>
