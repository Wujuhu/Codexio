<script lang="ts">
 import{number,stamp,updatedStamp,compact,cost,type Row}from'../lib/api';
 import{tr}from'../lib/i18n';
 export let settings:Row={};export let quota:Row={};export let summary:Row={};export let preview=false;
 function remaining(window:Row){return number(window?.remaining_percent)??(number(window?.used_percent)===null?null:100-Number(window.used_percent))}
 function quotaColor(value:number|null){return value===null?'#D0D5DD':value>=50?'#7DDEA0':value>=20?'#F6D56B':'#FF8A80'}
 function label(value:number|null){return value===null?'N/A':`${Math.round(value)}%`}
 $: style=settings.visual_style??'classic';
 $: scope=settings.quota_scope??'auto';
 $: docked=!preview&&!!settings.dock_edge&&settings.dock_edge!=='none';
 $: vertical=docked&&['left','right'].includes(settings.dock_edge);
 $: windows=scope==='week'||scope==='auto'&&remaining(quota.primary??{})===null?[['周额度',quota.secondary??{}]]:[['5 小时额度',quota.primary??{}],['周额度',quota.secondary??{}]];
 $: orb=windows[0];
 $: orbRemaining=remaining(orb?.[1]??{});
 $: waterY=100-Math.max(10,Math.min(88,orbRemaining??0));
 $: background=settings.background_transparent?`rgba(37,37,42,${Math.max(1/255,(100-Number(settings.background_opacity??70))/100)})`:'rgb(37,37,42)';
 $: status=quota.error||(quota.updated_at?(tr('上次更新：')+updatedStamp(quota.updated_at)):quota.status==='reading'?tr('正在读取'):quota.status==='stale'?tr('数据过期'):'');
</script>

<div class="quota-float" class:preview class:docked class:vertical class:single={windows.length===1} class:orb={style==='orb'&&!docked} class:classic={style==='classic'&&!docked} class:rings={style==='rings'&&!docked} class:tiles={style==='tiles'&&!docked} class:compact={style==='compact'&&!docked} class:minimal={style==='minimal'&&!docked} style:background={style==='orb'&&!docked?'transparent':background} style:border-color={settings.show_border===false?'transparent':settings.border_color??'#8AB4F8'} style:--wails-draggable={preview?'no-drag':'drag'} style:--quota-count={windows.length} draggable="false" ondragstart={event=>event.preventDefault()} role="presentation">
 {#if docked}
  {#each windows as [title,window]}{@const value=remaining(window)}<div class="dock-quota"><span>{title==='周额度'?tr('周'):tr('5 小时')}</span><strong style:color={quotaColor(value)}>{label(value)}</strong><div class="meter"><div style:width={vertical?'100%':`${Math.max(0,Math.min(100,value??0))}%`} style:height={vertical?`${Math.max(0,Math.min(100,value??0))}%`:'100%'} style:background={quotaColor(value)}></div></div></div>{/each}
 {:else if style==='orb'}
  <div class="water-orb" title={`${tr(orb[0])} · ${label(orbRemaining)} · ${tr('重置')} ${stamp(orb[1].resets_at)}`}><svg viewBox="0 0 100 100" aria-label={`${tr(orb[0])} ${label(orbRemaining)}`}><defs><clipPath id={preview?'preview-orb-clip':'orb-clip'}><circle cx="50" cy="50" r="48"/></clipPath><radialGradient id={preview?'preview-cavity':'cavity'}><stop offset="0" stop-color="#3A4854"/><stop offset=".7" stop-color="#202A34"/><stop offset="1" stop-color="#141C24"/></radialGradient><linearGradient id={preview?'preview-water':'water'} x1="0" y1="0" x2="0" y2="1"><stop stop-color="#7DF0B5"/><stop offset=".28" stop-color="#3DDC97"/><stop offset="1" stop-color="#1FA97A"/></linearGradient></defs><circle cx="50" cy="50" r="48" fill={`url(#${preview?'preview-cavity':'cavity'})`}/>{#if orbRemaining!==null}<g clip-path={`url(#${preview?'preview-orb-clip':'orb-clip'})`}><path class="orb-water" d={`M-20 ${waterY} Q5 ${waterY-6} 30 ${waterY} T80 ${waterY} T130 ${waterY} V104H-20Z`} fill={`url(#${preview?'preview-water':'water'})`}/></g>{/if}<circle cx="50" cy="50" r="47.5" fill="none" stroke="#E6F0F632" stroke-width="1.3"/><path d="M23 26Q30 14 42 13" fill="none" stroke="#FFFFFF46" stroke-width="1.2"/><text x="50" y="56" text-anchor="middle" font-size="26" fill="#F8FFFC">{label(orbRemaining)}</text></svg>{#if summary.tokens!=null}<small>{compact(summary.tokens)}<br/>{cost(summary.usd)}</small>{/if}</div>
 {:else}
  <div class="floating-quotas">{#each windows as [title,window]}{@const value=remaining(window)}<div class="floating-quota" style={`--quota-color:${quotaColor(value)}`}>
   {#if style==='rings'}<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="40" fill="none" stroke="#FFFFFF2E" stroke-width="10"/><circle cx="50" cy="50" r="40" fill="none" stroke={quotaColor(value)} stroke-width="10" stroke-linecap="round" pathLength="100" stroke-dasharray={`${Math.max(0,Math.min(100,value??0))} 100`} transform="rotate(-90 50 50)"/><text x="50" y="58" text-anchor="middle" font-size="29" fill="#F0F0F3">{value===null?'N/A':Math.round(value)}</text></svg><span>{tr(title)}</span><small>{stamp(window.resets_at)}</small>
   {:else if style==='classic'}<div class="floating-row-head"><span>{tr(title)}</span><strong>{label(value)}</strong></div><div class="meter"><div style:width={`${Math.max(0,Math.min(100,value??0))}%`}></div></div><small>{tr('重置')} {stamp(window.resets_at)}</small>
   {:else}<strong>{label(value)}</strong><span>{tr(title)}</span>{#if style!=='minimal'}<small>{stamp(window.resets_at)}</small>{/if}{/if}
  </div>{/each}</div>
  {#if !preview&&style!=='minimal'}<div class="floating-status" class:error={!!quota.error}><span class="status-time">{status}</span>{#if !quota.error&&summary.tokens!=null}<span>{compact(summary.tokens)} Token {cost(summary.usd)}</span>{/if}</div>{/if}
 {/if}
</div>

<style>
 .quota-float,.quota-float *{box-sizing:border-box;user-select:none;-webkit-user-drag:none}
 .quota-float{--float-radius:16px;--text:#F0F0F3;--muted:#C2C2CA;--quota-color:#7DDEA0;display:grid;grid-template-rows:minmax(0,1fr) auto;gap:8px;width:100%;height:100%;min-width:0;min-height:0;padding:12px 14px;color:var(--text);font-family:inherit;font-size:12px;line-height:1.35;border:1px solid;border-radius:var(--float-radius);clip-path:inset(0 round var(--float-radius));overflow:hidden;box-shadow:none}
 .quota-float.preview{align-self:flex-start;flex-shrink:0;width:320px;max-width:100%;height:180px;grid-template-rows:minmax(0,1fr)}
 .quota-float.preview.rings{height:200px}.quota-float.preview.minimal{height:96px}
 .floating-quotas{display:grid;grid-template-columns:repeat(var(--quota-count),minmax(0,1fr));align-items:stretch;gap:12px;min-height:0;min-width:0;margin:0}
 .floating-quota{min-width:0;min-height:0;display:flex;flex-direction:column;justify-content:center;align-items:stretch;gap:4px;text-align:center}
 .classic .floating-quotas{grid-template-columns:1fr;grid-template-rows:repeat(var(--quota-count),minmax(0,1fr))}
 .classic .floating-quota{text-align:left}
 .floating-row-head{display:flex;align-items:center;justify-content:space-between;gap:8px}
 .floating-quota strong{font-size:28px;font-weight:450;line-height:1.2;color:var(--quota-color);white-space:nowrap}
 .classic .floating-quota strong,.compact .floating-quota strong{font-size:22px}
 .floating-quota>span,.floating-row-head>span{font-size:12px;line-height:17px;flex-shrink:0;margin:0}
 .floating-quota small{font-size:10px;line-height:14px;flex-shrink:0;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .meter{height:7px;flex-shrink:0;width:100%;margin:0;border-radius:4px;background:#FFFFFF30;overflow:hidden}
 .meter>div{height:100%;border-radius:inherit;background:var(--quota-color)}
 .rings .floating-quota{display:grid;grid-template-rows:minmax(56px,1fr) auto auto;gap:5px}
 .rings .floating-quota svg{display:block;width:100%;height:100%;min-height:0;max-height:112px;margin:0 auto}
 .floating-status{display:grid;grid-template-columns:minmax(0,1fr);gap:2px;font-size:10px;line-height:13px;color:var(--muted);min-width:0}
 .floating-status>span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .floating-status.error{color:#FFAAA3}
 .tiles .floating-quota{background:#FFFFFF0A;border:1px solid #FFFFFF20;border-radius:10px;padding:8px}
 .minimal{grid-template-rows:minmax(0,1fr)}.minimal .floating-quotas{gap:8px}
 .quota-float.orb{padding:0;border:0;background:transparent!important;display:grid;grid-template-rows:minmax(0,1fr);place-items:center;clip-path:ellipse(48% 48% at 50% 50%)}
 .quota-float.orb.preview{width:148px;height:148px}
 .water-orb{width:min(100%,100vh);height:100%;max-width:100%;aspect-ratio:1;position:relative}
 .water-orb svg{height:100%;width:100%;display:block}
 .water-orb small{position:absolute;left:0;right:0;top:64%;text-align:center;font-size:10px;line-height:1.2;color:#F2FFF9}
 .orb-water{animation:water-wave 3s ease-in-out infinite;transform-origin:center}
 @keyframes water-wave{0%,100%{transform:translateX(0)}50%{transform:translateX(-10px)}}
 .quota-float.docked{--float-radius:14px;grid-template-columns:repeat(var(--quota-count),minmax(0,1fr));grid-template-rows:minmax(0,1fr);padding:7px 10px;gap:16px}
 .dock-quota{display:flex;align-items:center;gap:7px;min-width:0;min-height:0}
 .dock-quota>span{font-size:10px;color:var(--muted);white-space:nowrap}
 .dock-quota>strong{font-size:16px;font-weight:450;line-height:1.2;white-space:nowrap}
 .dock-quota .meter{flex:1;min-width:0;height:5px}
 .quota-float.docked.vertical{--float-radius:13px;container-type:inline-size;grid-template-columns:minmax(0,1fr);grid-template-rows:repeat(var(--quota-count),minmax(0,1fr));padding:10px 5px;gap:14px}
 .vertical .dock-quota{flex-direction:column;justify-content:flex-start;gap:7px}
 .vertical .dock-quota>span{font-size:9px}.vertical .dock-quota>strong{font-size:clamp(8px,30cqw,12px)}
 .vertical .dock-quota .meter{flex:1;min-height:28px;width:5px;height:auto;display:flex;align-items:flex-end}
 @media(prefers-reduced-motion:reduce){.orb-water{animation:none}}
</style>
