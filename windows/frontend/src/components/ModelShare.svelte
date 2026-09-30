<script lang="ts">
 import {rows,text,numeric,cost,number,percent,compact,type Row} from '../lib/api';import {tr} from '../lib/i18n';
 export let models:Row[]=[];export let summary:Row={};
 let share='usd',visible=5;
 const shares=[['usd','费用'],['tokens','Token'],['user_requests','请求']];
 $: ordered=rows(models).filter(model=>number(model[share])!==null).slice().sort((a,b)=>(number(b[share])??0)-(number(a[share])??0));
 function fraction(model:Row,key:string){const amount=number(model[key]),total=number(summary[key]);return amount===null||total===null?null:total>0?Math.min(1,Math.max(0,amount/total))*100:0;}
 function selectShare(key:string){share=key;visible=5;}
</script>
<section class="surface overview-models"><div class="row between"><h3 class="section-title">{tr('模型占比')}</h3><div class="segments" role="group" aria-label={tr('模型占比')}>{#each shares as [key,label]}<button class:active={share===key} aria-pressed={share===key} onclick={()=>selectShare(key)}>{tr(label)}</button>{/each}</div></div>
 {#each ordered.slice(0,visible) as model}<div class="model-share-row"><div class="row between"><strong>{text(model.model)}</strong><span>{share==='usd'?cost(model.usd):share==='tokens'?compact(model.tokens):numeric(model.user_requests)}</span></div><div class="meter"><div style:width={`${fraction(model,share)??0}%`} style:background="var(--tokens)"></div></div><div class="model-share-percentages">{#each shares as [key,label]}<span class:muted={key!==share}>{tr(label)} {percent(fraction(model,key))}</span>{/each}</div></div>{/each}
 {#if !ordered.length}<span class="muted">{tr('暂无单模型数据')}</span>{/if}{#if visible<ordered.length}<button class="quiet" onclick={()=>visible+=5}>{tr('显示更多')}</button>{/if}
</section>
