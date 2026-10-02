<script lang="ts">
  import {onMount,onDestroy} from 'svelte';
  import {type Row} from '../lib/api';
  import {tr} from '../lib/i18n';
  import {loadRequestImage} from './requestImages';
  export let requestId='';
  export let image:Row={};
  export let claim:(digest:string)=>boolean=()=>true;
  let host:HTMLSpanElement,dialog:HTMLDialogElement,source='',state='pending',duplicate=false,zoomed=false;
  const controller=new AbortController();
  const en=()=>document.documentElement.lang==='en';
  onMount(()=>{
    const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){observer.disconnect();void load()}},{rootMargin:'160px'});
    observer.observe(host);
    return()=>observer.disconnect();
  });
  onDestroy(()=>{controller.abort();if(source)URL.revokeObjectURL(source);dialog?.close()});
  async function load(){
    state='loading';
    try{
      const value=await loadRequestImage(requestId,String(image.id),controller.signal);
      if(controller.signal.aborted)return;
      if(value.availability!=='available'||!/^data:image\/(png|jpeg);base64,/.test(String(value.dataURL))){state='unavailable';return}
      if(!claim(String(value.sha256))){duplicate=true;return}
      const data=String(value.dataURL).split(',')[1];
      if(data.length>1398104){state='unavailable';return}
      const decoded=atob(data),bytes=new Uint8Array(decoded.length);
      for(let i=0;i<decoded.length;i++)bytes[i]=decoded.charCodeAt(i);
      source=URL.createObjectURL(new Blob([bytes],{type:String(value.mime)}));
      state='available';
    }catch(error){if(!controller.signal.aborted)state='unavailable'}
  }
  function open(){zoomed=true;dialog.showModal()}
</script>
<span bind:this={host} class="request-image" class:duplicate>
  {#if !duplicate}
    {#if source}
      <button class="image-preview" type="button" aria-label={en()?'Enlarge image':'放大图片'} onclick={open}><img src={source} alt={String(image.name||'')} loading="lazy" onerror={()=>state='unavailable'}/></button>
      {#if state==='unavailable'}<span class="image-status">{image.name} · {tr('暂不可用')}</span>{/if}
    {:else}<span class="image-status">{image.name|| (en()?'Image':'图片')} · {state==='unavailable'?tr('暂不可用'):tr('正在加载')}</span>{/if}
    <dialog bind:this={dialog} onclose={()=>zoomed=false} onclick={event=>{if(event.target===dialog)dialog.close()}} onkeydown={event=>{if(event.key==='Escape')dialog.close()}} aria-label={String(image.name||(en()?'Image':'图片'))}>
      <button class="close-image" type="button" onclick={()=>dialog.close()} aria-label={tr('关闭')}>×</button>
      {#if zoomed&&source}<img class="zoom-image" src={source} alt={String(image.name||'')}/>{/if}
    </dialog>
  {/if}
</span>
<style>
  .request-image{display:block;margin:10px 0;max-width:100%;min-width:0}.request-image.duplicate{display:none}.image-preview{display:block;padding:0;overflow:hidden;border:1px solid var(--border);border-radius:9px;background:transparent;width:100%;min-height:0}.image-preview img{display:block;max-width:100%;max-height:420px;width:auto;height:auto;margin:auto;object-fit:contain}.image-status{display:block;padding:12px;border:1px solid var(--border);border-radius:8px;color:var(--muted);font-size:11px;overflow-wrap:anywhere}dialog{position:fixed;max-width:95vw;max-height:95vh;border:1px solid var(--border);border-radius:12px;padding:35px 12px 12px;background:var(--surface);color:var(--text);overflow:auto}dialog::backdrop{background:rgba(0,0,0,.65)}.close-image{position:fixed;right:calc(2.5vw + 14px);top:calc(2.5vh + 8px);border-radius:50%;width:30px;height:30px;padding:0;font-size:22px;background:var(--surface);z-index:1}.zoom-image{display:block;max-width:calc(95vw - 26px);max-height:calc(95vh - 60px);width:auto;height:auto;object-fit:contain;margin:auto}
</style>
