<script lang="ts">
 import{rows,text,number,type Row}from'../lib/api';
 import{tr}from'../lib/i18n';
 import DetailPopover from './DetailPopover.svelte';
 export let data:Row={};
 let search='';
 let expanded:Record<string,boolean>={gpt:true,claude:true,other:true};
 const groups=[{id:'gpt',name:'GPT'},{id:'claude',name:'Claude'},{id:'other',name:'其他'}];
 $: catalog=rows(data.rows);
 $: viewGroups=groups.map(group=>({...group,rows:catalog.filter(row=>row.group===group.id&&(!search.trim()||[row.model,row.display_name,...(Array.isArray(row.aliases)?row.aliases:[])].some(value=>String(value??'').toLowerCase().includes(search.trim().toLowerCase()))))}));
 function price(value:any){const n=number(value);return n===null?'—':'$'+n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:4})}
 function hint(row:Row){if(text(row.tooltip)==='按基础价计费')return '';return document.documentElement.lang==='en'?text(row.tooltip_en,row.tooltip):text(row.tooltip);}
</script>

<div class="price-meta"><p class="muted">{tr('美元 / 1M Token')}</p></div>
<div class="price-toolbar"><input class="price-search" type="search" bind:value={search} placeholder={tr('搜索模型')} aria-label={tr('搜索模型')}/></div>
{#each viewGroups as group (group.id)}
 {@const list=group.rows}
 {@const ttl=list.some(row=>number(row.cache_write_1h)!==null)}
 {@const fields=['input','cache_read','cache_write',...(ttl?['cache_write_1h']:[]),'output']}
 {@const labels=['输入','缓存读取',ttl||group.id==='other'?'缓存写入 5 分钟':'缓存写入',...(ttl?['缓存写入 1 小时']:[]),'输出']}
 <details class="price-group" bind:open={expanded[group.id]}>
  <summary><span>{tr(group.name)}</span><span class="muted price-count">{list.length}</span></summary>
  <div class="price-table-shell"><table class="price-table"><thead><tr><th>{tr('模型')}</th>{#each labels as label}<th>{tr(label)}</th>{/each}</tr></thead><tbody>{#each list as row (row.model)}{@const condition=hint(row)}<tr><td><span class="price-model"><span>{text(row.model)}</span>{#if condition}<DetailPopover hint={condition}/>{/if}</span></td>{#each fields as key}<td>{price(row[key])}</td>{/each}</tr>{/each}</tbody></table>{#if !list.length}<div class="empty">{tr('暂无数据')}</div>{/if}</div>
 </details>
{/each}

<style>
 .price-meta,.price-toolbar{display:flex;align-items:center;gap:10px}.price-toolbar{margin-bottom:18px}.price-search{flex:1;min-width:200px;max-width:600px}
 .price-group{margin-bottom:20px}.price-group summary{font-size:14px;font-weight:500;padding:5px 0 10px}.price-count{font-size:12px;font-weight:400;margin-left:9px}
 .price-table-shell{max-width:100%;min-width:0;overflow-x:auto;border:1px solid var(--border);border-radius:10px}
 .price-table{width:100%;min-width:max-content;table-layout:auto;font-size:12px}
 .price-table th,.price-table td,.price-table th:first-child,.price-table td:first-child,.price-table th:last-child,.price-table td:last-child{width:auto;text-align:center;padding:7px 10px;font-size:12px;line-height:1.4;white-space:nowrap;overflow-wrap:normal}
 .price-model{display:inline-flex;align-items:center;justify-content:center;gap:6px}.price-table td{font-variant-numeric:tabular-nums}
</style>
