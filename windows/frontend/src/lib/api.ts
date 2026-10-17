import { Call, Events } from '@wailsio/runtime';
export type Row = Record<string, any>;
export type ClientKind = 'codex' | 'claude_code';
export type Query = { client_kind?: ClientKind; mode: string; period: string; start: string; end: string; model: string; tier: string; status: string; search: string; page: number; page_size: number; granularity: string };
export const defaultQuery = (): Query => ({client_kind:'codex',mode:'user_request',period:'today',start:'',end:'',model:'',tier:'',status:'',search:'',page:1,page_size:50,granularity:''});
const prefix = 'codexio/windows/backend.Service.';
const reads = new Map<string, {promise:Promise<any>;cancel:()=>void;revision?:string}>();
export function api<T = Row>(method: string, ...args: any[]): Promise<T> {return invoke<T>(method,args);}
export function revisionRead<T = Row>(method:string,revision:string,...args:any[]):Promise<T>{return invoke<T>(method,args,revision);}
function invoke<T>(method:string,args:any[],revision?:string):Promise<T>{
  const key = method + JSON.stringify(args);
  const reading=method.startsWith('Get');
  const existing=reading?reads.get(key):undefined;
  if(existing&&(revision===undefined||existing.revision===revision))return existing.promise;
  if(existing){reads.delete(key);existing.cancel();}
  const call=Call.ByName(prefix + method,...args);
  if(!reading)return call as Promise<T>;
  let timer:ReturnType<typeof setTimeout>;
  const promise=new Promise<T>((resolve,reject)=>{
    timer=setTimeout(()=>{
      const error=new Error(document.documentElement.lang==='en'?'Data read timed out. Please retry.':'数据读取超时，请重试');
      reject(error);
      void call.cancel(error);
    },35000);
    void call.then(resolve,reject);
  });
  const entry={promise,cancel:()=>{void call.cancel();},revision};
  reads.set(key,entry);
  void promise.finally(()=>{clearTimeout(timer);if(reads.get(key)===entry)reads.delete(key);}).catch(()=>{});
  return promise;
}
// Source changes abandon coalescing identities; in-flight callers still settle
// under their existing sequence guards and cannot remove replacement reads.
export function invalidateClientReads():void {
  for(const [key,entry] of reads)if(/^(GetOverview|GetLogs|GetTrends|GetClientFilters|GetClientChatRanking|GetDetails)/.test(key)){reads.delete(key);entry.cancel();}
}
export function listen(name: string, fn: (data: Row) => void): () => void {
  return Events.On(name, (event: any) => fn(event.data ?? {}));
}
export const rows = (value: any): Row[] => Array.isArray(value) ? value : [];
export const text = (value: any, fallback = '—'): string => value === null || value === undefined || value === '' ? fallback : String(value);
export const number = (value: any): number | null => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
export function numeric(value: any): string { const n=number(value); return n === null ? '—' : new Intl.NumberFormat(undefined, {maximumFractionDigits:0}).format(n); }
export function compact(value: any, fractionDigits:0|1=1): string {
  const n=number(value);
  if(n===null)return'—';
  const absolute=Math.abs(n);
  const edge=fractionDigits===0?999.5:999.95;
  const [unit,suffix]=absolute>=edge*1e6?[1e9,'B']:absolute>=edge*1e3?[1e6,'M']:absolute>=1e3?[1e3,'K']:[1,''];
  return (n/unit).toLocaleString('en-US',{maximumFractionDigits:suffix?fractionDigits:0})+suffix;
}
export function cost(value: any): string { const n=number(value); return n === null ? '—' : '$' + n.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}); }
export function priced(row: Row, key='cost_usd'): string { const en=document.documentElement.lang==='en';const value=row[key] ?? row.usd; return number(value)===null ? '—' : (row.pricing_status==='estimated'?'≈':'')+cost(value) + (row.cost_complete===false || Number(row.unpriced_calls)>0 || row.pricing_status==='partial' ? en?' · Partially priced':' · 部分定价' : ''); }
export function updatedStamp(value:any):string{if(value==null||value==='')return'—';const n=number(value);const d=new Date(n===null?value:n<1e12?n*1000:n);if(!Number.isFinite(d.getTime()))return'—';return `${d.getMonth()+1}.${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;}
export function percent(value: any): string { const n=number(value); return n === null ? '—' : n.toLocaleString(undefined,{maximumFractionDigits:1})+'%'; }
export function stamp(value: any, utc=false): string { if (value===null || value===undefined || value==='') return '—'; const n=number(value); const d=new Date(n!==null ? n<1e12 ? n*1000 : n : value); if(!Number.isFinite(d.getTime())) return text(value); return d.toLocaleString(undefined,{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',...(utc?{timeZone:'UTC'}:{})})+(utc?' UTC':''); }
export function utcReportStamp(value:any,dayOnly=false):string {if(value==null||value==='')return'—';const n=number(value),d=new Date(n===null?value:n<1e12?n*1000:n);if(!Number.isFinite(d.getTime()))return'—';const pad=(n:number)=>String(n).padStart(2,'0');return `${d.getUTCFullYear()}.${pad(d.getUTCMonth()+1)}.${pad(d.getUTCDate())}${dayOnly?'':` ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`} UTC`;}
export function kind(value: any, en=document.documentElement.lang==='en'): string { const labels: Record<string,string[]>={user_request:['用户请求','User request'],model_call:['模型调用','Model call'],automatic_approval_review:['自动审批审查','Automatic approval review'],context_compaction:['上下文压缩','Context compaction'],subagent:['子代理','Subagent'],subagent_request:['子代理','Subagent'],unassigned:['未归属调用','Unassigned call']}; return labels[value]?.[en?1:0]??text(value); }
export function iconPath(id: string, preview=false): string { return id==='main'||!id ? '/brand/'+(preview?'app-icons/main-preview.png':'app-light.svg') : '/brand/app-icons/'+id+(preview?'-preview':'')+'.png'; }
export async function openURL(url: string) { if (/^https?:\/\//i.test(url)) await api('OpenURL',url); }
