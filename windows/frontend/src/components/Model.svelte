<script lang="ts">
  import {rows,text,type Row} from '../lib/api';
  import {modelName,effort} from './logFormat';
  export let record:Row={};
  export let align:'left'|'center'|'right'='center';
  export let showEffort=true;
  export let showUpstream=true;
  export let tableCell=false;
  function columnLabels(record:Row){
    // Match Mac's claudeGridRow and LogFields.row without rewriting ledger IDs.
    const requested=typeof record.model==='string'?record.model:'';
    const upstream=typeof record.upstream_model==='string'?record.upstream_model:'';
    if(record.client_kind==='claude_code'){
      const models=Array.isArray(record.upstream_models)&&record.upstream_models.every((value:unknown)=>typeof value==='string')?record.upstream_models as string[]:[];
      const returned=[...new Set([...models,upstream].filter(Boolean))].sort();
      return{requested:returned.join(' · ')||requested||'—',upstream:'',mismatch:false};
    }
    const format=(value:string)=>value.replaceAll('gpt-','GPT-').replaceAll('-astra',' Astra').replaceAll('-sol',' Sol').replaceAll('-luna',' Luna').replaceAll('-terra',' Terra');
    const label=format(requested).trim();
    const models=Array.isArray(record.upstream_models)?record.upstream_models.filter((value:unknown):value is string=>typeof value==='string'&&!!value):[];
    const upstreams=[...new Set(models.length?models:upstream?[upstream]:[])].sort();
    return{
      requested:['','unknown','auto','未知','-','–','—'].includes(label.toLowerCase())?'—':label,
      upstream:upstreams.map(format).join(' + '),
      mismatch:typeof record.upstream_mismatched==='boolean'?record.upstream_mismatched:record.upstream_mismatched_calls!=null?Number(record.upstream_mismatched_calls)>0:!!requested&&upstreams.some(model=>model!==requested)
    };
  }
  $: column=tableCell?columnLabels(record):null;
  $: observed=tableCell?[]:rows(record.upstream_observations??record.observations);
  $: upstream=tableCell||!showUpstream ? [] : observed.length
    ? observed.map(row=>text(row.upstream_model??row.response_model??row.model,'')).filter(Boolean)
    : Array.isArray(record.upstream_models) ? record.upstream_models.map((value:any)=>text(value,'')).filter(Boolean) : record.upstream_model ? [String(record.upstream_model)] : [];
  $: requested=text(record.model??record.models?.join(', '));
  $: requestedLabel=column?.requested??modelName(requested);
  $: mismatch=column?.mismatch??(record.upstream_mismatched_calls!=null ? Number(record.upstream_mismatched_calls)>0 : record.upstream_mismatched!=null ? !!record.upstream_mismatched : !['—','unknown','mixed'].includes(requested)&&upstream.some((model:string)=>model!==requested));
  $: upstreamLabel=column?.upstream??[...new Set(upstream)].map(modelName).join(' · ');
  $: hasUpstream=!!upstreamLabel;
  $: effortLabel=showEffort?effort(record.reasoning_effort):'';
</script>
<div class="model-lines" class:has-upstream={hasUpstream} style:--model-alignment={align}>
  {#if hasUpstream}<div class="observed" class:mismatch title={upstreamLabel}><small>{upstreamLabel}</small></div>{/if}
  <span class="requested" title={[requestedLabel,effortLabel].filter(Boolean).join(' · ')}><span class="requested-name">{requestedLabel}</span>{#if effortLabel}<small class="effort">{effortLabel}</small>{/if}</span>
  {#if hasUpstream}<svg class:mismatch viewBox="0 0 24 30" width="18" height="25" role="img" aria-label="Observed upstream model"><path d="M3 25h15V6H8m4-4-4 4 4 4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round"/></svg>{/if}
</div>
<style>
  .model-lines{display:flex;position:relative;flex-direction:column;align-items:stretch;justify-content:center;gap:2px;width:100%;min-width:0;font-size:11px;line-height:1.35;padding:0}.model-lines.has-upstream{padding-right:19px}.model-lines .observed,.model-lines .requested{display:block;width:100%;max-width:100%;min-width:0;text-align:var(--model-alignment,center);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.observed{color:var(--muted)}.observed small{font-size:10px;line-height:1.35}.model-lines svg{position:absolute;right:0;top:50%;transform:translateY(-50%);margin:0;color:var(--muted)}.observed.mismatch,.model-lines svg.mismatch{color:var(--success)}
  .model-lines .requested{display:flex;align-items:baseline;justify-content:var(--model-alignment,center);gap:5px}.requested-name{min-width:0;overflow:hidden;text-overflow:ellipsis}.effort{flex:none;font-size:10px;font-weight:400;color:var(--muted)}
</style>
