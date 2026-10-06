<script lang="ts">
 import {number,percent,stamp,type Row} from '../lib/api';import {tr} from '../lib/i18n';import {fullTime,logTime} from './logFormat';
 export let quota:Row={};export let scope='both';export let compactLayout=false;
 function remaining(w:Row){return number(w.remaining_percent)??(number(w.used_percent)===null?null:100-Number(w.used_percent));}
 function resetTime(value:any,short:boolean){if(!short&&!compactLayout)return stamp(value);const n=number(value);if(value==null||value==='')return'—';const date=new Date(n===null?value:n<1e12?n*1000:n);if(!Number.isFinite(date.getTime()))return'—';return short?date.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}):logTime(value);}
</script>
<div class="quota-grid" class:compact-quota={compactLayout}>{#each [[quota.primary??{},'5 小时额度'],[quota.secondary??{},'周额度']] as [window,label],i}{#if scope!=='week'||i===1}{@const n=remaining(window)}<section class="quota surface"><div class="row quota-heading"><h3>{tr(label)}</h3><div class="spacer"></div><div class="quota-value"><strong>{percent(n)}</strong><span class="muted">{tr('剩余')}</span></div></div><div class="segmented-meter" role="meter" aria-label={tr(label)} aria-valuenow={n??undefined} aria-valuemin="0" aria-valuemax="100" aria-valuetext={percent(n)}>{#each Array(45) as _,index}<i class:filled={n!==null&&index/45<Math.max(0,Math.min(100,n))/100}></i>{/each}</div><div class="row between quota-foot"><small>{tr('已用')} {percent(number(window.used_percent)??(n===null?null:100-n))}</small><small title={compactLayout?fullTime(window.resets_at):stamp(window.resets_at)}>{tr('重置')} {resetTime(window.resets_at,i===0)}</small></div></section>{/if}{/each}</div>
{#if quota.status==='not_applicable'}<p class="muted">{tr('API Key 模式不适用订阅额度')}</p>{:else if quota.error}<p class="error">{quota.error}</p>{/if}

<style>
 .quota-value{display:flex;align-items:baseline;gap:7px}
 .quota-value>span{font-size:12px;white-space:nowrap}
 .compact-quota .quota{padding-block:14px;gap:10px}
 .compact-quota .quota-heading{align-items:center}
 .compact-quota .quota-heading h3{white-space:normal}
 .compact-quota .quota-heading strong{font-size:30px;line-height:1.2}
 .compact-quota .segmented-meter{height:10px}
 .compact-quota .quota-foot small{font-size:12px;line-height:1.2}
</style>
