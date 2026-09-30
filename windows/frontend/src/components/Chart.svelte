<script lang="ts">
 import {compact,number,rows,stamp,type Row} from '../lib/api';
 import {tr} from '../lib/i18n';
 import GlassHover from './GlassHover.svelte';
 export let data:Row[]=[];
 export let title='Token 和费用趋势';
 export let onselect:(r:Row)=>void=()=>{};
 let enabled=['tokens','usd'];
 let hover:{row:Row,index:number,x:number,y:number}|null=null;
 const series=[['tokens','总 Token'],['usd','费用'],['user_requests','用户请求']];
 const colors:Record<string,string>={tokens:'var(--plot-token)',usd:'var(--plot-cost)',user_requests:'var(--output)'};
 function dateOf(r:Row):Date|null {
   const raw=String(r.timestamp??r.date??'');
   const week=/^(\d{4})-W(\d{2})$/.exec(raw);
   if(week){const d=new Date(Number(week[1]),0,4);d.setDate(d.getDate()-(d.getDay()+6)%7+(Number(week[2])-1)*7);return d;}
   const d=new Date(/^\d{4}-\d{2}$/.test(raw)?raw+'-01T00:00:00':/^\d{4}-\d{2}-\d{2}$/.test(raw)?raw+'T00:00:00':raw);
   return Number.isFinite(d.getTime())?d:null;
 }
 // Sparse buckets get explicit unknown gaps; the display never invents zero usage.
 function project(source:Row[]):Row[]{
   const input=rows(source).slice(-800);
   if(input.length<2)return input;
   const unit=input[0].granularity??(String(input[0].timestamp??input[0].date).includes('T')?'hour':'day');
   const output:Row[]=[];
   for(let i=0;i<input.length;i++){
     if(i){const previous=dateOf(input[i-1]),current=dateOf(input[i]);
       if(previous&&current){const next=new Date(previous);if(unit==='hour')next.setHours(next.getHours()+1);else if(unit==='month')next.setMonth(next.getMonth()+1);else next.setDate(next.getDate()+(unit==='week'?7:1));
         if(next.getTime()<current.getTime())output.push({date:next.toISOString(),tokens:null,usd:null,user_requests:null,missing:true});
       }
     }
     output.push(input[i]);
   }
   return output;
 }
 $: items=project(data);
 $: {data;hover=null;}
 function value(r:Row,k:string){return number(k==='usd'?(r.usd??r.cost_usd):r[k]);}
 $: maxima=Object.fromEntries(series.map(([k])=>[k,Math.max(k==='usd'?.01:1,...items.map(r=>Math.max(0,value(r,k)??0)))]));
 $: firstTime=items.length?dateOf(items[0])?.getTime():undefined;
 $: lastTime=items.length?dateOf(items[items.length-1])?.getTime():undefined;
 $: positions=items.map((r,i)=>items.length===1?385:8+(firstTime!==undefined&&lastTime!==undefined&&lastTime>firstTime&&dateOf(r)?(dateOf(r)!.getTime()-firstTime)/(lastTime-firstTime):i/Math.max(1,items.length-1))*754);
 function y(r:Row,k:string){return 162-Math.max(0,value(r,k)??0)/maxima[k]*154;}
 function path(k:string){let connected=false;return items.map((r,i)=>{if(value(r,k)===null){connected=false;return '';}const command=connected?'L':'M';connected=true;return `${command}${positions[i]},${y(r,k)}`;}).join(' ');}
 function isolated(i:number,k:string){return value(items[i],k)!==null&&(i===0||value(items[i-1],k)===null)&&(i===items.length-1||value(items[i+1],k)===null);}
 // Declare projection dependencies explicitly; pointer movement only updates hover.
 let plots:{key:string,d:string,points:{x:number,y:number}[]}[]=[];
 $: {items;positions;maxima;plots=enabled.map(key=>({key,d:path(key),points:items.flatMap((r,i)=>isolated(i,key)?[{x:positions[i],y:y(r,key)}]:[])}));}
 function toggle(k:string){enabled=enabled.includes(k)?enabled.filter(x=>x!==k):[...enabled,k];}
 function label(r:Row,axis=false){const d=dateOf(r);if(!d)return String(r.label??'—');const hourly=r.granularity==='hour'||String(r.timestamp??r.date).includes('T');return axis?d.toLocaleDateString(undefined,{month:'numeric',day:'numeric'})+(hourly?' '+d.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}):''):hourly?stamp(d.getTime()):r.granularity==='week'?String(r.label??r.date):d.toLocaleDateString(undefined,{year:'numeric',month:'short',day:'numeric'});}
 function selectPoint(event:MouseEvent|FocusEvent,r:Row,index:number){const rect=(event.currentTarget as SVGElement).getBoundingClientRect();hover={row:r,index,x:event instanceof MouseEvent?event.clientX:rect.x+rect.width/2,y:event instanceof MouseEvent?event.clientY:rect.y+24};}
 function axisCost(v:number){return '$'+v.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:v<.01?4:2});}
</script>

<section class="surface chart-panel">
 <div class="row between chart-heading"><h3>{tr(title)}</h3><div class="row plot-legend">{#each series as [key,label]}<button class="quiet" class:chosen={enabled.includes(key)} aria-pressed={enabled.includes(key)} onclick={()=>toggle(key)}><i style:background={colors[key]}></i>{tr(label)}</button>{/each}</div></div>
 {#if !items.length}<div class="empty small">{tr('暂无数据')}</div>{:else}
 <div class="plot-scale"><span>{compact(maxima.tokens)} Token</span><span>{axisCost(maxima.usd)}</span></div>
 <svg class="usage-plot" viewBox="0 0 770 170" preserveAspectRatio="none" role="img" aria-label={tr(title)}>
  <path d="M8 8H762 M8 59.3H762 M8 110.7H762 M8 162H762" stroke="var(--grid)" fill="none" stroke-width="0.5" vector-effect="non-scaling-stroke"/>
  {#each plots as plot}<path d={plot.d} fill="none" stroke={colors[plot.key]} stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>{#each plot.points as point}<circle cx={point.x} cy={point.y} r={plot.key==='tokens'?3.5:2.5} fill={colors[plot.key]}/>{/each}{/each}
  {#if hover}<line x1={positions[hover.index]} x2={positions[hover.index]} y1="8" y2="162" stroke="var(--muted)" stroke-opacity=".4" pointer-events="none"/>{/if}
  {#each items as r,i}{#if !r.missing}<rect x={i===0?0:(positions[i-1]+positions[i])/2} y="0" width={(i===items.length-1?770:(positions[i]+positions[i+1])/2)-(i===0?0:(positions[i-1]+positions[i])/2)} height="170" fill="transparent" role="button" tabindex="0" aria-label={label(r)} onmousemove={event=>selectPoint(event,r,i)} onmouseenter={event=>selectPoint(event,r,i)} onmouseleave={()=>hover=null} onfocus={event=>selectPoint(event,r,i)} onblur={()=>hover=null} onclick={()=>onselect(r)} onkeydown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();onselect(r);}if(event.key==='Escape')hover=null;}}/>{/if}{/each}
 </svg>
 <div class="plot-dates"><span>{label(items[0],true)}</span><span>{items.length>1?label(items[items.length-1],true):''}</span></div>
 {#if hover}<GlassHover row={hover.row} title={label(hover.row)} x={hover.x} y={hover.y} onclose={()=>hover=null}/>{/if}
 {/if}
</section>

<style>
 .chart-panel{--plot-token:var(--tokens,#007aff);--plot-cost:var(--cost,#34c759);padding:18px;border:1px solid var(--border);border-radius:14px;background:transparent;min-width:0}
 .chart-heading{gap:12px;flex-wrap:wrap;margin-bottom:12px}.chart-heading h3{margin:0;font-size:14px;font-weight:500}.plot-legend{gap:8px;flex-wrap:wrap}.plot-legend button{font-size:11px;padding:3px 5px;opacity:.5}.plot-legend button.chosen{opacity:1}.plot-legend i{width:7px;height:7px;border-radius:50%}.usage-plot{display:block;width:100%;height:170px;overflow:visible}.plot-scale,.plot-dates{display:flex;justify-content:space-between;font-size:11px;gap:12px}.plot-scale{margin-bottom:10px}.plot-scale span:first-child{color:var(--plot-token)}.plot-scale span:last-child{color:var(--plot-cost)}.plot-dates{margin-top:10px;color:var(--muted)}
</style>
