<script lang="ts">
 import {rows,text,numeric,priced,percent,number,type Row,type Query} from '../lib/api';
 import {tr} from '../lib/i18n';
 import Metrics from './Metrics.svelte';
 import ModelShare from './ModelShare.svelte';
 import Chart from './Chart.svelte';
 import Filters from './Filters.svelte';
 import Insights from './Insights.svelte';
 import ActivityCalendar from './ActivityCalendar.svelte';
 import ChatRanking from './ChatRanking.svelte';
 export let data:Row={};
 export let query:Query;
 export let onchange:(q:Query)=>void;
 export let settings:Row={};export let onsave:((r:Row)=>Promise<void>)|undefined;export let onerror:((e:any)=>void)|undefined;
 let tab='activity';


 function changeTab(next:string){tab=next;}
 function selectRange(row:Row){
   const raw=String(row.date??row.timestamp??'');let start=String(row.from??raw).slice(0,10),end=String(row.through??raw).slice(0,10);
   const key=(d:Date)=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
   const week=/^(\d{4})-W(\d{2})$/.exec(raw),month=/^(\d{4})-(\d{2})$/.exec(raw);
   if(week){const day=new Date(Number(week[1]),0,4);day.setDate(day.getDate()-(day.getDay()+6)%7+(Number(week[2])-1)*7);start=key(day);day.setDate(day.getDate()+6);end=key(day);}
   else if(month){start=raw+'-01';end=key(new Date(Number(month[1]),Number(month[2]),0));}
   if(!/^\d{4}-\d{2}-\d{2}$/.test(start))return;
   tab='trend';onchange({...query,period:'custom',start,end,page:1});
 }
 function pageChat(direction:number){onchange({...query,page:Number(data.chat_page??1)+direction,page_size:25});}
</script>

<div class="usage-tabs" role="tablist" aria-label={tr('用量')}>
 {#each [['activity','活动'],['trend','趋势'],['chats','聊天排行']] as [key,label]}<button id={`usage-tab-${key}`} role="tab" aria-controls={`usage-panel-${key}`} aria-selected={tab===key} class:selected={tab===key} onclick={()=>changeTab(key)}>{tr(label)}</button>{/each}
</div>
{#if tab==='activity'}
 <div class="usage-section" role="tabpanel" id="usage-panel-activity" aria-labelledby="usage-tab-activity">
  <Insights stats={data.insights?.stats??{}} section="metrics"/>
  <ActivityCalendar data={data.heatmap??data.activity?.rows??[]} onselect={selectRange}/>
  <Insights stats={data.insights?.stats??{}} section="insights"/>
  <button class="ranking-link" onclick={()=>changeTab('chats')}><span>{tr('聊天用量排行')}</span><svg width="17" height="17" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M3 10h13m-5-5 5 5-5 5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
 </div>
{:else if tab==='trend'}
 <div class="usage-section trend-section" role="tabpanel" id="usage-panel-trend" aria-labelledby="usage-tab-trend">
  <div class="trend-filter-row"><Filters {query} models={data.models??[]} {onchange}/><select aria-label={tr('聚合')} value={query.granularity||((query.period==='today'||query.period==='yesterday')?'hour':'day')} onchange={event=>onchange({...query,granularity:event.currentTarget.value,page:1})}><option value="hour">{tr('每小时')}</option><option value="day">{tr('每天')}</option><option value="week">{tr('每周')}</option></select></div>
  <Metrics summary={data.summary??{}}/>
  <Chart data={data.chart??[]} title="" onselect={selectRange}/>
  <ModelShare models={data.models??[]} summary={data.summary??{}}/>
 </div>
{:else}
 <div class="usage-section" role="tabpanel" id="usage-panel-chats" aria-labelledby="usage-tab-chats">
  <ChatRanking {data} {settings} {onsave} {onerror} onpage={pageChat}/>
 </div>
{/if}
<style>
 .usage-tabs{display:flex;gap:28px;border-bottom:1px solid var(--border);min-width:0}.usage-tabs button{padding:0 0 13px;background:transparent;border:0;border-radius:0;color:var(--muted);font-size:14px;position:relative}.usage-tabs button.selected{color:var(--text);font-weight:500}.usage-tabs button.selected::after{content:'';position:absolute;bottom:-1px;left:0;right:0;height:2px;background:var(--text)}.usage-section{display:flex;flex-direction:column;gap:30px;min-width:0}.trend-section{gap:22px}.ranking-link{display:flex;justify-content:space-between;padding:20px 0 0;border:0;border-top:1px solid var(--border);background:transparent;border-radius:0;font-size:14px}.trend-filter-row{display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap}.trend-filter-row :global(.filters){flex:1;min-width:0}.trend-filter-row>select{flex-shrink:0}</style>
