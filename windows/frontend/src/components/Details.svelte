<script lang="ts">
  import {onDestroy} from 'svelte';
  import {api,rows,text,kind,numeric,compact,priced,stamp,type Row} from '../lib/api';
  import {tr} from '../lib/i18n';
  import {effort,speed,preview,duration} from './logFormat';
  import Markdown from './Markdown.svelte';
  import Model from './Model.svelte';
  import Icon from './Icon.svelte';
  export let id=''; export let reloadKey=''; export let onerror:(error:any)=>void=()=>{};
  let data:Row={}, busy=false, page=1, sequence=0, copied='', currentId='', callsOpen=false;
  let copyTimer:ReturnType<typeof setTimeout>;
  $: reset(id,reloadKey);
  $: request=data.request??{};
  $: members=rows(data.members);
  onDestroy(()=>{sequence++;clearTimeout(copyTimer)});
  function reset(next:string,_revision:string){if(next){const changed=currentId!==next;if(changed){data={};callsOpen=false}currentId=next;void load(next,changed?1:page)}else{data={};sequence++;busy=false}}
  async function load(next:string,nextPage:number){
    const ticket=++sequence;busy=true;
    try{const value=await api('GetDetails',next,nextPage);if(ticket===sequence){data=value;currentId=next;page=Number(value.page??nextPage)}}
    catch(error){if(ticket===sequence)onerror(error)}finally{if(ticket===sequence)busy=false}
  }
  function body(value:any){return typeof value==='string'?value:text(value?.text??value?.content??value?.markdown,'')}
  async function copy(value:string,label:string){try{await navigator.clipboard.writeText(value);copied=label;clearTimeout(copyTimer);copyTimer=setTimeout(()=>copied='',1800)}catch(error){onerror(error)}}
  function inspect(member:Row){callsOpen=false;data={};void load(String(member.id),1)}
  function back(){callsOpen=false;data={};void load(id,1)}
</script>
<aside class="inspector surface" aria-busy={busy}>
  <div class="inspector-scroll">
    <header><h2>{tr('请求详情')}</h2>
      {#if currentId&&currentId!==id}<button class="quiet back" onclick={back}>{tr('请求详情')==='Request details'?'Back to request':'返回请求'}</button>{/if}
      {#if data.request}<h3 class="request-title">{preview(request)}</h3><p class="request-date muted">{stamp(request.timestamp)}</p>{/if}
    </header>
    {#if !id}<div class="empty">{tr('点击请求查看详情')}</div>
    {:else if busy&&!data.request}<div class="empty">{tr('正在加载')}</div>
    {:else if data.request}
      <section class="metric-fields">
        <div class="field-pair model-pair"><span class="muted">{tr('模型')}</span><Model record={request}/></div>
        <div class="field-pair"><span class="muted">{tr('推理强度')}</span><span>{effort(request.reasoning_effort)||'—'}</span></div>
        {#if speed(request.service_tier)}<div class="field-pair"><span class="muted">{tr('速度')}</span><span>{speed(request.service_tier)}</span></div>{/if}
        <div class="field-pair"><span class="muted">{tr('费用')}</span><span>{priced(request)}</span></div>
        <div class="field-pair"><span class="muted">{tr('耗时')}</span><span>{duration(request.duration_ms)}</span></div>
        {#each [['input_tokens','输入 Token'],['cached_input_tokens','缓存读取'],['cache_write_input_tokens','缓存写入'],['output_tokens','输出 Token'],['reasoning_output_tokens','推理输出'],['total_tokens','总 Token']] as [key,label]}<div class="field-pair"><span class="muted">{tr(label)}</span><span>{compact(request[key])}</span></div>{/each}
      </section>
      <section><div class="row between"><h3>{tr('原始请求')}</h3><button class="quiet" aria-label={tr('复制')} disabled={!body(data.user)} onclick={()=>copy(body(data.user),'user')}><Icon name={copied==='user'?'check':'copy'} size={15}/></button></div>
        {#if body(data.user)}<Markdown content={body(data.user)} {onerror}/>{:else}<p class="muted">{tr('暂无原始请求')}</p>{/if}
        {#if data.user_complete===false}<small class="warning">{tr('正文未完整保留')}</small>{/if}
      </section>
      <section><div class="row between"><h3>{tr('最终回复')}</h3><button class="quiet" aria-label={tr('复制')} disabled={!body(data.final)} onclick={()=>copy(body(data.final),'final')}><Icon name={copied==='final'?'check':'copy'} size={15}/></button></div>
        {#if body(data.final)}<Markdown content={body(data.final)} {onerror}/>{:else}<p class="muted">{tr('暂无最终回复')}</p>{/if}
        {#if data.final_complete===false}<small class="warning">{tr('正文未完整保留')}</small>{/if}
      </section>
      {#if rows(data.attachments).length}<section><h3>{tr('附件')}</h3>{#each rows(data.attachments) as attachment}<div class="attachment"><Icon name="report" size={15}/><span>{text(attachment.name??attachment.filename??attachment.path??attachment.type)}</span></div>{/each}</section>{/if}
      {#if members.length}<details class="calls" bind:open={callsOpen}><summary>{tr('模型调用')} ({numeric(data.total_members)})</summary>
        {#each members as member}<div class="call"><div class="call-head"><Model record={member}/><button class="quiet" disabled={busy} onclick={()=>inspect(member)}>{tr('详情')}</button></div><small class="muted">{kind(member.record_kind)} · {[effort(member.reasoning_effort),speed(member.service_tier)].filter(Boolean).join(' · ')}</small><p>{compact(member.total_tokens)} Token · {priced(member)}</p>{#if member.output_preview}<p class="muted call-preview">{text(member.output_preview)}</p>{/if}</div>{/each}
        {#if Number(data.pages)>1}<div class="row call-pages"><button disabled={page<=1||busy} onclick={()=>load(currentId,page-1)}>{tr('上一页')}</button><span>{page} / {data.pages}</span><button disabled={page>=data.pages||busy} onclick={()=>load(currentId,page+1)}>{tr('下一页')}</button></div>{/if}
      </details>{/if}
    {/if}
  </div>
</aside>
<style>
  .inspector{width:100%;height:100%;min-height:0;max-height:100%;padding:0;text-align:left;white-space:normal;font-size:12px}.inspector-scroll{height:100%;overflow:auto;padding:20px;text-align:left;scrollbar-gutter:stable}header{padding:0 18px 15px 0;border-bottom:1px solid var(--border);margin-bottom:15px}h2{font-size:16px!important;line-height:1.4!important;padding:0!important;font-weight:600}.request-title{font-size:14px!important;font-weight:500;line-height:1.5;margin:13px 0 6px!important;overflow-wrap:anywhere}.request-date{font-size:12px;line-height:1.4}section{margin:0 0 15px!important;padding-bottom:15px;border-bottom:1px solid var(--border)}section h3{font-size:12px;font-weight:500;line-height:1.5;margin:0 0 8px}section>.row{margin-bottom:6px}.row button{padding:3px 4px}.field-pair{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin:0 0 8px}.field-pair:last-child{margin:0}.field-pair>span:last-child{text-align:right;min-width:0;overflow-wrap:anywhere}.model-pair{align-items:center}.model-pair :global(.model-lines){max-width:215px;padding-left:0}.back{font-size:11px;padding:4px 0!important;margin-top:8px}.attachment{display:flex;align-items:flex-start;gap:7px;font-size:12px;margin-top:8px;overflow-wrap:anywhere}.calls{margin-top:15px}.calls summary{cursor:pointer;font-size:12px;font-weight:500}.call{padding:12px 0;border-bottom:1px solid var(--border)}.call-head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px}.call-head :global(.model-lines){align-items:flex-start;padding-left:0;max-width:230px}.call-head button{font-size:11px;padding:3px 4px}.call small{font-size:11px}.call p{font-size:12px;line-height:1.5;margin-top:5px;text-align:left}.call-preview{display:-webkit-box;-webkit-line-clamp:3;line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.call-pages{justify-content:space-between;margin-top:12px;font-size:11px}.call-pages button{font-size:11px;padding:5px 7px}.warning{display:block;font-size:11px;margin-top:7px}.empty{font-size:12px;padding:30px 0}
</style>
