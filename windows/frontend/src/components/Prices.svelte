<script lang="ts">
 import{api,rows,text,number,updatedStamp,type Row}from'../lib/api';
 import{tr}from'../lib/i18n';
 export let data:Row={};export let onreload:()=>void;export let onerror:(e:any)=>void;
 let selected='';let editing=false;let draft:Row={};let busy=false;let search='';
 const fields=['input','cache_read','cache_write','output'];
 $: filtered=rows(data.rows).filter(row=>!search.trim()||String(row.model??'').toLowerCase().includes(search.trim().toLowerCase()));
 $: selectedModel=selected||'';
 $: base=rows(data.rows).find(row=>row.model===selectedModel&&String(row.service_tier??'default')==='default'&&Number(row.threshold??0)===0);
 function select(row:Row){selected=String(row.model??'');}
 function edit(){if(!base)return;draft=Object.fromEntries(fields.map(key=>[key,base[key]??'']));editing=true;}
 async function save(){if(!selectedModel)return;busy=true;try{await api('SavePrice',selectedModel,Object.fromEntries(fields.map(key=>[key,draft[key]===''?null:Number(draft[key])])));editing=false;onreload()}catch(e){onerror(e)}finally{busy=false}}
 async function restore(){if(!selectedModel)return;busy=true;try{await api('SavePrice',selectedModel,{});onreload()}catch(e){onerror(e)}finally{busy=false}}
 async function sync(){busy=true;try{await api('SyncPrices');onreload()}catch(e){onerror(e)}finally{busy=false}}
 function price(value:any){const n=number(value);return n===null?'—':'$'+n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}
 function condition(row:Row){return (String(row.service_tier??'default')==='priority'?'Fast':'Standard')+(Number(row.threshold??0)>0?' >272K':'')}
</script>

<div class="price-meta"><p class="muted">{tr('美元 / 1M Token')}</p><span class="muted">{data.status?.updated_at?tr('上次更新：')+updatedStamp(data.status.updated_at):''}</span></div>
{#if data.status?.error}<p class="warning">{text(data.status.error)}</p>{/if}
<div class="price-toolbar"><input class="price-search" type="search" bind:value={search} placeholder={tr('搜索模型')} aria-label={tr('搜索模型')}/><button disabled={busy} onclick={sync}>{tr('立即同步')}</button><button disabled={busy||!base} onclick={edit}>{tr('编辑基础价')}</button><button disabled={busy||!base} onclick={restore}>{tr('恢复自动基础价')}</button></div>
<section class="price-table-shell table-scroll"><table class="price-table"><thead><tr>{#each ['模型','条件','输入','缓存读取','缓存写入','输出'] as label}<th>{tr(label)}</th>{/each}</tr></thead><tbody>{#each filtered as row}<tr class:selected={selectedModel===row.model} tabindex="0" onclick={()=>select(row)} onkeydown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select(row)}}}><td>{text(row.model)}</td><td>{condition(row)}</td>{#each fields as key}<td>{price(row[key])}</td>{/each}</tr>{/each}</tbody></table>{#if !filtered.length}<div class="empty">{tr('暂无数据')}</div>{/if}</section>
{#if editing&&base}<div class="modal-backdrop"><form class="modal" onsubmit={event=>{event.preventDefault();void save()}}><h2>{selectedModel}</h2><p class="muted">{tr('编辑 Standard 基础价 · 美元 / 1M Token')}</p>{#each fields as key,index}<label class="field"><span>{tr(['输入','缓存读取','缓存写入','输出'][index])}</span><input type="number" step="any" min="0" bind:value={draft[key]} placeholder="—"/></label>{/each}<div class="row modal-actions"><button type="button" disabled={busy} onclick={()=>editing=false}>{tr('取消')}</button><button class="primary" disabled={busy}>{tr('保存')}</button></div></form></div>{/if}

<style>
 .price-meta,.price-toolbar{display:flex;align-items:center;gap:10px}.price-meta{justify-content:space-between}.price-toolbar{flex-wrap:wrap}.price-search{flex:1;min-width:220px}.price-table tbody tr{cursor:pointer}.price-table tbody tr.selected{background:var(--hover);outline:0}.price-table tbody tr:focus-visible{outline:1px solid var(--muted);outline-offset:-1px}.price-table-shell{width:600px;max-width:100%;min-width:0;align-self:flex-start;overflow-x:auto}.price-table{width:100%;min-width:max-content;table-layout:auto;font-size:11px}.price-table th,.price-table td,.price-table th:first-child,.price-table td:first-child,.price-table th:nth-child(2),.price-table td:nth-child(2),.price-table th:last-child,.price-table td:last-child{width:auto;text-align:center;padding:5px 6px;font-size:11px;line-height:1.4;white-space:nowrap;overflow-wrap:normal}.modal-actions{justify-content:flex-end}
</style>
