import {rows,text,number,compact,cost,type Row} from '../lib/api';
import {modelName,duration,speed,effort,requestStatus,fullTime} from './logFormat';
import {tr} from '../lib/i18n';
export type JourneyBlock={id:string;model?:Row;items:Row[]};
export function journeyBlocks(events:Row[]):JourneyBlock[]{
 const parentIDs=new Set(events.filter(e=>e.kind==='tool').map(e=>e.id));const children=new Map<string,Row[]>();
 for(const e of events)if(parentIDs.has(e.parent_id))children.set(e.parent_id,[...(children.get(e.parent_id)??[]),e]);
 const blocks:JourneyBlock[]=[];let pending:Row[]=[];
 function flush(){if(!pending.length)return;const last=blocks.at(-1);if(last?.model)last.items.push(...pending);else blocks.push({id:pending[0].id,items:pending});pending=[]}
 for(const raw of events){if(parentIDs.has(raw.parent_id))continue;const e:Row={...raw,children:children.get(raw.id)};if(e.primary)continue;if(e.kind==='model'){blocks.push({id:e.id,model:e,items:pending});pending=[]}else if(['pause','resume','completed','userMessage','agentInput'].includes(e.kind)){flush();blocks.push({id:e.id,items:[e]})}else pending.push(e)}flush();return blocks;
}
export function journeyTitle(e:Row):string{
 if(e.kind==='model')return tr('调用了')+' '+modelName(e.model);
 if(['commandExecution','tool'].includes(e.kind))return text(e.tool);
 if(e.kind==='mcpToolCall')return 'MCP · '+text(e.tool);
 if(e.kind==='subAgentActivity')return tr(e.activity==='started'?'启动子代理':e.activity==='completed'?'子代理完成':'子代理');
 if(e.kind==='agentMessage')return tr(e.phase==='final_answer'?'返回回答':'进度说明');
 const names:Record<string,string>={reasoning:'思考摘要',fileChange:'文件改动',userMessage:'用户引导',agentInput:'代理指令',userInput:'AI 提问',userInputReply:'用户回答',subAgentActivity:'子代理',collabAgentToolCall:'子代理',imageView:'查看图片',imageGeneration:'生成图片',webSearch:'网页工具',contextCompaction:'上下文压缩',resume:'继续执行',pause:'执行中断',completed:'本轮完成',sleep:'等待',plan:'更新计划'};
 return tr(names[e.kind]??text(e.kind));
}
// These SVGs are also bundled into the Mac app; keep one source of artwork.
const journeyIcons=new Set(['model','commandExecution','tool','reasoning','fileChange','userMessage','userInput','agentMessage','subAgentActivity','imageView','imageGeneration','webSearch','contextCompaction','pause','resume','completed','sleep','plan','mcpToolCall','command','close','chevronDown','chevronRight','userInputReply','agentInput','collabAgentToolCall','default']);
export function journeyIcon(kind:string):string{return journeyIcons.has(kind)?kind:'default'}
export function journeyDuration(value:any):string{const n=number(value);if(n===null||n<0)return'—';if(n===0)return'0 ms';if(n<1)return'<1 ms';if(n<1000)return Number(n.toFixed(2))+' ms';return duration(n)}
export function fileAction(value:any):string{return tr(({add:'新增',delete:'删除',update:'修改',read:'读取',search:'搜索',listfiles:'列出',image:'生成',visualization:'可视化'} as Record<string,string>)[String(value??'').toLowerCase()]??'修改')}
export function eventFields(e:Row):[string,string][]{
 if(e.kind==='model')return [[tr('模型'),modelName(e.model)],[tr('推理强度'),effort(e.effort)||'—'],[tr('速度'),speed(e.speed)],[tr('耗时'),journeyDuration(e.duration_ms)],[tr('费用'),cost(e.cost_usd)],['Token',compact(e.tokens)],...(['input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens'].map((k,i)=>[tr(['输入 Token','缓存读取','缓存写入','输出 Token','推理输出'][i]),compact(e.usage?.[k])] as [string,string]))];
 const result:[string,string][]=[[tr('工具'),text(e.tool)],[tr('状态'),e.status==='interrupted'?tr('已中止'):requestStatus(e)],[tr('耗时'),journeyDuration(e.duration_ms)],[tr('时间'),fullTime(e.time)]];
 if(e.server)result.splice(1,0,[tr('服务'),text(e.server)]);if(e.exit_code!=null)result.push([tr('退出码'),text(e.exit_code)]);if(e.cwd)result.push([tr('工作目录'),text(e.cwd)]);
 if(e.agent_id)result.push([tr('费用'),cost(e.cost_usd)],['Token',compact(e.tokens)],[tr('模型调用'),text(e.calls)]);return result;
}
