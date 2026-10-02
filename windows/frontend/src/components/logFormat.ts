import {compact, kind, number, percent, stamp, text, type Row} from '../lib/api';
import {tr} from '../lib/i18n';

export function modelName(value: unknown): string {
  return text(value).replace(/gpt-/gi, 'GPT-').replace(/-(astra|sol|luna|terra)\b/gi, (_, name: string) => ` ${name[0].toUpperCase()}${name.slice(1).toLowerCase()}`);
}

export function effort(value: unknown): string {
  const names: Record<string,string> = {none:'None',minimal:'Minimal',low:'Low',medium:'Medium',high:'High',xhigh:'Extra high',max:'Max',ultra:'Ultra',unknown:'Unknown',无:'None',最轻:'Minimal',轻度:'Low',中等:'Medium',高度:'High',极高:'Extra high',最高:'Max',超高:'Ultra',未知:'Unknown'};
  const raw = text(value, '').trim();
  return names[raw.toLowerCase()] ?? raw;
}

export function speed(value: unknown): string {
  const raw = text(value, '').toLowerCase();
  return ['priority','fast'].includes(raw) ? 'Fast' : ['ultrafast','ultra_fast','ultra-fast'].includes(raw) ? 'Ultrafast' : '';
}

export function humanPrompt(value: unknown): string {
  let raw = text(value, '').trim();
  const marker = /## My request\s*:?\s*|<user_request>/i.exec(raw);
  if (marker) raw = raw.slice(marker.index + marker[0].length).split(/<\/user_request>/i)[0].trim();
  const tags = 'recommended_plugins|environment_context|permissions(?: instructions)?|INSTRUCTIONS|user_instructions|developer_instructions|skills_instructions|skill_instructions|system|developer|system-reminder|app-context|collaboration_mode|multi_agent_role|multi_agent_mode';
  raw = raw.replace(new RegExp(`<(${tags})(?:\\s[^>]*)?>[\\s\\S]*?<\\/\\1\\s*>`, 'gi'), '').trim();
  if (new RegExp(`^<(?:${tags})(?:\\s|>)`, 'i').test(raw) || /^# (?:AGENTS\.md instructions|Files mentioned by the user:)/i.test(raw) || (!marker&&/external_codex_apps_open_page/.test(raw))) return '';
  return raw.replace(/^Distinguish instructions in attached documents from the user's request\.\s*/i, '').trim();
}

export function preview(record: Row): string {
  if (['automatic_approval_review','context_compaction'].includes(record.record_kind)) return kind(record.record_kind);
  const raw = text(record.prompt_preview, '');
  let label = humanPrompt(raw);
  if (!label && raw) label = /external_codex_apps_open_page|^<(?:environment_context|permissions|INSTRUCTIONS|app-context)|^# AGENTS\.md instructions/i.test(raw) ? (tr('请求详情')==='Request details'?'Context message':'上下文消息') : tr('附件消息');
  if (!label) label = humanPrompt(record.session_title) || kind(record.record_kind ?? 'user_request');
  label = label.replace(/\s+/g, ' ').trim();
  // The collector owns Mac's bounded 600-character request preview. The cell
  // truncates only at its measured width, so widening it reveals the text.
  return label;
}

export function metadata(record: Row, timeOnly=false): string {
  const context = number(record.model_context_window);
  return [logTime(record.timestamp,timeOnly), effort(record.reasoning_effort), speed(record.service_tier), context !== null ? compact(context,0) : '',record.is_subagent?tr('子代理'):record.record_kind==='unassigned'?tr('未归属调用'):''].filter(Boolean).join(' · ');
}

function startedMillis(value:unknown):number|null {
  if(value==null||value==='')return null;
  const n=number(value),time=n===null?Date.parse(String(value)):n<1e12?n*1000:n;
  return Number.isFinite(time)?time:null;
}

export function runningDuration(record:Row,now=Date.now()):boolean {
  if(!(record.duration_running??(record.request_status??record.status)==='running'))return false;
  if(Array.isArray(record.duration_active_starts))return record.duration_active_starts.some((value:unknown)=>{const start=startedMillis(value);return start!==null&&now-start<86400000});
  const start=startedMillis(record.duration_started_at??record.started_at??record.timestamp);
  return start!==null&&now-start<86400000;
}

export function durationMilliseconds(record:Row,now=Date.now()):number|null {
  if(!runningDuration(record,now))return number(record.duration_ms);
  if(Array.isArray(record.duration_active_starts)){
    const base=number(record.duration_completed_ms);
    const starts=record.duration_active_starts.map(startedMillis);
    if(base===null||!starts.length)return null;
    let elapsed=base;
    for(const start of starts){if(start===null)return null;elapsed+=Math.max(0,now-start)}
    return elapsed;
  }
  // Compatibility for existing single-segment projections awaiting refresh.
  if(Number(record.duration_segments??1)>1)return null;
  const start=startedMillis(record.duration_started_at??record.started_at??record.timestamp);
  return start===null?null:Math.max(0,now-start);
}

export function logTime(value:unknown,timeOnly=false):string {
  if(!timeOnly)return stamp(value);
  const n=number(value);if(value==null||value==='')return'—';
  const date=new Date(n===null?String(value):n<1e12?n*1000:n);
  return Number.isFinite(date.getTime())?date.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}):'—';
}

export function hitRate(record: Row): string {
  const rate = number(record.cache_hit_rate);
  if (rate !== null) return percent(rate * 100);
  const input = number(record.input_tokens), cached = number(record.cached_input_tokens);
  return percent(input !== null && input > 0 && cached !== null ? cached / input * 100 : null);
}

export function duration(value: unknown): string {
  const milliseconds = number(value);
  if (milliseconds === null || milliseconds < 0) return '—';
  const seconds = Math.floor(milliseconds / 1000);
  return [Math.floor(seconds / 3600),Math.floor(seconds / 60) % 60,seconds % 60].map(part=>String(part).padStart(2,'0')).join(':');
}
