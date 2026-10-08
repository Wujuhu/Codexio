<script lang="ts">
 import {onDestroy} from 'svelte';
 import {tr} from '../lib/i18n';
 export let command='';export let tool='';export let onopen:(()=>void)|undefined=undefined;
 let dialog:HTMLDialogElement,opened=false;
 // Use the same native top-layer dialog as RequestImage; long commands stay
 // outside the timeline's layout and cannot be clipped by its scroll view.
 function open(){onopen?.();opened=true;dialog.showModal()}
 onDestroy(()=>dialog?.close());
</script>
<button class="command-button" type="button" aria-label={tr('打开命令')} title={tr('打开命令')} onclick={open}><svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 5-5 5 5 5m8-10 5 5-5 5m-3-12-3 14"/></svg></button>
<dialog bind:this={dialog} aria-label={tool} onclose={()=>opened=false} onclick={event=>{if(event.target===dialog)dialog.close()}} onkeydown={event=>{if(event.key==='Escape')dialog.close()}}>
 {#if opened}<header><strong>{tool}</strong><button class="quiet" type="button" onclick={()=>dialog.close()} aria-label={tr('关闭')}>×</button></header><textarea readonly value={command} aria-label={tool} spellcheck={false} wrap="off"></textarea>{/if}
</dialog>
<style>
 .command-button{padding:3px;min-height:22px;width:24px;flex:none;border:0;border-radius:5px;background:color-mix(in srgb,var(--text) 4%,transparent);color:var(--muted)}.command-button:hover{background:var(--hover);color:var(--text)}dialog{position:fixed;width:min(620px,calc(100vw - 40px));max-width:calc(100vw - 40px);max-height:calc(100dvh - 40px);padding:14px;border:1px solid var(--border);border-radius:12px;color:var(--text);background:var(--bg);box-shadow:0 10px 40px #0003;overflow:hidden}dialog::backdrop{background:#0004}header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding-bottom:10px;border-bottom:1px solid var(--border);font-size:13px}header strong{min-width:0;overflow-wrap:anywhere;font-weight:500}header button{font-size:20px;line-height:1;padding:3px 6px;flex:none}textarea{display:block;width:100%;height:min(360px,calc(100dvh - 125px));margin:12px 0 0;resize:none;border:0;border-radius:0;background:transparent;overflow:auto;overscroll-behavior:contain;white-space:pre;font:12px/1.6 ui-monospace,Consolas,monospace;text-align:left;user-select:text;padding:2px 2px 12px}
</style>
