import { Call, Events } from '@wailsio/runtime';
export type Row = Record<string, any>;
export type Query = { mode: string; period: string; start: string; end: string; model: string; tier: string; status: string; search: string; page: number; page_size: number; granularity: string };
export const defaultQuery = (): Query => ({mode:'user_request',period:'today',start:'',end:'',model:'',tier:'',status:'',search:'',page:1,page_size:50,granularity:''});
const prefix = 'codexio/windows/backend.Service.';
const reads = new Map<string, Promise<any>>();
export function api<T = Row>(method: string, ...args: any[]): Promise<T> {
  const key = method + JSON.stringify(args);
  if (method.startsWith('Get') && reads.has(key)) return reads.get(key)!;
  const promise = Call.ByName(prefix + method, ...args) as Promise<T>;
  if (method.startsWith('Get')) { reads.set(key, promise); void promise.finally(() => reads.delete(key)).catch(() => {}); }
  return promise;
}
export function listen(name: string, fn: (data: Row) => void): () => void {
  return Events.On(name, (event: any) => fn(event.data ?? {}));
}
export const rows = (value: any): Row[] => Array.isArray(value) ? value : [];
export const text = (value: any, fallback = '—'): string => value === null || value === undefined || value === '' ? fallback : String(value);
export const number = (value: any): number | null => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
export function numeric(value: any): string { const n=number(value); return n === null ? '—' : new Intl.NumberFormat(undefined, {maximumFractionDigits:0}).format(n); }
export function compact(value: any): string {
  const n=number(value);
  if(n===null)return'—';
  const absolute=Math.abs(n);
  const [unit,suffix]=absolute>=1e9?[1e9,'B']:absolute>=1e6?[1e6,'M']:absolute>=1e3?[1e3,'K']:[1,''];
  return (n/unit).toLocaleString('en-US',{maximumFractionDigits:suffix?1:0})+suffix;
}
export function cost(value: any): string { const n=number(value); return n === null ? '—' : '$' + n.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}); }
export function priced(row: Row, key='cost_usd'): string { const en=document.documentElement.lang==='en';const value=row[key] ?? row.usd; return number(value)===null ? en?'Unpriced':'未定价' : cost(value) + (row.cost_complete===false || Number(row.unpriced_calls)>0 || row.pricing_status==='partial' ? en?' · Partially priced':' · 部分定价' : ''); }
export function updatedStamp(value:any):string{if(value==null||value==='')return'—';const n=number(value);const d=new Date(n===null?value:n<1e12?n*1000:n);if(!Number.isFinite(d.getTime()))return'—';return `${d.getMonth()+1}.${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;}
export function percent(value: any): string { const n=number(value); return n === null ? '—' : n.toLocaleString(undefined,{maximumFractionDigits:1})+'%'; }
export function stamp(value: any, utc=false): string { if (value===null || value===undefined || value==='') return '—'; const n=number(value); const d=new Date(n!==null ? n<1e12 ? n*1000 : n : value); if(!Number.isFinite(d.getTime())) return text(value); return d.toLocaleString(undefined,{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',...(utc?{timeZone:'UTC'}:{})})+(utc?' UTC':''); }
export function utcReportStamp(value:any,dayOnly=false):string {if(value==null||value==='')return'—';const n=number(value),d=new Date(n===null?value:n<1e12?n*1000:n);if(!Number.isFinite(d.getTime()))return'—';const pad=(n:number)=>String(n).padStart(2,'0');return `${d.getUTCFullYear()}.${pad(d.getUTCMonth()+1)}.${pad(d.getUTCDate())}${dayOnly?'':` ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`} UTC`;}
export function kind(value: any, en=document.documentElement.lang==='en'): string { const labels: Record<string,string[]>={user_request:['用户请求','User request'],model_call:['模型调用','Model call'],automatic_approval_review:['自动审批审查','Automatic approval review'],context_compaction:['上下文压缩','Context compaction'],subagent:['子代理','Subagent'],subagent_request:['子代理','Subagent'],unassigned:['未归属调用','Unassigned call']}; return labels[value]?.[en?1:0]??text(value); }
export function iconPath(id: string, preview=false): string { return id==='main'||!id ? '/brand/'+(preview?'app-icons/main-preview.png':'app-light.svg') : '/brand/app-icons/'+id+(preview?'-preview':'')+'.png'; }
export async function openURL(url: string) { if (/^https?:\/\//i.test(url)) await api('OpenURL',url); }
