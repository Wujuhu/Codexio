<script lang="ts">
  import {rows,text,type Row} from '../lib/api';
  import {modelName,effort} from './logFormat';
  export let record:Row={};
  export let align:'left'|'center'|'right'='center';
  export let showEffort=true;
  export let showUpstream=true;
  $: observed=rows(record.upstream_observations??record.observations);
  $: upstream=!showUpstream ? [] : observed.length
    ? observed.map(row=>text(row.upstream_model??row.response_model??row.model,'')).filter(Boolean)
    : Array.isArray(record.upstream_models) ? record.upstream_models.map((value:any)=>text(value,'')).filter(Boolean) : record.upstream_model ? [String(record.upstream_model)] : [];
  $: requested=text(record.model??record.models?.join(', '));
  $: mismatch=record.upstream_mismatched_calls!=null ? Number(record.upstream_mismatched_calls)>0 : record.upstream_mismatched!=null ? !!record.upstream_mismatched : !['—','unknown','mixed'].includes(requested)&&upstream.some((model:string)=>model!==requested);
  $: upstreamLabel=[...new Set(upstream)].map(modelName).join(' · ');
  $: effortLabel=showEffort?effort(record.reasoning_effort):'';
</script>
<div class="model-lines" class:has-upstream={upstream.length>0} style:--model-alignment={align}>
  {#if upstream.length}<div class="observed" class:mismatch title={upstreamLabel}><small>{upstreamLabel}</small></div>{/if}
  <span class="requested" title={[modelName(requested),effortLabel].filter(Boolean).join(' · ')}><span class="requested-name">{modelName(requested)}</span>{#if effortLabel}<small class="effort">{effortLabel}</small>{/if}</span>
  {#if upstream.length}<svg class:mismatch viewBox="0 0 24 30" width="18" height="25" role="img" aria-label="Observed upstream model"><path d="M3 25h15V6H8m4-4-4 4 4 4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round"/></svg>{/if}
</div>
<style>
  .model-lines{display:flex;position:relative;flex-direction:column;align-items:stretch;justify-content:center;gap:2px;width:100%;min-width:0;font-size:11px;line-height:1.35;padding:0}.model-lines.has-upstream{padding-right:19px}.model-lines .observed,.model-lines .requested{display:block;width:100%;max-width:100%;min-width:0;text-align:var(--model-alignment,center);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.observed{color:var(--muted)}.observed small{font-size:10px;line-height:1.35}.model-lines svg{position:absolute;right:0;top:50%;transform:translateY(-50%);margin:0;color:var(--muted)}.observed.mismatch,.model-lines svg.mismatch{color:var(--success)}
  .model-lines .requested{display:flex;align-items:baseline;justify-content:var(--model-alignment,center);gap:5px}.requested-name{min-width:0;overflow:hidden;text-overflow:ellipsis}.effort{flex:none;font-size:10px;font-weight:400;color:var(--muted)}
</style>
