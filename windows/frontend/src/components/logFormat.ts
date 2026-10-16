import {compact, kind, number, percent, text, type Row, type Query} from '../lib/api';
import {isEnglish, tr} from '../lib/i18n';

export function modelName(value: unknown): string {
  const raw=text(value,'').trim();
  if(['','unknown','未知','-','–','—'].includes(raw.toLowerCase()))return'—';
  return raw.replace(/gpt-/gi, 'GPT-').replace(/-(astra|sol|luna|terra)\b/gi, (_, name: string) => ` ${name[0].toUpperCase()}${name.slice(1).toLowerCase()}`);
}

export function effort(value: unknown): string {
  const names: Record<string,string> = {none:'None',minimal:'Minimal',low:'Low',medium:'Medium',high:'High',xhigh:'Extra High','extra high':'Extra High',extra_high:'Extra High','extra-high':'Extra High',max:'Max',ultra:'Ultra',mixed:'Mixed',混合:'Mixed',unknown:'—',auto:'—',无:'None',最轻:'Minimal',轻度:'Low',中等:'Medium',高度:'High',极高:'Extra High',最高:'Max',超高:'Ultra',未知:'—'};
  const raw = text(value, '').trim();
  return names[raw.toLowerCase()] ?? raw;
}

export function speed(value: unknown): string {
  const raw = text(value, '').trim().toLowerCase();
  if (['default','standard'].includes(raw)) return 'Standard';
  if (['priority','fast'].includes(raw)) return 'Fast';
  if (['ultrafast','ultra_fast','ultra-fast'].includes(raw)) return 'Ultra Fast';
  return raw === 'mixed' ? 'Mixed' : '—';
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
  if (!label && raw && !/external_codex_apps_open_page|^<(?:environment_context|permissions|INSTRUCTIONS|app-context)|^# AGENTS\.md instructions/i.test(raw)) label = tr('附件消息');
  if (!label) {
    const empty = record.submission_snapshot?.input_state === 'empty';
    label = empty ? (isEnglish()?'Empty request':'空请求') : '—';
  }
  label = label.replace(/\s+/g, ' ').trim();
  // The collector owns Mac's bounded 600-character request preview. The cell
  // truncates only at its measured width, so widening it reveals the text.
  return label;
}

export function contextTokens(record:Row):number|null {
  return number(record.client_kind==='claude_code'?record.context_input_tokens:record.model_context_window);
}

export function requestMetadata(record: Row, query: Query, showYear?: boolean): string {
  const context = contextTokens(record);
  const tier = speed(record.service_tier);
  return [logTime(record.timestamp,query,showYear), effort(record.reasoning_effort), ['Fast','Ultra Fast'].includes(tier) ? tier : '', context !== null ? compact(context,0) : ''].filter(Boolean).join(' · ');
}

function startedMillis(value:unknown):number|null {
  if(value==null||value==='')return null;
  const n=number(value),time=n===null?Date.parse(String(value)):n<1e12?n*1000:n;
  return Number.isFinite(time)?time:null;
}

function durationAnchor(record:Row):{start:number;base:number}|null {
  const start=startedMillis(record.duration_started_at),base=number(record.duration_base_ms);
  if(start!==null&&start>0&&base!==null&&base>=0)return{start,base};
  // Old projections can prove a single active segment. Multiple starts cannot
  // be summed safely without the backend's merged execution intervals.
  if(!Array.isArray(record.duration_active_starts))return null;
  const starts=record.duration_active_starts.map(startedMillis),completed=number(record.duration_completed_ms);
  if(starts.some((value:number|null)=>value===null||value<=0)||completed===null||completed<0)return null;
  const unique=[...new Set(starts)];
  return unique.length===1&&unique[0]!==null?{start:unique[0],base:completed}:null;
}

export function runningDuration(record:Row):boolean {
  return !!(record.duration_running??(record.request_status??record.status)==='running')&&durationAnchor(record)!==null;
}

export function durationMilliseconds(record:Row,now=Date.now()):number|null {
  if(record.duration_running??(record.request_status??record.status)==='running'){
    const anchor=durationAnchor(record);
    return anchor===null?null:anchor.base+Math.max(0,now-anchor.start);
  }
  const duration=number(record.duration_ms);
  return duration!==null&&duration>=0?duration:null;
}

export function tpsLabel(record:Row,unit='TPS'):string {
  const missing='—';
  if((record.request_status??record.status)!=='completed'||record.duration_running)return missing;
  const output=number(record.output_tokens),milliseconds=durationMilliseconds(record);
  if(typeof record.output_tokens==='boolean'||typeof record.duration_ms==='boolean'||output===null||!Number.isInteger(output)||output<0||milliseconds===null||milliseconds<=0)return missing;
  const rate=output/(milliseconds/1000);
  return Number.isFinite(rate)?`${Math.round(rate)} ${unit}`:missing;
}

export function ttftLabel(record:Row):string {
  const milliseconds=typeof record.ttft_ms==='boolean'?null:number(record.ttft_ms);
  return milliseconds!==null&&milliseconds>=0?(milliseconds/1000).toFixed(2)+(isEnglish()?' s':' 秒'):'—';
}

const pad=(value:number)=>String(value).padStart(2,'0');
const months=['January','February','March','April','May','June','July','August','September','October','November','December'];
export function logTime(value:unknown,query?:Query,showYear?:boolean):string {
  const milliseconds=startedMillis(value);
  if(milliseconds===null)return'—';
  const date=new Date(milliseconds),now=new Date();
  const clock=`${pad(date.getHours())}:${pad(date.getMinutes())}`;
  if(query?.period==='today'&&date.toDateString()===now.toDateString())return clock;
  const start=new Date(now.getFullYear(),now.getMonth(),now.getDate());
  if(query?.period==='week')start.setDate(start.getDate()-6);
  else if(query?.period==='month')start.setDate(start.getDate()-29);
  else if(query?.period==='yesterday')start.setDate(start.getDate()-1);
  else if(query?.period==='year')start.setFullYear(start.getFullYear()-1);
  // Logs pass the backend's filtered-range decision, including all-time pages.
  const years=[date.getFullYear(),start.getFullYear()];
  for(const bound of [query?.start,query?.end]){
    if(bound){const year=Number(bound.slice(0,4));if(Number.isFinite(year))years.push(year);}
  }
  const withYear=showYear??(['all','all_time'].includes(query?.period??'')||years.some(year=>year!==now.getFullYear()));
  return `${withYear?`${date.getFullYear()}.`:''}${date.getMonth()+1}.${date.getDate()} ${clock}`;
}

export function fullTime(value:unknown):string {
  const milliseconds=startedMillis(value);
  if(milliseconds===null)return'—';
  const date=new Date(milliseconds),clock=`${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return isEnglish()?`${months[date.getMonth()]} ${date.getDate()}, ${date.getFullYear()} ${clock}`:`${date.getFullYear()} 年 ${date.getMonth()+1} 月 ${date.getDate()} 日 ${clock}`;
}

export function requestStatus(record:Row):string {
  const labels:Record<string,string>={running:'进行中',completed:'已完成',aborted:'已中止',failed:'失败'};
  const label=labels[record.request_status??record.status];
  return label?tr(label):'—';
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
  const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds/60)%60,remaining=seconds%60;
  if(isEnglish())return hours>0?`${hours} h ${minutes} min ${remaining} s`:minutes>0?`${minutes} min ${remaining} s`:`${remaining} s`;
  return hours>0?`${hours} 小时 ${minutes} 分 ${remaining} 秒`:minutes>0?`${minutes} 分 ${remaining} 秒`:`${remaining} 秒`;
}
