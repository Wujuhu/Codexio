<script lang="ts">
 import{onMount,tick}from'svelte';import{Call}from'@wailsio/runtime';import{api,listen,defaultQuery,text,stamp,updatedStamp,iconPath,type Row,type Query}from'./lib/api';import{tr,setLanguage}from'./lib/i18n';
 import Icon from './components/Icon.svelte';import Overview from './components/Overview.svelte';import Logs from './components/Logs.svelte';import Trends from './components/Trends.svelte';import Subscription from './components/Subscription.svelte';import Prices from './components/Prices.svelte';import Settings from './components/Settings.svelte';import Floating from './components/Floating.svelte';import Report from './components/Report.svelte';import UpdatePrompt from './components/UpdatePrompt.svelte';import './style.css';
 const params=new URLSearchParams(location.search);const floating=params.get('view')==='floating';const smoke=params.get('smoke')==='1';
 const titles:Record<string,string>={overview:'概览',logs:'请求日志',trends:'用量趋势',subscription:'订阅额度',pricing:'模型定价',settings:'设置'};const labels:Record<string,string>={overview:'概览',logs:'日志',trends:'用量',subscription:'订阅',pricing:'定价',settings:'设置'};
 let page='overview';let settings:Row={};let data:Row={};let query=defaultQuery();let pageQueries:Record<string,Query>={};let cache:Record<string,Row>={};let busy=true;let error='';let notice='';let ready=false;let report=false;let reportPeriod='day';let language='zh';let theme='light';let sequence=0;let mounted=false;let eventTimer:ReturnType<typeof setTimeout>;let saveQueue=Promise.resolve();let smokeSent=false;let updateOpen=false;
 let nativeReady=false;
 let settingsSequence=0;
 let settingsPending=false;
 let settingsFlight:Promise<void>|null=null;
 let reportCheckPending=false;
 let acknowledgementFlight:Promise<void>|null=null;
 let dataFlight:Promise<Row>|null=null;
 let eventSequence=0;
 $: if(!floating&&!smoke&&settings.update?.status==='available'&&Number(settings.update?.deferred_until??0)<Date.now()/1000) updateOpen=true;
 $: if(Number(settings.update?.deferred_until??0)>Date.now()/1000||settings.update?.status==='current') updateOpen=false;
 $: {language=settings.language??'zh';setLanguage(language);}
 $: theme=settings.theme==='system'||!settings.theme?(typeof matchMedia==='function'&&matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):settings.theme;
 $: if(mounted)document.documentElement.lang=language==='en'?'en':'zh-CN';
 function fail(e:any){error=typeof e==='string'?e:e?.message??String(e)}
 function adoptSettings(next:Row){
   // A mutation projection must invalidate older preference reads as well.
   settingsSequence++;
   settings=next;
   if(settingsFlight) settingsPending=true;
 }
 function refreshSettings(checkReport=false):Promise<void>{
   settingsSequence++;
   settingsPending=true;
   reportCheckPending=reportCheckPending||checkReport;
   if(settingsFlight) return settingsFlight;
   // Drain a superseded coalesced read before requesting a fresh projection.
   // Starting a second GetSettings during it would reuse its old promise.
   const flight=Promise.resolve().then(async()=>{
       while(settingsPending&&mounted){
         settingsPending=false;
         const ticket=settingsSequence;
         try{
           const next=await api('GetSettings');
           if(!mounted||ticket!==settingsSequence) continue;
           settings=next;
           if(page==='settings'&&!floating){data=next;cache.settings=next;}
           if(reportCheckPending){
             reportCheckPending=false;
             if(next.report_due&&next.usage_report_auto!==false&&!floating&&!smoke&&!report)
               void showReport(next.report_preferred_period??'day');
           }
         }catch(e){if(mounted&&ticket===settingsSequence) fail(e);}
       }
   });
   settingsFlight=flight;
   void flight.finally(()=>{if(settingsFlight===flight)settingsFlight=null;}).catch(()=>{});
   return flight;
 }
 async function acknowledgeRenderedOverview(){
   if(floating||acknowledgementFlight||(smoke?smokeSent:nativeReady)) return;
   const tokens=document.querySelector('[data-metric="tokens"]')?.textContent;
   if(!tokens||(smoke&&data.summary?.tokens==null)) return;
   const payload={rendered_tokens:tokens,rendered_requests:document.querySelector('[data-metric="user_requests"]')?.textContent??String(data.summary?.user_requests),page:'overview'};
   const flight=Promise.resolve().then(async()=>{
     try{
       if(smoke){await Call.ByName('main.SmokeService.Ready',payload);smokeSent=true;}
       else{await api('Ready');nativeReady=true;}
     }catch(e){if(mounted)fail(e);}
   });
   acknowledgementFlight=flight;
   try{await flight;}finally{if(acknowledgementFlight===flight)acknowledgementFlight=null;}
 }
 async function reload(){
   const ticket=++sequence;
   const active=page;
   const current={...query};
   let request:Promise<Row>|null=null;
   busy=true;
   try{
     if(active==='settings'&&!floating){await refreshSettings();if(mounted&&ticket===sequence)ready=true;return;}
     const method=floating?'GetOverview':({overview:'GetOverview',logs:'GetLogs',trends:'GetTrends',subscription:'GetSubscription',pricing:'GetPrices'} as Record<string,string>)[active];
     request=api(method,...(['GetOverview','GetLogs','GetTrends'].includes(method)?[current]:[]));
     dataFlight=request;
     const result=await request;
     if(!mounted||ticket!==sequence) return;
     data=result;
     cache[active]=result;
     error='';
     ready=true;
     await tick();
     if(mounted&&ticket===sequence&&active==='overview')await acknowledgeRenderedOverview();
   }catch(e){if(mounted&&ticket===sequence)fail(e);}
   finally{if(dataFlight===request)dataFlight=null;if(mounted&&ticket===sequence)busy=false;}
 }
 function cancelScheduledDataReload(){eventSequence++;clearTimeout(eventTimer);}
 function go(next:string){cancelScheduledDataReload();pageQueries[page]={...query};page=next;query=pageQueries[next]??{...defaultQuery(),...(next==='trends'?{page_size:5}:{})};data=cache[next]??{};void reload()}
 function filter(next:Query){cancelScheduledDataReload();query=next;pageQueries[page]=next;void reload()}
 async function save(changes:Row){saveQueue=saveQueue.catch(()=>{}).then(async()=>{settingsSequence++;try{const next=await api('SaveSettings',changes);if(mounted)adoptSettings(next);}catch(e){if(mounted)fail(e);}});await saveQueue;}
 async function refresh(){if(busy)return;busy=true;try{await api('Refresh');await reload()}catch(e){busy=false;fail(e)}}
 async function desktop(action:string){try{await api('DesktopAction',action)}catch(e){fail(e)}}
 async function showReport(period='day'){reportPeriod=period;report=true;}
 function key(event:KeyboardEvent){if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();go('logs');void tick().then(()=>document.querySelector<HTMLInputElement>('[data-search]')?.focus())}if(event.key==='Escape'&&report)report=false;}
 function changed(event:Row){
   if(!mounted)return;
   const scope=String(event.scope??'');
   if(scope==='settings'||scope==='update'||scope==='report'||page==='settings'&&['mobile','upstream'].includes(scope)){
     void refreshSettings(scope==='report');
     return;
   }
   const relevant=!scope||['all','data'].includes(scope)||scope===page||
     scope==='usage'&&!floating&&['overview','logs','trends'].includes(page)||
     scope==='quota'&&(floating||['overview','subscription'].includes(page))||
     scope==='upstream'&&!floating&&['overview','logs'].includes(page);
   if(!relevant)return;
   clearTimeout(eventTimer);
   const eventTicket=++eventSequence;
   const active=page;
   eventTimer=setTimeout(()=>{
     const previous=dataFlight;
     void Promise.resolve(previous).catch(()=>{}).then(()=>{
       if(mounted&&eventTicket===eventSequence&&page===active)void reload();
     });
   },120);
 }
 onMount(()=>{
   mounted=true;
   const off=listen('codexio:changed',changed);
   const offReport=listen('codexio:show-report',e=>{if(!floating)void showReport(e.period??'day')});
   const offNotice=listen('codexio:notice',e=>{notice=text(e.message??e.status,'');if(e.scope==='update')changed(e)});
   const media=matchMedia('(prefers-color-scheme: dark)');
   const systemTheme=()=>{if(settings.theme==='system')settings={...settings};};
   media.addEventListener('change',systemTheme);
   void refreshSettings(true).then(()=>{if(mounted)return reload();});
   return()=>{
     mounted=false;
     sequence++;
     settingsSequence++;
     settingsPending=false;
     eventSequence++;
     off();offNotice();offReport();
     clearTimeout(eventTimer);
     media.removeEventListener('change',systemTheme);
   };
 });
</script>
<svelte:window onkeydown={key}/>
<div class={`app theme-${theme}`} class:floating-route={floating} aria-busy={busy}>
{#key language}{#if floating}<Floating {settings} quota={data.quota??{}} onsave={save} onerror={fail}/>{:else}<aside class="sidebar" class:collapsed={settings.sidebar_collapsed}><button class="brand-button" aria-label={tr(settings.sidebar_collapsed?'展开侧栏':'收起侧栏')} onclick={()=>save({sidebar_collapsed:!settings.sidebar_collapsed})}>{#if settings.sidebar_collapsed}<img class="brand-icon" src={iconPath(settings.app_icon??'main')} alt="Codexio"/>{:else}<span class="wordmark" aria-label="Codexio"></span>{/if}</button><nav aria-label="Codexio">{#each settings.navigation_order??Object.keys(titles) as item}<button class:active={page===item} aria-current={page===item?'page':undefined} onclick={()=>go(item)} title={tr(labels[item])}><Icon name={item}/>{#if !settings.sidebar_collapsed}<span>{tr(labels[item])}</span>{/if}</button>{/each}</nav><div class="spacer"></div><button class="sidebar-foot" onclick={()=>go('subscription')}><span class="account-dot"></span>{#if !settings.sidebar_collapsed}<span>{text(settings.state?.plan_type??data.quota?.plan_type,'Codex')}</span>{/if}</button><button class="sidebar-foot" onclick={()=>desktop('toggle-floating')}><Icon name="floating"/>{#if !settings.sidebar_collapsed}{tr('悬浮窗')}{/if}</button></aside>
<main class="canvas"><header class="page-header"><h1>{tr(titles[page])}</h1><div class="row header-actions"><span class="muted updated">{settings.state?.updated_at||data.quota?.updated_at||data.updated_at?(language==='en'?'Last updated: ':'上次更新：')+updatedStamp(settings.state?.updated_at??data.quota?.updated_at??data.updated_at):''}</span><button class="quiet" aria-label={tr('刷新')} disabled={busy} onclick={refresh}><Icon name="refresh"/></button><button class="quiet" onclick={()=>showReport()}><Icon name="report"/>{tr('AI 使用报告')}</button></div></header>{#if error}<div class="alert error" role="alert"><span>{error}</span><button onclick={reload}>{tr('重试')}</button><button class="quiet" onclick={()=>error=''} aria-label={tr('关闭')}><Icon name="close" size={15}/></button></div>{/if}{#if notice}<div class="alert" role="status"><span>{notice}</span><button class="quiet" onclick={()=>notice=''} aria-label={tr('关闭')}><Icon name="close" size={15}/></button></div>{/if}<div class="page-content" class:ledger-page={page==='logs'}>{#if busy&&!ready&&!Object.keys(data).length}<div class="empty">{tr('正在加载')}</div>{:else if page==='overview'}<Overview {data} {query} onchange={filter} onerror={fail}/>{:else if page==='logs'}<Logs {data} {query} models={cache.overview?.models??cache.trends?.models??[]} {settings} onchange={filter} onsave={save} onerror={fail}/>{:else if page==='trends'}<Trends {data} {query} onchange={filter}/>{:else if page==='subscription'}<Subscription {data} {settings} onsave={save} onreload={reload} onerror={fail}/>{:else if page==='pricing'}<Prices {data} onreload={reload} onerror={fail}/>{:else}<Settings {settings} quota={cache.overview?.quota??{}} onsave={save} onreload={reload} onerror={fail}/>{/if}</div></main>{/if}{#if updateOpen&&!report}<UpdatePrompt state={settings.update} mock={settings.mock} onstate={u=>adoptSettings({...settings,update:u})} onerror={fail}/>{/if}{#if report}<Report initialPeriod={reportPeriod} {settings} onsave={save} onclose={()=>report=false} onpresent={()=>desktop('report-seen')} onerror={fail}/>{/if}{/key}</div>
