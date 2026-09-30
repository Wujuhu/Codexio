<script lang="ts">
  import {onDestroy} from 'svelte';
  import {defaultQuery,rows,numeric,compact,priced,cost,text,type Row,type Query} from '../lib/api';
  import {tr} from '../lib/i18n';
  import {preview,metadata,hitRate,duration,effort,speed,logTime} from './logFormat';
  import Filters from './Filters.svelte';
  import DetailPopover from './DetailPopover.svelte';
  import Model from './Model.svelte';
  export let data:Row={}; export let query:Query=defaultQuery(); export let models:Row[]=[]; export let settings:Row={};
  export let onchange:(q:Query)=>void; export let onsave:(r:Row)=>Promise<void>; export let onerror:(e:any)=>void;
  const definitions=[['content','请求 / 时间',190],['time','时间',118],['model','模型',180],['input','输入',70],['output','输出',70],['total','总 Token',70],['cached','缓存读取',70],['cache_write','缓存写入',70],['cache_rate','命中率',84],['cost','费用',76],['duration','耗时',86],['effort','推理强度',70],['speed','速度',70],['context','上下文',70],['status','状态',70],['details','详情',50]].map(([key,title,width])=>({key:String(key),title:String(title),width:Number(width)}));
  const defaults=['content','model','input','output','cache_rate','cost','duration','details'];
  let widths:Record<string,number>={},available=0,menu:HTMLDetailsElement;
  let drag:{key:string,start:number,width:number}|null=null;
  let loadedWidths:any;
  let savedWidths=new Set<string>();
  $: columns=definitions.filter(c=>c.key==='content'||c.key==='details'||(Array.isArray(settings.native_log_columns)?settings.native_log_columns:defaults).includes(c.key));
  $: if(!drag&&settings.log_column_widths!==loadedWidths){
    loadedWidths=settings.log_column_widths;
    const legacy=Array.isArray(loadedWidths)&&loadedWidths.length===5?{content:loadedWidths[0],model:loadedWidths[1],total:loadedWidths[2],cost:loadedWidths[3]}:loadedWidths;
    savedWidths=new Set(definitions.filter((c,i)=>Number.isFinite(Number(Array.isArray(legacy)?legacy[i]:legacy?.[c.key]))).map(c=>c.key));
    widths=Object.fromEntries(definitions.map((c,i)=>{const n=Number(Array.isArray(legacy)?legacy[i]:legacy?.[c.key]);return[c.key,Number.isFinite(n)?Math.max(48,Math.min(1200,n)):c.width]}));
  }
  $: total=columns.reduce((n,c)=>n+(widths[c.key]??c.width),0);
  // CompactTable.fit shares spare width equally across unsaved flexible columns.
  $: flexible=columns.filter(c=>!savedWidths.has(c.key)&&['content','model'].includes(c.key));
  $: rendered=columns.map(c=>{const width=widths[c.key]??c.width;const extra=!drag&&flexible.length?Math.max(0,available-total)/flexible.length:0;return width+(!savedWidths.has(c.key)?c.key==='content'?Math.min(95,extra):c.key==='model'?Math.min(50,extra):0:0)});
  $: tableWidth=rendered.reduce((a,b)=>a+b,0);
  function toggle(key:string,enabled:boolean){const selected=new Set(columns.map(c=>c.key));enabled?selected.add(key):selected.delete(key);void onsave({native_log_columns:definitions.filter(c=>selected.has(c.key)).map(c=>c.key)}).catch(onerror)}
  function begin(event:PointerEvent,index:number){if(event.button!==0)return;event.preventDefault();widths={...widths,...Object.fromEntries(columns.map((c,i)=>[c.key,rendered[i]]))};drag={key:columns[index].key,start:event.clientX,width:rendered[index]}}
  function move(event:PointerEvent){if(drag)widths={...widths,[drag.key]:Math.max(48,Math.min(1200,drag.width+event.clientX-drag.start))}}
  function persistWidths(){void onsave({log_column_widths:Object.fromEntries(Object.entries(widths).map(([key,width])=>[key,Math.round(width)]))}).catch(onerror)}
  function end(){if(drag){drag=null;persistWidths()}}
  function keyResize(event:KeyboardEvent,index:number){if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();widths={...widths,[columns[index].key]:Math.max(48,Math.min(1200,rendered[index]+(event.key==='ArrowLeft'?-10:10)))};persistWidths()}
  function cell(record:Row,key:string):string{switch(key){case'time':return logTime(record.timestamp,query.period==='today');case'input':return compact(record.input_tokens);case'output':return compact(record.output_tokens);case'total':return compact(record.total_tokens);case'cached':return compact(record.cached_input_tokens);case'cache_write':return compact(record.cache_write_input_tokens);case'cache_rate':return hitRate(record);case'cost':return cost(record.cost_usd??record.usd);case'duration':return duration(record.duration_ms);case'effort':return effort(record.reasoning_effort)||'—';case'speed':return speed(record.service_tier)?'Fast':['default','standard',''].includes(String(record.service_tier??''))?tr('标准'):tr('未知');case'context':return compact(record.model_context_window);case'status':return tr(({running:'进行中',completed:'已完成',aborted:'已中止',unknown:'未知'} as Record<string,string>)[record.status]??'未知');default:return text(record[key])}}
  function closeMenu(event:PointerEvent){if(menu?.open&&!menu.contains(event.target as Node))menu.open=false}
  onDestroy(()=>drag=null);
</script>
<svelte:window onpointermove={move} onpointerup={end} onpointercancel={end} onblur={end} onpointerdown={closeMenu}/>
<Filters {query} {models} logs {onchange}><details class="column-picker" bind:this={menu}><summary>{tr('显示字段')}</summary><div class="column-menu">{#each definitions.filter(c=>!['content','details'].includes(c.key)) as column}<label><input type="checkbox" checked={columns.some(c=>c.key===column.key)} onchange={event=>toggle(column.key,event.currentTarget.checked)}/>{tr(column.title)}</label>{/each}<button class="quiet" onclick={()=>onsave({native_log_columns:defaults}).catch(onerror)}>{tr('恢复默认字段')}</button></div></details></Filters>
<div class="logs-layout"><section class="ledger surface" class:resizing={!!drag}>
  <div class="table-scroll" bind:clientWidth={available}>
    <table style:width={`${tableWidth}px`} style:min-width={`${tableWidth}px`}>
      <colgroup>{#each rendered as width}<col style:width={`${width}px`}/>{/each}</colgroup>
      <thead><tr>{#each columns as column,i}<th scope="col">{column.key==='content'?`${tr('请求')} / ${tr('时间')}`:tr(column.title)}<button type="button" class="column-handle" aria-label={`${tr(column.key==='content'?'请求':column.title)} · ${tr('请求详情')==='Request details'?'Resize column':'调整列宽'}`} onpointerdown={event=>begin(event,i)} onkeydown={event=>keyResize(event,i)}></button></th>{/each}</tr></thead>
      <tbody>{#each rows(data.rows) as record (record.id)}<tr>{#each columns as column}<td>{#if column.key==='content'}<div class="request-text" title={preview(record)}>{preview(record)}</div><small class="muted request-meta" title={metadata(record)}>{metadata(record,query.period==='today')}</small>{:else if column.key==='model'}<Model {record}/>{:else if column.key==='details'}<DetailPopover id={String(record.id)} {record} {onerror}/>{:else}<span title={column.key==='cost'?priced(record):cell(record,column.key)}>{cell(record,column.key)}</span>{/if}</td>{/each}</tr>{/each}</tbody>
    </table>
    {#if !rows(data.rows).length}<div class="empty">{tr('暂无数据')}</div>{/if}
  </div>
</section>
  <footer class="ledger-footer"><div class="counts">{#each [['requests','用户请求'],['subagents','子代理'],['reviews','自动审批审查'],['compactions','上下文压缩'],['unassigned','未归属调用']] as [key,label]}{#if key==='requests'||Number(data.counts?.[key])>0}<span>{tr(label)} {numeric(data.counts?.[key])}</span>{/if}{/each}</div><div class="row"><button disabled={query.page<=1} onclick={()=>onchange({...query,page:query.page-1})} aria-label={tr('上一页')}>‹</button><span>{data.page??query.page} / {Math.max(1,data.pages??1)}</span><button disabled={query.page>=Number(data.pages??1)} onclick={()=>onchange({...query,page:query.page+1})} aria-label={tr('下一页')}>›</button></div></footer>
</div>
<style>
  .logs-layout{display:flex;flex-direction:column;gap:14px;flex:1;min-height:0;min-width:0}.ledger{padding:0;border-radius:10px}.table-scroll{width:100%;min-height:0;overflow:auto}table{table-layout:fixed;min-width:0;border-collapse:collapse;font-size:12px}th,td{text-align:center;position:relative;padding:5px 8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}th{height:42px;background:var(--surface);font-size:11px;font-weight:500;position:sticky;top:0;z-index:1;border-bottom:1px solid var(--border)}th:first-child,th:nth-child(2),th:last-child{min-width:0;width:auto;text-align:center}td{height:45px;border-bottom:1px solid color-mix(in srgb,var(--border) 55%,transparent)}tbody tr:hover{background:transparent}.request-text,.request-meta{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.request-text{font-weight:400;line-height:18px}.request-meta{font-size:10px;margin-top:0;line-height:14px}.column-handle{position:absolute;right:0;top:0;height:100%;width:8px;min-width:8px;cursor:col-resize;touch-action:none}.column-handle::after{content:'';position:absolute;right:3px;top:8px;bottom:8px;width:1px;background:var(--border)}.resizing{cursor:col-resize;user-select:none}.ledger-footer{padding:0;border:0;font-size:12px;flex-wrap:wrap;flex-shrink:0}.counts{display:flex;gap:10px;flex-wrap:wrap}.column-picker{position:relative;flex-shrink:0;font-size:11px}.column-picker>summary{list-style:none;cursor:pointer;border:1px solid var(--control);border-radius:6px;padding:4px 9px;min-height:26px;background:var(--control-bg)}.column-picker>summary::after{content:'⌄';margin-left:8px}.column-menu{position:absolute;right:0;top:31px;z-index:10;background:var(--control-bg);border:1px solid var(--border);border-radius:9px;box-shadow:0 5px 20px #0002;padding:6px;min-width:158px}.column-menu label{display:flex;align-items:center;gap:7px;padding:4px 6px;cursor:pointer}.column-menu label:hover{background:var(--hover)}.column-menu input{width:14px;height:14px}.column-menu button{width:100%;border-top:1px solid var(--border);border-radius:0;margin-top:4px;font-size:11px}
</style>
