<script lang="ts">
 import {rows,text,type Row} from '../lib/api';import{tr}from'../lib/i18n';import JourneyIcon from './JourneyIcon.svelte';
 import JourneyEvent from './JourneyEvent.svelte';import JourneyCommand from './JourneyCommand.svelte';import{journeyTitle,journeyDuration,fileAction}from'./journey';
 export let event:Row={};export let selected='';export let onselect:(r:Row)=>void;export let onagent:(r:Row)=>void;
 const clock=new Intl.DateTimeFormat(undefined,{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
 function time(value:any){const d=new Date(value);return value&&Number.isFinite(d.getTime())?clock.format(d):'—'}
</script>
<div class="event">
 <div class="event-main" class:chosen={event.id===selected} class:guidance={event.kind==='userMessage'}>
  <div class="event-body">
   <div class="event-heading"><JourneyIcon kind={event.kind}/><button class="event-title" title={journeyTitle(event)} onclick={()=>onselect(event)}>{journeyTitle(event)}</button>{#if event.command}<JourneyCommand command={event.command} tool={journeyTitle(event)} onopen={()=>onselect(event)}/>{/if}<span class="event-time">{event.duration_ms!=null?journeyDuration(event.duration_ms):time(event.time)}</span></div>
   {#if event.preview||event.kind==='reasoning'||rows(event.files).length}<button class="event-details" onclick={()=>onselect(event)}>{#if event.preview||event.kind==='reasoning'}<small>{text(event.preview)}</small>{/if}{#each rows(event.files) as file}<span class="file"><span>{fileAction(file.action)}</span><span>{text(file.path)}{file.destination?' → '+file.destination:''}</span></span>{/each}</button>{/if}
  </div>
 </div>
 {#if event.agent_id}<button class="quiet agent" onclick={()=>onagent(event)}>{tr('查看子代理行程')}</button>{/if}
 {#if rows(event.children).length}<div class="children">{#each rows(event.children) as child(child.id)}<JourneyEvent event={child} {selected} {onselect} {onagent}/>{/each}</div>{/if}
</div>
<style>
 .event{min-width:0;padding:4px 0}.event-main{display:flex;gap:8px;width:100%;padding:8px 6px;align-items:flex-start;border:1px solid transparent;background:transparent;font-size:12px;text-align:left;border-radius:7px;min-width:0}.event-main:hover,.event-main.chosen{background:var(--hover)}.event-main.guidance{background:color-mix(in srgb,var(--tokens) 8%,var(--bg));border-color:color-mix(in srgb,var(--tokens) 16%,transparent)}.event-main.guidance:hover,.event-main.guidance.chosen{background:color-mix(in srgb,var(--tokens) 14%,var(--bg))}.guidance>.event-body .event-title{font-weight:500}.guidance :global(.journey-icon){color:var(--tokens)}.event-body{flex:1;min-width:0;overflow-wrap:anywhere}.event-heading{display:flex;gap:8px;align-items:center;min-width:0;min-height:22px}.event-title{display:block;padding:0;min-height:22px;line-height:20px;border:0;border-radius:0;background:transparent;text-align:left;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;justify-content:flex-start}.event-title:hover,.event-details:hover{background:transparent}.event-details{display:block;padding:0;margin-left:24px;width:calc(100% - 24px);min-height:0;border:0;border-radius:0;background:transparent;white-space:normal;text-align:left;font-weight:400}.event-body small{display:-webkit-box;-webkit-line-clamp:4;line-clamp:4;-webkit-box-orient:vertical;overflow:hidden;font-size:11px;white-space:pre-wrap;color:var(--muted);margin-top:4px;line-height:1.5}.event-time{margin-left:auto;flex:none;font-size:10px;color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}.file{display:flex;gap:8px;font-size:11px;line-height:1.6;margin-top:5px}.file>span:first-child{flex:none;color:var(--muted)}.file>span:last-child{min-width:0;overflow-wrap:anywhere}.children{border-left:1px solid var(--border);padding-left:13px;margin-left:14px}.agent{font-size:11px;margin-left:29px;padding:3px 0}
</style>
