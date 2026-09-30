<script lang="ts">
 import{api,text,type Row}from'../lib/api';import{tr}from'../lib/i18n';
 export let state:Row={};export let mock=false;export let onstate:(r:Row)=>void;export let onerror:(e:any)=>void;let busy=false;
 async function install(){busy=true;try{await api('InstallUpdate')}catch(e){onerror(e)}finally{busy=false}}
 async function later(){try{onstate(await api('DeferUpdate'))}catch(e){onerror(e)}}
</script>
<div class="modal-backdrop"><div class="modal" role="dialog" aria-modal="true" aria-label={tr('应用更新')}><h2>{tr('应用更新')} {text(state.version,'')}</h2>{#if state.notes}<p class="update-notes">{state.notes}</p>{/if}{#if state.error}<p class="error">{state.error}</p>{/if}{#if ['downloading','installing','verifying'].includes(state.status)}<progress max="100" value={Number(state.progress??0)}></progress><p class="muted">{numericProgress(state.progress)}%</p>{/if}<div class="row"><button class="primary" disabled={busy||mock||['downloading','installing','verifying'].includes(state.status)} onclick={install}>{tr('立即更新')}</button><button disabled={busy} onclick={later}>{tr('稍后')}</button>{#if state.url}<button class="quiet" onclick={()=>api('OpenURL',state.url).catch(onerror)}>{tr('打开下载页面')}</button>{/if}</div></div></div>
<script module lang="ts">function numericProgress(value:any){return Number.isFinite(Number(value))?Math.round(Number(value)):0}</script>
