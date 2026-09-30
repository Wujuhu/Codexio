<script lang="ts">
 import {number,numeric,rows,type Row} from '../lib/api';
 import {tr} from '../lib/i18n';
 import GlassHover from './GlassHover.svelte';
 export let data:Row[]=[];
 export let onselect:(r:Row)=>void=()=>{};
 let aggregation='day',availableWidth=650;
 let hover:{row:Row,x:number,y:number}|null=null;
 const dateKey=(d:Date)=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
 const localDate=(key:string)=>new Date(key.slice(0,10)+'T00:00:00');
 function calendar(source:Row[]):Row[]{
   if(!source.length)return [];
   const map=new Map(rows(source).map(r=>[String(r.date??r.timestamp).slice(0,10),r]));
   const today=new Date();today.setHours(0,0,0,0);
   return Array.from({length:365},(_,i)=>{const day=new Date(today);day.setDate(day.getDate()-364+i);const key=dateKey(day);return map.get(key)??{date:key,tokens:null,usd:null,user_requests:null,missing:true};});
 }
 function sum(group:Row[],key:string):number|null{const values=group.map(r=>number(r[key]));return values.some(v=>v===null)?null:values.reduce<number>((total,v)=>total+(v??0),0);}
 function skippedTokens(day:Row):number{return Math.max(0,number(day.skipped?.tokens)??0);}
 function tokensComplete(day:Row):boolean{return day.tokens_complete!==false&&skippedTokens(day)===0;}
 function aggregate(days:Row[],mode:string):Row[]{
   if(mode==='day')return days;
   const totals:Row={tokens:0,usd:0,user_requests:0,cost_complete:true,tokens_complete:true};
   let skipped=0;
   if(mode==='cumulative')return days.map(day=>{for(const key of ['tokens','usd','user_requests']){const value=number(day[key]);totals[key]=totals[key]===null||value===null?null:totals[key]+value;}totals.cost_complete=totals.cost_complete&&day.cost_complete!==false;totals.tokens_complete=totals.tokens_complete&&tokensComplete(day);skipped+=skippedTokens(day);return {...day,...totals,skipped:{...day.skipped,tokens:skipped},from:days[0]?.date,cumulative:true};});
   const groups=new Map<string,Row[]>();
   for(const day of days){const monday=localDate(String(day.date));monday.setDate(monday.getDate()-(monday.getDay()+6)%7);const key=dateKey(monday);groups.set(key,[...(groups.get(key)??[]),day]);}
   return [...groups].map(([date,group])=>({date,from:group[0].date,through:group[group.length-1].date,tokens:sum(group,'tokens'),usd:sum(group,'usd'),user_requests:sum(group,'user_requests'),cost_complete:group.every(r=>r.cost_complete!==false),tokens_complete:group.every(tokensComplete),skipped:{tokens:group.reduce((total,day)=>total+skippedTokens(day),0)},weekly:true}));
 }
 $: daily=calendar(rows(data));
 $: buckets=aggregate(daily,aggregation);
 $: {data;aggregation;hover=null;}
 $: rowCount=aggregation==='week'?1:7;
 $: offset=rowCount===1||!buckets.length?0:(localDate(String(buckets[0].date)).getDay()+6)%7;
 $: columns=Math.max(1,Math.ceil((buckets.length+offset)/rowCount));
 $: gap=Math.min(4,Math.max(0,(availableWidth-columns*4)/Math.max(1,columns-1)));
 $: side=Math.min(16,Math.max(0,(availableWidth-(columns-1)*gap)/columns));
 $: maximum=Math.max(1,...buckets.map(r=>number(r.tokens)??0));
 $: months=buckets.map((r,i)=>({i,date:localDate(String(r.date))})).filter(({i,date})=>(i===0&&date.getDate()<=20)||(i>0&&date.getMonth()!==localDate(String(buckets[i-1].date)).getMonth()));
 function measure(node:HTMLElement){const observer=new ResizeObserver(entries=>{const width=entries[0].contentRect.width;if(Math.abs(width-availableWidth)>.5)availableWidth=width;});observer.observe(node);return{destroy:()=>observer.disconnect()};}
 function color(row:Row,max:number){const value=number(row.tokens);return value===null?'transparent':value>0?`color-mix(in srgb, var(--tokens, #007aff) ${(20+80*Math.pow(value/max,.45)).toFixed(2)}%, transparent)`:'color-mix(in srgb, var(--muted) 14%, transparent)';}
 function label(row:Row){const end=localDate(String(row.through??row.date)).toLocaleDateString(undefined,{year:'numeric',month:'short',day:'numeric'});return row.weekly||row.cumulative?`${localDate(String(row.from)).toLocaleDateString(undefined,{month:'short',day:'numeric'})} – ${end}`:end;}
 function show(event:MouseEvent|FocusEvent,row:Row){const bounds=(event.currentTarget as HTMLElement).getBoundingClientRect();hover={row,x:event instanceof MouseEvent?event.clientX:bounds.x+bounds.width/2,y:event instanceof MouseEvent?event.clientY:bounds.bottom};}
</script>

<section class="activity-calendar">
 <div class="row between activity-heading"><h3>{tr('Token 活动')}</h3><div class="aggregation" aria-label={tr('Token 活动')}>{#each [['day','每日'],['week','每周'],['cumulative','累计']] as [key,label]}<button class:selected={aggregation===key} aria-pressed={aggregation===key} onclick={()=>aggregation=key}>{tr(label)}</button>{/each}</div></div>
 <div class="calendar-fit" use:measure>
  {#if !daily.length}<div class="empty small">{tr('暂无数据')}</div>{:else}
  <div class="calendar-grid" style:grid-template-columns={`repeat(${columns}, ${side}px)`} style:grid-template-rows={`repeat(${rowCount}, ${rowCount===1?44:side}px)`} style:gap={`${gap}px`}>
   {#each Array(offset) as _}<span aria-hidden="true"></span>{/each}
   {#each buckets as row}<button class="day" class:unknown={number(row.tokens)===null} aria-label={`${label(row)} · ${numeric(row.tokens)} Token`} style:background={color(row,maximum)} onmouseenter={event=>show(event,row)} onmousemove={event=>show(event,row)} onmouseleave={()=>hover=null} onfocus={event=>show(event,row)} onblur={()=>hover=null} onkeydown={event=>{if(event.key==='Escape')hover=null;}} onclick={()=>{hover=null;onselect(row);}}></button>{/each}
  </div>
  <div class="month-axis">{#each months as month}<span style:left={`${Math.min(Math.floor((month.i+offset)/rowCount)*(side+gap),Math.max(0,availableWidth-28))}px`}>{month.date.toLocaleDateString(undefined,{month:'short'})}</span>{/each}</div>
  {/if}
 </div>
 {#if hover}<GlassHover row={hover.row} title={label(hover.row)} x={hover.x} y={hover.y} onclose={()=>hover=null}/>{/if}
</section>

<style>
 .activity-calendar{min-width:0}.activity-heading{margin:10px 0 20px;gap:14px;flex-wrap:wrap}.activity-heading h3{font-size:16px;font-weight:600;margin:0}.aggregation{display:flex;border-radius:8px;padding:2px;background:var(--surface);border:1px solid var(--border);width:210px}.aggregation button{flex:1;border:0;background:transparent;padding:4px 8px;border-radius:6px;font-size:12px}.aggregation button.selected{background:var(--bg);box-shadow:0 1px 3px #0002}.calendar-fit{width:100%;min-width:0}.calendar-grid{display:grid;grid-auto-flow:column;width:max-content;max-width:100%}.calendar-grid .day{padding:0;border:0;border-radius:3px;min-width:0;min-height:0;width:100%;height:100%}.calendar-grid .day:hover{outline:1px solid var(--text);outline-offset:1px}.calendar-grid .day.unknown{border:1px dashed var(--control)}.month-axis{height:16px;position:relative;margin-top:10px}.month-axis span{position:absolute;top:0;font-size:11px;color:var(--muted);white-space:nowrap}
</style>
