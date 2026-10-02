<script lang="ts">
 import {compact,numeric,percent,number,text,type Row} from '../lib/api';
 import {tr} from '../lib/i18n';
 export let stats:Row={};
 export let section:'metrics'|'insights'|'all'='all';
 const activityFields=[['total_tokens','累计 Token 数'],['peak_daily_tokens','单日峰值 Token'],['longest_chat_seconds','最长聊天时长'],['current_streak_days','当前连续天数'],['longest_streak_days','最长连续天数']];
 function activityValue(source:Row,key:string){
   const value=number(source[key]);
   if(value===null)return '—';
   if(key.endsWith('tokens'))return compact(value);
   if(key==='longest_chat_seconds')return value<60?numeric(value)+'s':value<3600?numeric(value/60)+'m':(value/3600).toFixed(1)+'h';
   return numeric(value)+' '+tr('天');
 }
</script>
{#if section!=='insights'}
 <section class="activity-metrics" aria-label={tr('活动')}>
  {#each activityFields as [key,label]}<div><strong>{activityValue(stats,key)}</strong><span>{tr(label)}</span></div>{/each}
 </section>
{/if}
{#if section!=='metrics'}
 <section class="local-insights">
  <h3>{tr('活动洞察')}</h3>
  <div class="local-insight-values"><div><span>{tr('快速模式')}</span><strong>{percent(stats.fast_percent)}</strong></div><div><span>{tr('最常用的推理强度')}</span><strong>{stats.most_used_effort?text(stats.most_used_effort)+' · '+percent(stats.effort_percent):'—'}</strong></div></div>
  {#if Number(stats.unknown_speed)>0||Number(stats.unknown_effort)>0||stats.duration_partial}<p class="insight-quality muted">{#if Number(stats.unknown_speed)>0}{tr('速度未知')} {numeric(stats.unknown_speed)} {tr('次')}{/if}{#if Number(stats.unknown_effort)>0}<span>{tr('推理强度未知')} {numeric(stats.unknown_effort)} {tr('次')}</span>{/if}{#if stats.duration_partial}<span>{tr('时长仅含已记录区间')}</span>{/if}</p>{/if}
 </section>
{/if}
<style>
 .activity-metrics{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));border:1px solid var(--border);border-radius:20px;padding:22px 0}.activity-metrics>div{display:flex;flex-direction:column;align-items:center;gap:9px;padding:0 8px;min-width:0}.activity-metrics>div+div{border-left:1px solid var(--border)}.activity-metrics strong{font-size:22px;font-weight:500;font-variant-numeric:tabular-nums;white-space:nowrap}.activity-metrics span{font-size:12px;color:var(--muted);text-align:center}.local-insights h3{font-size:16px;font-weight:500;margin:8px 0 16px}.local-insight-values{display:grid;grid-template-columns:1fr 1fr;gap:55px;font-size:16px}.local-insight-values>div{display:flex;align-items:baseline;justify-content:space-between;gap:12px}.local-insight-values span{color:var(--muted)}.local-insight-values strong{font-weight:400;white-space:nowrap}.insight-quality{font-size:10px;margin-top:12px;display:flex;gap:10px;flex-wrap:wrap}@container canvas (max-width:559px){.activity-metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.activity-metrics>div+div{border-left:0}.local-insight-values{grid-template-columns:1fr;gap:12px}}
</style>
