<script lang="ts">
 import brandMarkSource from '../public/brand/brand-mark.svg?raw';
 const brandMark = brandMarkSource.replace('color="#000000"', 'color="currentColor"');
 import{onMount,tick}from'svelte';import{Call}from'@wailsio/runtime';import{api,listen,invalidateClientReads,defaultQuery,text,stamp,updatedStamp,iconPath,type Row,type Query,type ClientKind}from'./lib/api';import{tr,setLanguage}from'./lib/i18n';
 import ReportCatButton from './components/ReportCatButton.svelte';import Icon from './components/Icon.svelte';import Overview from './components/Overview.svelte';import Logs from './components/Logs.svelte';import Trends from './components/Trends.svelte';import Subscription from './components/Subscription.svelte';import Prices from './components/Prices.svelte';import Settings from './components/Settings.svelte';import Floating from './components/Floating.svelte';import Report from './components/Report.svelte';import UpdatePrompt from './components/UpdatePrompt.svelte';import './style.css';
 const params=new URLSearchParams(location.search);const floating=params.get('view')==='floating';const smoke=params.get('smoke')==='1';
 const titles:Record<string,string>={overview:'概览',logs:'日志',trends:'用量',subscription:'订阅',pricing:'定价',settings:'设置'};const labels:Record<string,string>={overview:'概览',logs:'日志',trends:'用量',subscription:'订阅',pricing:'定价',settings:'设置'};
 let clientKind:ClientKind='codex';
 const clientPages=new Set(['overview','logs','trends']);
 const pageKey=(name:string,client:ClientKind=clientKind)=>clientPages.has(name)&&client==='claude_code'?`${client}:${name}`:name;
 let clientFilters:Record<string,Row>={};
 const filtersDirty=new Set<ClientKind>(['codex','claude_code']);
 let page='overview';let settings:Row={};let data:Row={};let query=defaultQuery();let pageQueries:Record<string,Query>={};let cache:Record<string,Row>={};let busy=true;let error='';let notice='';let ready=false;let report=false;let reportPeriod='day';let language='zh';let theme='light';let sequence=0;let mounted=false;let eventTimer:ReturnType<typeof setTimeout>;let saveQueue=Promise.resolve();let smokeSent=false;let updateOpen=false;
 let nativeReady=false;
 let backendRefreshing=false;let manualRefreshing=false;let refreshRevision=-1;
 $: refreshActive=backendRefreshing||manualRefreshing;
 function adoptRefresh(event:Row){const revision=Number(event.refresh_revision??-1);if(revision>=refreshRevision){refreshRevision=revision;backendRefreshing=event.refreshing===true;}}
 let clientStates:Partial<Record<ClientKind,Row>>={};
 function adoptState(state:Row,client:ClientKind=state.client_kind==='claude_code'?'claude_code':'codex'){clientStates={...clientStates,[client]:state};}
 let activeClient:ClientKind='codex';
 $: activeClient=!floating&&clientPages.has(page)?clientKind:'codex';
 $: selectedState=clientStates[activeClient]??{};
 $: headerUpdated=selectedState.updated_at??null;
 $: scanError=activeClient==='codex'&&selectedState.status==='error'?text(selectedState.error,''):'';
 $: claudeSourceMessage=activeClient==='claude_code'?sourceMessage(selectedState,language):'';
 function sourceMessage(state:Row,_language:string):string{
   const status=String(state.source_status??'');
   const messages:Record<string,string>={unavailable:'Claude Code 数据源暂不可用；已采集统计与摘要仍保留，缺失原文与附件显示 —。',partial:'部分 Claude Code 数据不完整；可用统计与摘要仍保留。',empty:'当前 Claude Code 数据目录没有记录；已采集统计与摘要仍保留。',error:'Claude Code 数据读取失败；已采集统计与摘要仍保留。'};
   const count=Number(state.incomplete_records??0);
   const message=messages[status]??(count>0?messages.partial:state.status==='error'?messages.error:'');
   if(!message)return '';
   const extra=[count>0?`${tr('不完整记录')} ${count}`:'',state.error?tr(String(state.error)):''].filter(Boolean);
   return tr(message)+(extra.length?' '+extra.join(' · '):'');
 }
 $: accountQuota=(page==='subscription'?data:cache.subscription)??cache.overview?.quota??{};
 let settingsSequence=0;
 let settingsPending=false;
 let settingsFlight:Promise<void>|null=null;
 let reportCheckPending=false;
 let acknowledgementFlight:Promise<void>|null=null;
 let dataFlight:Promise<Row>|null=null;
 let eventSequence=0;
 let sidebarPreview:number|null=null;let sidebarResize:{x:number,width:number,raw:number}|null=null;
 let navPreview:string[]|null=null;let navPress:{item:string,x:number,y:number}|null=null;let draggedPage='';let navTimer:ReturnType<typeof setTimeout>;let suppressClick=false;let navElement:HTMLElement;
 $: navigation=['overview',...Array.from(new Set([...(settings.navigation_order??Object.keys(titles)),...Object.keys(titles)])).filter(item=>item!=='overview'&&item in titles)];
 $: sidebarWidth=sidebarPreview??(settings.sidebar_collapsed?62:Math.max(140,Math.min(320,Number(settings.sidebar_width)||238)));
 function resizeSidebar(event:PointerEvent){if(event.button!==0)return;event.preventDefault();sidebarResize={x:event.clientX,width:sidebarWidth,raw:sidebarWidth};}
 function pressNavigation(event:PointerEvent,item:string){if(event.button!==0||item==='overview')return;navPress={item,x:event.clientX,y:event.clientY};clearTimeout(navTimer);navTimer=setTimeout(()=>{if(navPress){draggedPage=item;navPreview=[...navigation];suppressClick=true;}},350);}
 function sidebarMove(event:PointerEvent){
   if(sidebarResize){sidebarResize.raw=sidebarResize.width+event.clientX-sidebarResize.x;sidebarPreview=Math.max(settings.sidebar_collapsed?62:140,Math.min(320,sidebarResize.raw));return;}
   if(!navPress)return;
   if(!draggedPage&&Math.hypot(event.clientX-navPress.x,event.clientY-navPress.y)>5){clearTimeout(navTimer);navPress=null;return;}
   if(!draggedPage||!navElement)return;
   const target=Array.from(navElement.querySelectorAll<HTMLButtonElement>('[data-page]')).find(button=>{const rect=button.getBoundingClientRect();return event.clientY>=rect.top&&event.clientY<=rect.bottom&&event.clientX>=rect.left&&event.clientX<=rect.right;})?.dataset.page;
   if(target&&target!=='overview'&&target!==draggedPage){const order=(navPreview??navigation).filter(item=>item!==draggedPage);order.splice(order.indexOf(target),0,draggedPage);navPreview=order;}
 }
 function sidebarEnd(cancel=false){
   if(sidebarResize){const width=sidebarResize.raw;sidebarResize=null;if(cancel)sidebarPreview=null;else{const committed=width<140?62:Math.min(320,width);sidebarPreview=committed;void save(width<140?{sidebar_collapsed:true}:{sidebar_collapsed:false,sidebar_width:committed}).finally(()=>{if(!sidebarResize&&sidebarPreview===committed)sidebarPreview=null;});}}
   clearTimeout(navTimer);navPress=null;if(draggedPage){const order=navPreview;draggedPage='';if(!cancel&&order&&order.join('|')!==navigation.join('|'))void save({navigation_order:order}).finally(()=>{if(navPreview===order)navPreview=null;});else navPreview=null;setTimeout(()=>suppressClick=false,0);}
 }
 function navClick(item:string){if(!suppressClick&&item!==page)go(item);}
 function navKeyboard(event:KeyboardEvent,item:string){if(!event.altKey||!['ArrowUp','ArrowDown'].includes(event.key)||item==='overview')return;event.preventDefault();const order=[...navigation],index=order.indexOf(item),target=index+(event.key==='ArrowUp'?-1:1);if(target>0&&target<order.length){[order[index],order[target]]=[order[target],order[index]];void save({navigation_order:order});}}
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
     if(result.state)adoptState(result.state,clientPages.has(active)?current.client_kind??'codex':'codex');
     cache[pageKey(active,current.client_kind??'codex')]=result;
     if(clientPages.has(active)&&!floating){const source=current.client_kind??'codex';if(filtersDirty.has(source))void api('GetClientFilters',source).then(value=>{if(mounted&&ticket===sequence&&source===clientKind){clientFilters={...clientFilters,[source]:{...value,models:(value.models??[]).map((model:any)=>typeof model==='string'?{model}:model)}};filtersDirty.delete(source);}}).catch(e=>{if(mounted&&ticket===sequence&&source===clientKind)fail(e)});}
     error='';
     ready=true;
     await tick();
     if(mounted&&ticket===sequence&&active==='overview')await acknowledgeRenderedOverview();
   }catch(e){if(mounted&&ticket===sequence)fail(e);}
   finally{if(dataFlight===request)dataFlight=null;if(mounted&&ticket===sequence)busy=false;}
 }
 function cancelScheduledDataReload(){eventSequence++;clearTimeout(eventTimer);}
 function go(next:string){cancelScheduledDataReload();pageQueries[pageKey(page)]={...query};page=next;query={...(pageQueries[pageKey(next)]??{...defaultQuery(),...(next==='trends'?{page_size:5}:{})}),client_kind:clientPages.has(next)?clientKind:'codex'};data=cache[pageKey(next)]??{};void reload()}
 function selectClient(next:ClientKind){if(next===clientKind||!clientPages.has(page))return;cancelScheduledDataReload();invalidateClientReads();for(const name of clientPages){const stored=name===page?query:pageQueries[pageKey(name)];if(stored)pageQueries[pageKey(name,next)]={...stored,client_kind:next,model:'',tier:'',status:'',search:'',page:1};}clientKind=next;query={...(pageQueries[pageKey(page)]??defaultQuery()),client_kind:next,model:'',tier:'',status:'',search:'',page:1};data={};error='';void reload()}
 function filter(next:Query){cancelScheduledDataReload();query={...next,client_kind:clientPages.has(page)?clientKind:'codex'};pageQueries[pageKey(page)]=query;void reload()}
 async function save(changes:Row){saveQueue=saveQueue.catch(()=>{}).then(async()=>{settingsSequence++;try{const next=await api('SaveSettings',changes);if(mounted)adoptSettings(next);}catch(e){if(mounted)fail(e);}});await saveQueue;}
 function searchLogs(){go('logs');void tick().then(()=>document.querySelector<HTMLInputElement>('[data-search]')?.focus())}
 async function refresh(){if(busy||refreshActive)return;manualRefreshing=true;try{await api('Refresh');await reload()}catch(e){fail(e)}finally{manualRefreshing=false}}
 async function desktop(action:string){try{await api('DesktopAction',action)}catch(e){fail(e)}}
 async function showReport(period='day'){reportPeriod=period;report=true;}
 function key(event:KeyboardEvent){if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();go('logs');void tick().then(()=>document.querySelector<HTMLInputElement>('[data-search]')?.focus())}if(event.key==='Escape'&&report)report=false;}
 function changed(event:Row){
   if(!mounted)return;
   if(event.scope==='refresh'){adoptRefresh(event);return;}
   if(event.state)adoptState(event.state);
   const scope=String(event.scope??'');
   if(!scope||['all','data','usage'].includes(scope)){filtersDirty.add('codex');filtersDirty.add('claude_code');}
   if(scope==='quota'&&page==='trends'&&!floating&&clientKind==='codex'){data={...data,chat_usage:{...(data.chat_usage??{}),quota_revision:event.generation}};return;}
   if(scope==='settings'||scope==='update'||scope==='report'||page==='settings'&&['mobile','upstream'].includes(scope)){
     void refreshSettings(scope==='report');
     return;
   }
   const relevant=!scope||['all','data'].includes(scope)||scope===page||
     scope==='usage'&&!floating&&['overview','logs','trends'].includes(page)||
     scope==='quota'&&(floating||page==='subscription'||page==='overview'&&clientKind==='codex')||
     scope==='upstream'&&!floating&&clientKind==='codex'&&['overview','logs'].includes(page);
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
   const offClock=listen('codexio:clock',event=>{if(mounted&&event.updated_at!=null){const client:ClientKind=event.client_kind==='claude_code'?'claude_code':'codex';adoptState({...clientStates[client],updated_at:event.updated_at},client);}});
   void api('GetState').then(state=>{if(mounted){adoptState(state,'codex');adoptRefresh(state);}}).catch(e=>{if(mounted&&activeClient==='codex')fail(e);});
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
     off();offNotice();offReport();offClock();
     clearTimeout(eventTimer);clearTimeout(navTimer);
     media.removeEventListener('change',systemTheme);
   };
 });
</script>
<svelte:window onkeydown={key} onpointermove={sidebarMove} onpointerup={()=>sidebarEnd()} onpointercancel={()=>sidebarEnd(true)} onblur={()=>sidebarEnd(true)}/>
<div class={`app theme-${theme}`} class:floating-route={floating} aria-busy={busy}>
{#key language}{#if floating}<Floating {settings} quota={data.quota??{}} summary={data.summary??{}}/>{:else}<aside class="sidebar" class:collapsed={sidebarWidth<140} class:sidebar-resizing={!!sidebarResize} style:width={`${sidebarWidth}px`}>
<div class="sidebar-brand">{#if sidebarWidth<140}<button class="brand-button" aria-label={tr('展开侧栏')} onclick={()=>save({sidebar_collapsed:false})}>{#if !settings.app_icon||settings.app_icon==='main'}<span class="brand-icon default-brand" aria-hidden="true">{@html brandMark}</span>{:else}<img class="brand-icon" src={iconPath(settings.app_icon)} alt="Codexio"/>{/if}</button>{:else}<span class="wordmark" aria-label="Codexio"></span><button class="quiet sidebar-search" aria-label={tr('搜索请求或聊天')} onclick={searchLogs}><Icon name="search" size={16}/></button>{/if}</div>
<nav aria-label="Codexio" bind:this={navElement}>{#each navPreview??navigation as item (item)}<button data-page={item} class:active={page===item} class:nav-dragging={draggedPage===item} aria-current={page===item?'page':undefined} onpointerdown={event=>pressNavigation(event,item)} onkeydown={event=>navKeyboard(event,item)} onclick={()=>navClick(item)} title={tr(labels[item])}><Icon name={item} size={18}/>{#if sidebarWidth>=140}<span>{tr(labels[item])}</span>{#if item!=='overview'}<span class="nav-grip" aria-hidden="true">≡</span>{/if}{/if}</button>{/each}</nav>
<div class="spacer"></div><div class="sidebar-account"><button class="sidebar-foot" onclick={()=>go('subscription')} title={text(accountQuota.account?.email,'Codex')}>{#if sidebarWidth<140}<Icon name="account" size={18}/>{:else}<span>{accountQuota.plan_type?'ChatGPT · '+text(accountQuota.plan_type):tr('本机 Codex')}<small>{text(accountQuota.account?.email,'')}</small></span>{/if}</button><button class="sidebar-foot floating-toggle" role="switch" aria-checked={settings.widget_visible!==false&&settings.display_mode!=='tray'} aria-label={tr('显示悬浮窗')} onclick={()=>desktop('toggle-floating')}><Icon name="floating" size={18}/>{#if sidebarWidth>=140}<span>{tr('悬浮窗')}</span><i class="sidebar-switch" class:enabled={settings.widget_visible!==false&&settings.display_mode!=='tray'} aria-hidden="true"></i>{/if}</button></div><button class="sidebar-resize" onpointerdown={resizeSidebar} onkeydown={event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();const width=sidebarWidth<140&&event.key==='ArrowRight'?140:sidebarWidth+(event.key==='ArrowLeft'?-10:10);void save(width<140?{sidebar_collapsed:true}:{sidebar_collapsed:false,sidebar_width:Math.min(320,width)});}}} aria-label={language==='en'?'Resize sidebar':'调整侧栏宽度'}></button></aside>
<main class="canvas"><header class="window-toolbar"><button class="quiet sidebar-toggle" aria-label={tr(settings.sidebar_collapsed?'展开侧栏':'收起侧栏')} onclick={()=>save({sidebar_collapsed:!settings.sidebar_collapsed})}><Icon name="sidebar"/></button><div class="spacer"></div>{#if clientPages.has(page)}<div class="client-switch" role="group" aria-label={tr('客户端来源')}>{#each [['codex','ChatGPT / Codex','chatgpt'],['claude_code','Claude Code','claude-code']] as [value,label,icon]}<button class="quiet client-button" class:selected={clientKind===value} aria-label={label} title={label} aria-pressed={clientKind===value} onclick={()=>selectClient(value as ClientKind)}><span class="client-logo" style:mask-image={`url(/client-icons/${icon}.svg)`} aria-hidden="true"></span></button>{/each}</div>{/if}<ReportCatButton onopen={()=>showReport()} suspended={report||updateOpen} dark={theme==='dark'} label={tr('打开 AI 使用报告')}/><span class="muted updated">{(language==='en'?'Last updated: ':'上次更新：')+(headerUpdated==null?'—':updatedStamp(headerUpdated))}</span><button class="quiet" aria-label={tr('刷新')} title={tr('刷新')} disabled={busy||refreshActive} aria-busy={refreshActive} onclick={refresh}><span class="refresh-icon" class:rotating={refreshActive}><Icon name="refresh" size={16}/></span></button></header><div class="page-header" class:overview-header={page==='overview'}><h1>{tr(titles[page])}</h1></div>{#if error||scanError}<div class="alert error" role="alert"><span>{error||scanError}</span><button onclick={reload}>{tr('重试')}</button><button class="quiet" onclick={()=>{error='';scanError=''}} aria-label={tr('关闭')}><Icon name="close" size={15}/></button></div>{/if}{#if claudeSourceMessage}<div class="alert" class:error={selectedState.source_status==='error'} class:warning={['partial','unavailable'].includes(String(selectedState.source_status))} role={selectedState.source_status==='empty'?'status':'alert'}><span>{claudeSourceMessage}</span></div>{/if}{#if notice}<div class="alert" role="status"><span>{notice}</span><button class="quiet" onclick={()=>notice=''} aria-label={tr('关闭')}><Icon name="close" size={15}/></button></div>{/if}<div class="page-content" class:ledger-page={page==='logs'} class:settings-page={page==='settings'}>{#if busy&&!ready&&!Object.keys(data).length}<div class="empty">{tr('正在加载')}</div>{:else if page==='overview'}{#key clientKind}<Overview {data} {query} onchange={filter} onerror={fail} onlogs={()=>go('logs')}/>{/key}{:else if page==='logs'}{#key clientKind}<Logs {data} {query} models={clientFilters[clientKind]?.models??cache[pageKey('overview')]?.models??cache[pageKey('trends')]?.models??[]} {settings} onchange={filter} onsave={save} onerror={fail}/>{/key}{:else if page==='trends'}{#key clientKind}<Trends {data} {query} {settings} onsave={save} onerror={fail} onchange={filter}/>{/key}{:else if page==='subscription'}<Subscription {data} {settings} onsave={save} onreload={reload} onerror={fail}/>{:else if page==='pricing'}<Prices {data} onreload={reload} onerror={fail}/>{:else}<Settings {settings} quota={cache.overview?.quota??{}} onsave={save} onreload={reload} onreport={()=>showReport()} onerror={fail}/>{/if}</div></main>{/if}{#if updateOpen&&!report}<UpdatePrompt state={settings.update} mock={settings.mock} onstate={u=>adoptSettings({...settings,update:u})} onerror={fail}/>{/if}{#if report}<Report initialPeriod={reportPeriod} {settings} onsave={save} onclose={()=>report=false} onpresent={()=>desktop('report-seen')} onerror={fail}/>{/if}{/key}</div>
