<script lang="ts">
 import {compact,numeric,percent,cost,number,type Row} from '../lib/api';
 import {tr} from '../lib/i18n';
 export let summary:Row={};
 const fields=[['usd','费用'],['tokens','总 Token'],['user_requests','用户请求'],['cache_hit_rate','命中率']];
 function value(source:Row,key:string){
   if(key==='usd')return cost(source.usd);
   if(key==='cache_hit_rate'){
     const rate=number(source[key]);
     return percent(rate===null?null:rate*(rate<=1?100:1));
   }
   return key==='tokens'?compact(source[key]):numeric(source[key]);
 }
</script>
<div class="metrics">{#each fields as [key,label]}<section class="metric surface"><span class="muted metric-heading">{tr(label)}</span><strong data-metric={key} title={value(summary,key)}>{value(summary,key)}</strong></section>{/each}</div>
