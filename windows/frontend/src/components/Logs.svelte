<script lang="ts">
  import {onDestroy} from 'svelte';
  import {defaultQuery,rows,numeric,compact,priced,type Row,type Query} from '../lib/api';
  import {tr} from '../lib/i18n';
  import {preview,metadata,hitRate,duration} from './logFormat';
  import Filters from './Filters.svelte';
  import DetailPopover from './DetailPopover.svelte';
  import Model from './Model.svelte';
  export let data:Row={}; export let query:Query=defaultQuery(); export let models:Row[]=[]; export let settings:Row={};
  export let onchange:(q:Query)=>void; export let onsave:(r:Row)=>Promise<void>; export let onerror:(e:any)=>void;
  const columns = [{key:'content',title:'请求 / 时间',width:190,min:120},{key:'model',title:'模型',width:180,min:110},{key:'input',title:'输入',width:70,min:56},{key:'output',title:'输出',width:70,min:56},{key:'cache_rate',title:'命中率',width:84,min:66},{key:'cost',title:'费用',width:76,min:66},{key:'duration',title:'耗时',width:86,min:66},{key:'details',title:'详情',width:50,min:46}];
  let widths = columns.map(c=>c.width), available=0;
  let drag:{index:number,start:number,width:number}|null=null;
  let loadedWidths:any;
  $: if (!drag && settings.log_column_widths !== loadedWidths) {
    loadedWidths=settings.log_column_widths;
    const saved = loadedWidths;
    const legacy = Array.isArray(saved) && saved.length===5 ? {content:saved[0],model:saved[1],total:saved[2],cost:saved[3],status:saved[4]} : saved;
    widths = columns.map((c,i)=>{
      const raw = Array.isArray(legacy) ? legacy[i] : legacy?.[c.key];
      const value = raw == null ? NaN : Number(raw);
      return Number.isFinite(value) ? Math.max(c.min,Math.min(650,value)) : c.width;
    });
  }
  $: total=widths.reduce((a,b)=>a+b,0);
  $: rendered=widths.map((w,i)=>w+(!drag&&available>total ? (available-total)*(i===0?.6:i===1?.4:0) : 0));
  function begin(event:PointerEvent,index:number){
    if(event.button!==0)return;
    event.preventDefault();
    widths=[...rendered];
    drag={index,start:event.clientX,width:widths[index]};
  }
  function move(event:PointerEvent){if(drag)widths=widths.map((w,i)=>i===drag!.index?Math.max(columns[i].min,Math.min(650,drag!.width+event.clientX-drag!.start)):w)}
  function save(){void onsave({log_column_widths:Object.fromEntries(columns.map((c,i)=>[c.key,Math.round(widths[i])]))}).catch(onerror)}
  function end(){if(drag){drag=null;save()}}
  function keyResize(event:KeyboardEvent,index:number){
    if(!['ArrowLeft','ArrowRight'].includes(event.key))return;
    event.preventDefault();
    widths=rendered.map((w,i)=>i===index?Math.max(columns[i].min,Math.min(650,w+(event.key==='ArrowLeft'?-10:10))):w);save();
  }
  onDestroy(()=>drag=null);
</script>
<svelte:window onpointermove={move} onpointerup={end} onpointercancel={end} onblur={end}/>
<Filters {query} {models} logs {onchange}/>
<div class="logs-layout"><section class="ledger surface" class:resizing={!!drag}>
  <div class="table-scroll" bind:clientWidth={available}>
    <table style:width={`${Math.max(available,total)}px`}>
      <colgroup>{#each rendered as width}<col style:width={`${width}px`}/>{/each}</colgroup>
      <thead><tr>{#each columns as column,i}<th scope="col">{column.key==='content'?`${tr('请求')} / ${tr('时间')}`:tr(column.title)}<button type="button" class="column-handle" aria-label={`${tr(column.key==='content'?'请求':column.title)} · ${tr('请求详情')==='Request details'?'Resize column':'调整列宽'}`} onpointerdown={event=>begin(event,i)} onkeydown={event=>keyResize(event,i)}></button></th>{/each}</tr></thead>
      <tbody>{#each rows(data.rows) as record (record.id)}<tr>
        <td><div class="request-text" title={preview(record)}>{preview(record)}</div><small class="muted request-meta" title={metadata(record)}>{metadata(record)}</small></td>
        <td><Model {record}/></td><td>{compact(record.input_tokens)}</td><td>{compact(record.output_tokens)}</td><td>{hitRate(record)}</td><td title={priced(record)}>{priced(record)}</td>
        <td>{duration(record.duration_ms)}</td>
        <td><DetailPopover id={String(record.id)} {record} {onerror}/></td>
      </tr>{/each}</tbody>
    </table>
    {#if !rows(data.rows).length}<div class="empty">{tr('暂无数据')}</div>{/if}
  </div>
  <footer class="ledger-footer"><div class="counts">{#each [['requests','用户请求'],['subagents','子代理'],['reviews','自动审批审查'],['compactions','上下文压缩'],['unassigned','未归属调用']] as [key,label]}<span>{tr(label)} {numeric(data.counts?.[key])}</span>{/each}</div><div class="row"><button disabled={query.page<=1} onclick={()=>onchange({...query,page:query.page-1})}>{tr('上一页')}</button><span>{data.page??query.page} / {Math.max(1,data.pages??1)}</span><button disabled={query.page>=Number(data.pages??1)} onclick={()=>onchange({...query,page:query.page+1})}>{tr('下一页')}</button></div></footer>
</section></div>
<style>
  .logs-layout{display:flex;flex:1;min-height:0;min-width:0}.ledger{padding:0;border-radius:18px}.table-scroll{width:100%;min-height:0;overflow:auto}table{table-layout:fixed;min-width:0;border-collapse:collapse;font-size:12px}th,td{text-align:center;position:relative;padding:10px 8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}th{height:36px;background:var(--surface);font-size:12px;font-weight:500;position:sticky;top:0;z-index:1;border-bottom:1px solid var(--border)}th:first-child,th:nth-child(2),th:last-child{min-width:0;width:auto;text-align:center}td{height:52px;border-bottom:1px solid color-mix(in srgb,var(--border) 55%,transparent)}tbody tr:hover{background:transparent}.request-text,.request-meta{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.request-meta{font-size:11px;margin-top:3px;line-height:1.35}.column-handle{position:absolute;right:0;top:0;height:100%;width:8px;min-width:8px;cursor:col-resize;touch-action:none}.column-handle::after{content:'';position:absolute;right:3px;top:10px;bottom:10px;width:1px;background:var(--border)}.resizing{cursor:col-resize;user-select:none}.ledger-footer{padding:10px 14px;font-size:11px;flex-wrap:wrap}.counts{display:flex;gap:10px;flex-wrap:wrap}
</style>
