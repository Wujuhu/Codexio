<script lang="ts">
 import {number,percent,stamp,type Row} from '../lib/api';import {tr} from '../lib/i18n';
 export let quota:Row={};export let scope='both';
 function remaining(w:Row){return number(w.remaining_percent)??(number(w.used_percent)===null?null:100-Number(w.used_percent));}
</script>
<div class="quota-grid">{#each [[quota.primary??{},'5 小时额度'],[quota.secondary??{},'周额度']] as [window,label],i}{#if scope!=='week'||i===1}{@const n=remaining(window)}<section class="quota surface"><div class="row quota-heading"><h3>{tr(label)}</h3><div class="spacer"></div><strong>{percent(n)}</strong><span class="muted">{tr('剩余')}</span></div><div class="segmented-meter" role="meter" aria-label={tr(label)} aria-valuenow={n??undefined} aria-valuemin="0" aria-valuemax="100" aria-valuetext={percent(n)}>{#each Array(45) as _,index}<i class:filled={n!==null&&index/45<Math.max(0,Math.min(100,n))/100}></i>{/each}</div><div class="row between quota-foot"><small>{tr('已用')} {percent(number(window.used_percent)??(n===null?null:100-n))}</small><small>{tr('重置')} {stamp(window.resets_at)}</small></div></section>{/if}{/each}</div>
{#if quota.status==='not_applicable'}<p class="muted">{tr('API Key 模式不适用订阅额度')}</p>{:else if quota.error}<p class="error">{quota.error}</p>{/if}
