<script lang="ts">
 import {numeric,percent,priced,number,type Row} from '../lib/api';
 import {tr} from '../lib/i18n';
 export let summary:Row={};
 export let comparison:Row={};
 const fields=[['usd','费用'],['tokens','总 Token'],['user_requests','用户请求'],['cache_hit_rate','命中率']];
 function value(key:string){
   if(key==='usd')return priced(summary,'usd');
   if(key==='cache_hit_rate'){
     const rate=number(summary[key]);
     return percent(rate===null?null:rate*(rate<=1?100:1));
   }
   return numeric(summary[key]);
 }
</script>
<div class="metrics">{#each fields as [key,label]}{@const change=comparison.changes?.[key]?.percent??comparison[key]}<section class="metric surface"><span class="muted">{tr(label)}</span><strong data-metric={key}>{value(key)}</strong>{#if change!==undefined}<small class:positive={Number(change)>=0}>{Number(change)>0?'+':''}{percent(change)} {tr(comparison.label??'')}</small>{/if}</section>{/each}</div>
