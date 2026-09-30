<script lang="ts">
 import {compact,numeric,percent,cost,number,type Row} from '../lib/api';
 import {tr} from '../lib/i18n';
 export let summary:Row={};
 export let comparison:Row={};
 const fields=[['usd','费用'],['tokens','总 Token'],['user_requests','用户请求'],['cache_hit_rate','命中率']];
 function value(key:string){
   if(key==='usd')return cost(summary.usd);
   if(key==='cache_hit_rate'){
     const rate=number(summary[key]);
     return percent(rate===null?null:rate*(rate<=1?100:1));
   }
   return key==='tokens'?compact(summary[key]):numeric(summary[key]);
 }
</script>
<div class="metrics">{#each fields as [key,label]}{@const change=number(comparison.changes?.[key]?.percent??comparison[key])}<section class="metric surface"><div class="metric-heading"><span class="muted">{tr(label)}</span>{#if comparison.label}<small class="metric-comparison" class:positive={change!==null&&change>0} class:negative={change!==null&&change<0} title={comparison.changes?.[key]?.status==='zero_baseline'?tr('无可比较的基数'):comparison.previous_range}><span>{tr(comparison.label)}</span>{#if change!==null&&change!==0}<svg viewBox="0 0 12 12" aria-hidden="true"><path d={change>0?'M6 10V2M2.5 5.5 6 2l3.5 3.5':'M6 2v8M2.5 6.5 6 10l3.5-3.5'} fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>{/if}<span>{change===null?'—':percent(Math.abs(change))}</span></small>{/if}</div><strong data-metric={key} title={value(key)}>{value(key)}</strong>{#if key==='usd'&&(summary.cost_complete===false||Number(summary.unpriced_calls)>0||summary.pricing_status==='partial')}<small class="pricing-note muted">{tr('部分定价')}</small>{/if}</section>{/each}</div>
