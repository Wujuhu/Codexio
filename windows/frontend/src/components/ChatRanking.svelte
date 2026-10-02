<script lang="ts">
 import{onDestroy}from'svelte';import{api,rows,text,number,compact,utcReportStamp,type Row}from'../lib/api';
 import{tr}from'../lib/i18n';
 export let data:Row={};export let settings:Row={};export let onsave:((r:Row)=>Promise<void>)|undefined;export let onerror:((e:any)=>void)|undefined;export let onpage:((direction:number)=>void)|undefined;
 let local=false,sort='weekly_limit_percent',page=0,visible=5;let expanded=new Set<string>();let ranking:Row={},sequence=0;
 $: if(!local)void load(data.chat_usage,sort,page);
 $: candidates=local?rows(data.chats):rows(ranking.threads);
 $: pages=local?Math.max(1,Number(data.chat_pages??1)):Math.max(1,Number(ranking.pages??1));
 $: page=Math.min(page,pages-1);
 $: shown=candidates.slice(0,visible);
 $: void settings;
 $: void onsave;
 onDestroy(()=>sequence++);
 async function load(_source:Row,metric:string,current:number){const ticket=++sequence;try{const result=await api('GetChatRanking',metric,current+1);if(ticket===sequence)ranking=result}catch(e){if(ticket===sequence)onerror?.(e)}}
 function toggleMode(){local=!local;page=0;visible=5;expanded=new Set();sequence++;}
 function changeSort(next:string){sort=next;page=0;visible=5;expanded=new Set();}
 function changePage(direction:number){visible=5;expanded=new Set();if(local)onpage?.(direction);else page+=direction;}
 function toggle(id:string){const next=new Set(expanded);next.has(id)?next.delete(id):next.add(id);expanded=next;}
 function precise(value:any){const n=number(value);return n===null?'—':n.toLocaleString('en-US',{maximumFractionDigits:n>0&&n<.01?4:2})+'%'}
 function creditsText(value:any){const n=number(value);return n===null?'—':n.toLocaleString('en-US',{maximumFractionDigits:2})}
 function breakdown(row:Row,key:string){const total=number(row[sort]);const amounts=new Map<string,number>();for(const group of rows(row.groups)){const n=number(group[sort]);if(n!==null){const label=text(group[key],tr('未知'));amounts.set(label,(amounts.get(label)??0)+n)}}const result=Array.from(amounts,([label,value])=>({label,value})).sort((a,b)=>b.value-a.value);const rest=(total??0)-result.reduce((sum,item)=>sum+item.value,0);if(total!==null&&rest>Math.max(.000001,total*.0001))result.push({label:tr('未归类'),value:rest});return total!==null&&total>0?result.map(item=>({...item,percent:item.value/total*100})):[]}
 function groupLabel(key:string,value:string){if(key==='reasoning_effort')return({minimal:'Minimal',low:'Low',medium:'Medium',high:'High',max:'Max'} as Record<string,string>)[value]??value;if(key==='speed')return['priority','fast'].includes(value)?tr('快速模式'):['standard','default'].includes(value)?tr('标准'):value;return value}
 async function open(id:string){try{await api('OpenChat',id)}catch(e){onerror?.(e)}}
 async function refresh(){try{await api('RefreshAccountReports')}catch(e){onerror?.(e)}}
</script>

<section class="chat-ranking">
 <div class="ranking-heading"><div><h3>{tr(local?'本机 Token 排行':'聊天用量排行')}</h3>{#if !local}<p class="muted">{tr('比较每个聊天的套餐和额度用量')}</p>{/if}</div><button class="quiet" onclick={toggleMode}>{tr(local?'查看额度排行':'本机 Token')}</button></div>
 {#if ranking.error&&!local}<p class="ranking-note">{ranking.error}</p>{/if}
 {#if !shown.length}<div class="ranking-empty">{tr(!local&&ranking.loading?'正在读取':'暂不可用')}<small>{tr(local?'暂无本机聊天记录':'当前账户尚未提供这项明细')}</small>{#if !local}<button onclick={refresh}>{tr('刷新明细')}</button>{/if}</div>
 {:else}<div class="ranking-list" class:local>
  <div class="ranking-header"><span>{tr('聊天')}</span>{#if !local}<button class="sort-column" onclick={()=>changeSort('weekly_limit_percent')}>{tr('占每周限额的 %')}</button>{/if}<button class="sort-column" onclick={()=>changeSort('balance_usage_credits')}>{tr(local?'Token':'已用额度')}</button></div>
  {#each shown as row}{@const id=String(row.thread_id??row.session_id)}{@const openRow=expanded.has(id)}<article class:expanded={openRow} class="ranking-entry"><button class="ranking-row" onclick={()=>toggle(id)}><span class="chat-name"><span class="chevron">{openRow?'⌄':'›'}</span><span>{text(row.title??row.name??row.session_title??row.thread_id??row.session_id)}{#if row.data_status==='partial'}<small>{tr('部分数据')}</small>{/if}</span></span>{#if !local}<span>{precise(row.weekly_limit_percent)}</span>{/if}<span>{local?compact(row.local_tokens??row.tokens??row.total_tokens):creditsText(row.balance_usage_credits)}</span></button>
   {#if openRow}<div class="ranking-details">{#if !local}{#each [['model','模型'],['reasoning_effort','推理强度'],['speed','速度']] as [key,label]}<div class="breakdown"><span>{tr(label)}</span><div>{#each breakdown(row,key) as group}<span>{groupLabel(key,group.label)} <small>{precise(group.percent)}</small></span>{:else}<span>—</span>{/each}</div></div>{/each}{/if}<button class="quiet open-chat" onclick={()=>open(id)}>{tr('打开聊天 ↗')}</button></div>{/if}
  </article>{/each}
 </div><div class="ranking-footer">{#if visible<Math.min(25,candidates.length)}<button onclick={()=>visible=Math.min(25,visible+5)}>{tr('显示更多')}</button>{/if}<small>{!local?tr('统计截至')+' '+utcReportStamp(ranking.data_as_of):''}</small><div><button aria-label={tr('上一页')} disabled={local?Number(data.chat_page??1)<=1:!page} onclick={()=>changePage(-1)}>‹</button><span>{local?data.chat_page??1:page+1} / {pages}</span><button aria-label={tr('下一页')} disabled={local?Number(data.chat_page??1)>=pages:page+1>=pages} onclick={()=>changePage(1)}>›</button></div></div>{/if}
</section>

<style>
 .chat-ranking{min-width:0;display:flex;flex-direction:column;gap:16px}.ranking-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.ranking-heading h3{font-size:18px;margin:0 0 4px}.ranking-heading p,.ranking-note,.ranking-footer small{font-size:11px}.ranking-list{border:1px solid var(--border);border-radius:18px;overflow:hidden}.ranking-header,.ranking-row{display:grid;grid-template-columns:minmax(260px,1fr) minmax(130px,.42fr) minmax(100px,.28fr);align-items:center}.ranking-list.local .ranking-header,.ranking-list.local .ranking-row{grid-template-columns:minmax(260px,1fr) minmax(120px,.3fr)}.ranking-header{min-height:39px;padding:0 16px;color:var(--muted);font-size:12px}.ranking-header>span:first-child{padding-left:26px}.sort-column{border:0;background:transparent;color:inherit;min-height:28px;padding:0}.ranking-entry{border-top:1px solid var(--border)}.ranking-entry.expanded{background:color-mix(in srgb,var(--text) 2.5%,transparent)}.ranking-row{width:100%;min-height:58px;padding:0 16px;border:0;border-radius:0;background:transparent;font-size:14px;font-variant-numeric:tabular-nums}.ranking-row>span:not(.chat-name){text-align:right}.chat-name{display:flex;align-items:center;text-align:left;gap:7px;min-width:0}.chat-name>span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.chevron{width:18px;flex:none;color:var(--muted)}.chat-name small{display:block;color:var(--muted);font-size:10px}.ranking-details{padding:2px 52px 22px;display:flex;flex-direction:column;gap:14px;font-size:13px}.breakdown{display:grid;grid-template-columns:80px 1fr;gap:16px;align-items:start}.breakdown>span{color:var(--muted)}.breakdown>div{display:flex;flex-wrap:wrap;gap:7px 24px}.breakdown small{color:var(--muted);font-size:12px}.open-chat{align-self:flex-start;padding:2px 0!important}.ranking-footer{display:flex;align-items:center;gap:12px;font-size:10px}.ranking-footer small{flex:1;color:var(--muted)}.ranking-footer>div{display:flex;align-items:center;gap:10px}.ranking-footer button{font-size:11px;min-height:24px}.ranking-empty{padding:18px 0;display:flex;flex-direction:column;align-items:flex-start;gap:10px;font-size:13px}.ranking-empty small{color:var(--muted);font-size:11px}@media(max-width:680px){.ranking-list{overflow-x:auto}.ranking-header,.ranking-row{min-width:570px}.ranking-list.local .ranking-header,.ranking-list.local .ranking-row{min-width:430px}}
</style>
