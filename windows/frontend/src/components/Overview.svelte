<script lang="ts">
 import {rows,text,numeric,priced,cost,number,percent,compact,type Row,type Query} from '../lib/api';
 import {tr} from '../lib/i18n';
 import Quota from './Quota.svelte';import Metrics from './Metrics.svelte';import Chart from './Chart.svelte';import Filters from './Filters.svelte';import Model from './Model.svelte';import DetailPopover from './DetailPopover.svelte';import ModelShare from './ModelShare.svelte';import {preview,metadata,duration} from './logFormat';
 export let data:Row={};export let query:Query;export let onchange:(q:Query)=>void;export let onerror:(e:any)=>void;export let onlogs:()=>void=()=>{};
 $: summary=data.summary??{};
 $: recent=rows(data.recent).filter(row=>!['automatic_approval_review','context_compaction'].includes(row.record_kind)).slice(0,3);
</script>
<Quota quota={data.quota??{}}/><hr class="page-divider"/><div class="row between wrap"><h3 class="section-title">{tr('用量')}</h3><Filters {query} periodOnly {onchange}/></div><Metrics {summary}/><Chart data={data.chart??[]}/>
<ModelShare models={data.models??[]} {summary}/>
<div class="row recent-heading"><h3 class="section-title">{tr('最近请求')}</h3><button class="quiet muted" onclick={onlogs}>{tr('查看全部')}</button></div>
{#if recent.length}<section class="recent recent-table"><div class="recent-columns"><span>{tr('请求')} / {tr('时间')}</span><span>{tr('模型')}</span><span>{tr('总 Token')}</span><span>{tr('费用')}</span><span>{tr('耗时')}</span><span>{tr('详情')}</span></div>{#each recent as record}<div class="recent-item"><div class="recent-prompt"><strong title={preview(record)}>{preview(record)}</strong><small title={metadata(record,true)}>{metadata(record,true)}</small></div><Model {record}/><span>{compact(record.total_tokens)}</span><span title={priced(record)}>{cost(record.cost_usd??record.usd)}</span><span>{duration(record.duration_ms)}</span><DetailPopover id={String(record.id)} {record} {onerror}/></div>{/each}</section>{:else}<div class="empty">{tr('暂无请求')}</div>{/if}
