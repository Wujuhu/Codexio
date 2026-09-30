<script lang="ts">
 import {compact,numeric,percent,number,text,type Row}from'../lib/api';
 import {tr}from'../lib/i18n';
 export let stats:Row={};
 const activityFields=[['total_tokens','累计 Token 数'],['peak_daily_tokens','单日峰值 Token'],['longest_chat_seconds','最长聊天时长'],['current_streak_days','当前连续天数'],['longest_streak_days','最长连续天数']];
 function activityValue(key:string){
   const value=number(stats[key]);
   if(value===null)return'—';
   if(key.endsWith('tokens'))return compact(value);
   if(key==='longest_chat_seconds')return value<60?numeric(value)+'s':value<3600?numeric(value/60)+'m':(value/3600).toFixed(1)+'h';
   return numeric(value)+' '+tr('天');
 }
</script>
<section class="surface insights"><h3>{tr('活动洞察')}</h3><div class="insight-values"><div><span class="muted">{tr('快速模式')}</span><strong>{percent(stats.fast_percent)}</strong></div><div><span class="muted">{tr('最常用的推理强度')}</span><strong>{stats.most_used_effort?text(stats.most_used_effort)+' · '+percent(stats.effort_percent):'—'}</strong></div></div><p class="muted insight-note">{tr('本机记录 · 按模型调用次数统计模式占比')}{#if Number(stats.unknown_speed)>0} · {tr('速度未知')} {numeric(stats.unknown_speed)} {tr('次')}{/if}{#if Number(stats.unknown_effort)>0} · {tr('推理强度未知')} {numeric(stats.unknown_effort)} {tr('次')}{/if}{#if stats.duration_partial} · {tr('时长仅含已记录区间')}{/if}</p>{#if activityFields.some(([key])=>Object.prototype.hasOwnProperty.call(stats,key))}<div class="activity-stats">{#each activityFields as [key,label]}{#if Object.prototype.hasOwnProperty.call(stats,key)}<div><strong>{activityValue(key)}</strong><span class="muted">{tr(label)}</span></div>{/if}{/each}</div>{/if}</section>
