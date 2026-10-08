<script lang="ts">
 import {rows,text,type Row} from '../lib/api';import{tr}from'../lib/i18n';import Icon from './Icon.svelte';
 import JourneyEvent from './JourneyEvent.svelte';import{journeyTitle,journeyIcon,journeyDuration,fileAction}from'./journey';
 export let event:Row={};export let selected='';export let onselect:(r:Row)=>void;export let onagent:(r:Row)=>void;
 const clock=new Intl.DateTimeFormat(undefined,{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
 function time(value:any){const d=new Date(value);return value&&Number.isFinite(d.getTime())?clock.format(d):'—'}
</script>
<div class="event">
 <button class="event-main" class:chosen={event.id===selected} onclick={()=>onselect(event)}><Icon name={journeyIcon(event.kind)} size={15}/><span class="event-body"><span class="event-title">{journeyTitle(event)}</span>{#if event.preview||event.kind==='reasoning'}<small>{text(event.preview)}</small>{/if}{#each rows(event.files) as file}<span class="file"><span>{fileAction(file.action)}</span><span>{text(file.path)}{file.destination?' → '+file.destination:''}</span></span>{/each}</span><span class="event-time">{event.duration_ms!=null?journeyDuration(event.duration_ms):time(event.time)}</span></button>
 {#if event.command}<details class="command"><summary>{tr('查看完整命令')}</summary><pre>{event.command}</pre></details>{/if}
 {#if event.agent_id}<button class="quiet agent" onclick={()=>onagent(event)}>{tr('查看子代理行程')}</button>{/if}
 {#if rows(event.children).length}<div class="children">{#each rows(event.children) as child(child.id)}<JourneyEvent event={child} {selected} {onselect} {onagent}/>{/each}</div>{/if}
</div>
<style>
 .event{min-width:0;padding:4px 0}.event-main{display:flex;gap:8px;width:100%;padding:8px 6px;align-items:flex-start;border:0;background:transparent;font-size:12px;text-align:left;border-radius:7px;min-width:0}.event-main:hover,.event-main.chosen{background:var(--hover)}.event-body{flex:1;min-width:0;overflow-wrap:anywhere}.event-title{display:block;line-height:1.5}.event-body small{display:-webkit-box;-webkit-line-clamp:4;line-clamp:4;-webkit-box-orient:vertical;overflow:hidden;font-size:11px;white-space:pre-wrap;color:var(--muted);margin-top:4px;line-height:1.5}.event-time{font-size:10px;color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}.file{display:flex;gap:8px;font-size:11px;line-height:1.6;margin-top:5px}.file>span:first-child{flex:none;color:var(--muted)}.file>span:last-child{min-width:0;overflow-wrap:anywhere}.command{margin:0 6px 8px 29px;font-size:11px}.command summary{cursor:pointer;color:var(--muted)}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:250px;overflow:auto;font:11px/1.55 ui-monospace,Consolas,monospace;border:1px solid var(--border);padding:10px;border-radius:7px;background:var(--surface);margin-top:8px}.children{border-left:1px solid var(--border);padding-left:13px;margin-left:14px}.agent{font-size:11px;margin-left:29px;padding:3px 0}
</style>
