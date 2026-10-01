<script lang="ts">
 import{onDestroy}from'svelte';
 import{api,rows,text,updatedStamp,type Row}from'../lib/api';
 import{tr}from'../lib/i18n';
 import DOMPurify from'dompurify';
 export let initial:Row={};
 let data:Row={};let lastInitial:Row;let busy=false;let hostName='';let hostDirty=false;let cloudKey='';let actionError='';
 let drafts:Record<string,string>={};let dirty=new Set<string>();let confirm:Row|null=null;let removeCloud=false;let alive=true;
 let queue=Promise.resolve();
 $: if(initial!==lastInitial){lastInitial=initial;if(!busy)adopt(initial)}
 onDestroy(()=>{alive=false});
 function adopt(value:Row){data=value;actionError='';if(!hostDirty)hostName=value.name??'';for(const reader of rows(value.readers)){const id=String(reader.id);if(!dirty.has(id))drafts[id]=String(reader.note??'')}drafts={...drafts}}
 function syncLabel(value:Row|undefined){return value?.time?tr('上次同步：')+updatedStamp(value.time):tr('尚未同步')}
 function noteChanged(id:string,value:string){drafts={...drafts,[id]:value};dirty=new Set([...dirty,id])}
 function act(action:string,values:Row={}):Promise<boolean>{
  let success=false;
  queue=queue.catch(()=>{}).then(async()=>{busy=true;actionError='';try{const next=await api('MobileAction',action,values);if(alive)adopt(next);success=true}catch(e){const current=await api('GetSettings').catch(()=>null);if(alive){if(current?.mobile)adopt(current.mobile);if(!data.error&&!data.cloud_error)actionError=e instanceof Error?e.message:String(e)}}finally{busy=false}});
  return queue.then(()=>success);
 }
 async function saveNote(id:string){const value=(drafts[id]??'').trim();if(!dirty.has(id))return;const ok=await act('note',{id,note:value});if(ok&&(drafts[id]??'').trim()===value){dirty=new Set([...dirty].filter(item=>item!==id));drafts={...drafts,[id]:String(rows(data.readers).find(reader=>String(reader.id)===id)?.note??value)}}}
 async function rename(){const value=hostName.trim();if(!hostDirty||!value)return;const ok=await act('rename',{name:value});if(ok&&hostName.trim()===value){hostDirty=false;hostName=String(data.name??value)}}
 async function enroll(){if(await act('enroll',{invite:cloudKey.trim()}))cloudKey=''}
 async function revoke(){const id=confirm?.id;if(id&&await act('revoke',{id})){confirm=null;dirty=new Set([...dirty].filter(item=>item!==id));delete drafts[id]}}
 async function remove(){if(await act('remove-cloud'))removeCloud=false}
</script>

<section class="mobile-settings">
 <div class="sync-heading"><label class="sync-toggle"><input type="checkbox" checked={!!data.enabled} disabled={busy} onchange={event=>{const enabled=event.currentTarget.checked;event.currentTarget.checked=!!data.enabled;void act(enabled?'enable':'disable')}}/>{tr('同步')}</label><small class="sync-stamp">{syncLabel(data.last_sync)}</small></div>
 {#if data.enabled}
  <input class="host-name" maxlength="40" aria-label={tr('本机名称')} value={hostName} oninput={event=>{hostName=event.currentTarget.value;hostDirty=true}} onblur={rename} onkeydown={event=>{if(event.key==='Enter')event.currentTarget.blur()}}/>
  <p class="sync-help">{tr('首次配对请让 iPhone 与电脑处于可互通的局域网。')}</p>
  <div class="cloud-sync">
   <div class="cloud-heading"><span class="cloud-title"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M7 19h11a4 4 0 0 0 .4-8A6.5 6.5 0 0 0 6 9a5 5 0 0 0 1 10Z"/></svg>{tr('云端同步')}</span>{#if data.cloud_enabled}<button disabled={busy} onclick={()=>removeCloud=true}>{tr('移除密钥')}</button>{/if}</div>
   {#if !data.cloud_enabled}<form class="cloud-enroll-row" onsubmit={event=>{event.preventDefault();void enroll()}}><input type="password" autocomplete="off" bind:value={cloudKey} placeholder={tr('云端密钥')} aria-label={tr('云端密钥')}/><button disabled={busy||!cloudKey.trim()}>{tr('启用云同步')}</button></form>{/if}
   {#if data.cloud_error}<p class:error={!data.cloud_error_temporary} class="sync-help" role="status">{text(data.cloud_error)}</p>{/if}
  </div>
  <div><button disabled={busy||rows(data.readers).length>=3} onclick={()=>act('pair')}>{tr('生成二维码')}</button></div>
  {#if data.qr_url}<div class="pairing">{#if /^data:image\/(png|svg\+xml);base64,/.test(data.qr_url)}<img class="qr" src={data.qr_url} alt={tr('配对手机')}/>{/if}</div>
  {:else if data.pairing?.qr_svg}<div class="pairing"><div class="qr">{@html DOMPurify.sanitize(data.pairing.qr_svg,{USE_PROFILES:{svg:true}})}</div></div>{/if}
  {#each rows(Array.isArray(data.pending)?data.pending:data.pending?[data.pending]:[]) as pending}<div class="pending-reader"><p>{text(pending.name??pending.device_name)}</p><div class="sync-actions"><button disabled={busy} onclick={()=>act('deny',{id:pending.id})}>{tr('拒绝')}</button><button disabled={busy} onclick={()=>act('approve',{id:pending.id})}>{tr('确认配对')}</button></div></div>{/each}
  <div class="reader-list">{#each rows(data.readers) as reader (reader.id)}<article class="reader-card"><div class="reader-heading"><strong>{text(reader.name??reader.id)}</strong><button disabled={busy} onclick={()=>confirm=reader}>{tr('撤销')}</button></div><div class="reader-note"><input maxlength="80" aria-label={tr('备注')+' · '+text(reader.name??reader.id)} placeholder={tr('备注')} value={drafts[String(reader.id)]??reader.note??''} oninput={event=>noteChanged(String(reader.id),event.currentTarget.value)} onblur={()=>saveNote(String(reader.id))} onkeydown={event=>{if(event.key==='Enter')event.currentTarget.blur()}}/><small class="sync-stamp">{syncLabel(reader.last_sync)}</small></div></article>{/each}</div>
 {/if}
 {#if data.error}<p class="error" role="status">{text(data.error)}</p>{/if}
 {#if actionError}<p class="error" role="status">{actionError}</p>{/if}
</section>
{#if confirm||removeCloud}<div class="modal-backdrop"><div class="modal" role="alertdialog" aria-modal="true" aria-label={tr(removeCloud?'移除云端密钥？':'撤销')}><h2>{removeCloud?tr('移除云端密钥？'):tr('撤销')+' '+text(confirm?.name)}</h2>{#if removeCloud}<p>{tr('云端同步将停止，局域网配对保留。')}</p>{/if}<div class="sync-actions"><button disabled={busy} onclick={()=>{confirm=null;removeCloud=false}}>{tr('取消')}</button><button disabled={busy} onclick={()=>removeCloud?remove():revoke()}>{tr(removeCloud?'移除密钥':'撤销')}</button></div></div></div>{/if}
<style>
 .mobile-settings{display:flex;flex-direction:column;gap:18px;min-width:0;font-size:13px}.sync-heading,.cloud-heading,.reader-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.sync-heading{flex-wrap:wrap}.sync-toggle,.cloud-title{display:flex;align-items:center;gap:9px;font-size:15px}.sync-help,.sync-stamp{font-size:11px;color:var(--muted)}.sync-stamp{white-space:nowrap}.host-name{width:100%}.cloud-sync{display:flex;flex-direction:column;gap:12px}.cloud-title{font-size:14px}.cloud-enroll-row{display:flex;flex-wrap:wrap;gap:10px;min-width:0}.cloud-enroll-row input{flex:1;min-width:140px}.sync-actions{display:flex;flex-wrap:wrap;gap:10px;justify-content:flex-end}.reader-list{display:flex;flex-direction:column;gap:12px}.reader-card{background:var(--surface);border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:10px;min-width:0}.reader-heading strong{font-size:14px;font-weight:500}.reader-note{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.reader-note input{flex:1;min-width:140px}.pairing{margin:0;align-items:flex-start}.qr{width:220px;max-width:100%;height:auto;aspect-ratio:1;background:white;border-radius:8px;padding:8px}.qr :global(svg){display:block;width:100%;height:100%}.pending-reader{display:flex;flex-direction:column;gap:10px}
</style>
