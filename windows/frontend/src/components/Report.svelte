<script lang="ts">
  import { tick, onMount, onDestroy } from 'svelte';
  import { api, rows, text, numeric, number, type Row } from '../lib/api';
  import { tr } from '../lib/i18n';
  import { toPng } from 'html-to-image';
  import Icon from './Icon.svelte';
  import {modelName} from './logFormat';
  export let initialPeriod = 'day';
  export let settings: Row = {};
  export let onsave: (r: Row) => Promise<void>;
  export let onclose: () => void;
  export let onerror: (e: any) => void;
  export let onpresent: () => void;

  const palettes: Record<string, string[]> = {
    garden: ['#FFFDF7','#173C31','#4C6959','#7CA88B','#C7DCC8','#F1BAA1','#F1F7EE'],
    bookmark: ['#FFFBF4','#4E4032','#78634D','#C99B65','#E8D7AF','#EBAF86','#F7EFDD'],
    afternoon: ['#FFFCF7','#493027','#785746','#DA855A','#F2C5AA','#D67650','#FEF1E8']
  };
  let period = initialPeriod;
  let style = palettes[settings.usage_report_style] ? settings.usage_report_style : 'garden';
  let data: Row = {};
  let busy = false, exporting = false, savingStyle = false, presented = false;
  let sequence = 0, exported = '', error = '';
  let card: HTMLElement, reader: HTMLElement, dialog: HTMLDivElement, closeButton: HTMLButtonElement;
  let readerWidth = 480, readerHeight = 800, scrollTop = 0;
  $: void load(period);
  $: summary = data.summary ?? {};
  $: top = rows(data.models).slice(0, 3);
  $: slices = rows(data.time_slices).slice(0, 4);
  $: peak = slices.filter(s => Number(s.requests) > 0).reduce<Row | null>((best, s) => !best || Number(s.requests) > Number(best.requests) ? s : best, null);
  $: biggest = Math.max(1, ...slices.map(s => Number(s.requests) || 0));
  $: paper = palettes[style];
  $: height = Math.max(820, 952 - Math.max(0, 3 - top.length) * 50);
  $: scale = Math.max(.38, Math.min(.92, (readerWidth - 24) / 500, (readerHeight - 12) / height));
  $: atBottom = scrollTop + readerHeight >= height * scale + 12 - 8;
  $: word = tr(period === 'day' ? '昨天' : period === 'week' ? '上周' : '上月');
  $: headline = style === 'bookmark' ? `${word}，\n${tr('最常陪你思考的是它')}` : peak ? `${word}，${tr(String(peak.name))}${tr('最热闹')}` : `${word}${tr('没有请求记录')}`;
  $: insight = peak ? `${word}${tr('的请求在')}${tr(String(peak.name))}${tr('最多，共 ')}${numeric(peak.requests)}${tr(' 次。')}` : `${word}${tr('没有请求记录')}`;
  $: calls = summary.call_count ?? summary.requests;
  $: firstDate = date(data.first);
  $: lastDate = date(data.last);
  $: metrics = [[costLabel(summary), tr('费用')], [tokenLabel(summary.tokens, summary.tokens_complete !== false && Number(summary.skipped?.tokens ?? 0) === 0), tr('总 Token')], [numeric(summary.user_requests), tr('用户请求')], [number(summary.cache_hit_rate) === null ? '—' : `${Math.round(Number(summary.cache_hit_rate) * 100)}%`, tr('命中率')]];

  onMount(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const observer = new ResizeObserver(entries => {
      const box = entries[0]?.contentRect;
      if (box) { readerWidth = box.width; readerHeight = box.height; }
    });
    observer.observe(reader);
    closeButton.focus();
    return () => { observer.disconnect(); if (previousFocus?.isConnected) previousFocus.focus(); };
  });
  onDestroy(() => { sequence++; });
  async function load(p: string) {
    const ticket = ++sequence;
    busy = true; error = ''; data = {}; exported = '';
    if (reader) reader.scrollTop = 0;
    try {
      const next = await api('GetReports', p);
      if (ticket !== sequence) return;
      data = next;
      await tick();
      if (ticket === sequence && !presented) { presented = true; onpresent(); }
    } catch (e) { if (ticket === sequence) { error = String(e); onerror(e); } }
    finally { if (ticket === sequence) busy = false; }
  }
  async function selectStyle(value: string) {
    const previous = style;
    style = value; exported = ''; savingStyle = true;
    if (reader) reader.scrollTop = 0;
    try { await onsave({ usage_report_style: value }); }
    catch (e) { style = previous; onerror(e); }
    finally { savingStyle = false; }
  }
  async function share() {
    if (!card || busy || exporting) return;
    exporting = true;
    const ticket = sequence, selectedPeriod = period, selectedPaper = paper[6], cardHeight = height;
    try {
      await document.fonts.ready;
      // The live reader is scaled. Export the entire unscaled Mac artwork, at 2x.
      const png = await toPng(card, { width: 500, height: cardHeight, pixelRatio: 2, cacheBust: false, backgroundColor: selectedPaper, style: { transform: 'none', width: '500px', height: `${cardHeight}px`, maxWidth: 'none', maxHeight: 'none', overflow: 'visible' } });
      if (ticket !== sequence) return;
      const saved = await api('SaveReportPNG', png, selectedPeriod);
      if (ticket === sequence) exported = text(saved.path, '');
    } catch (e) { if (ticket === sequence) onerror(e); }
    finally { exporting = false; }
  }
  function date(value: any): Date | null {
    if (value === null || value === undefined || value === '') return null;
    const n = number(value), d = new Date(n === null ? value : n < 1e12 ? n * 1000 : n);
    return Number.isFinite(d.getTime()) ? d : null;
  }
  function clock(d: Date | null) { return d ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false }) : '—'; }
  function timeNote(d: Date, first: boolean) {
    const h = d.getHours();
    return tr(first ? h >= 5 && h < 9 ? '早早开工，继续加油喵' : '按自己的节奏来，喵' : h >= 22 || h < 5 ? '辛苦啦，早点休息喵' : '努力收好，好好放松喵');
  }
  function costLabel(row: Row) {
    const n = number(row.usd ?? row.cost_usd);
    return n === null ? '—' : (row.cost_complete === false || Number(row.unpriced_calls) > 0 || row.pricing_status === 'partial' ? '≥' : '') + '$' + n.toFixed(2);
  }
  function tokenLabel(value: any, complete = true) {
    const n = number(value);
    if (n === null) return '—';
    const en = document.documentElement.lang === 'en';
    const [unit, suffix] = en ? n >= 1e9 ? [1e9, 'B'] : n >= 1e6 ? [1e6, 'M'] : n >= 1e3 ? [1e3, 'K'] : [1, ''] : n >= 1e8 ? [1e8, '亿'] : n >= 1e4 ? [1e4, '万'] : [1, ''];
    return (complete ? '' : '≥') + (n / Number(unit)).toFixed(unit === 1 ? 0 : 2).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '') + suffix;
  }
  function sharePercent(amount: any, total: any) { return Number(total) > 0 ? Math.round(Number(amount) / Number(total) * 100) : 0; }
  // Match ReportArtSmallMetric's one-line 0.65 minimum scale with actual font
  // glyph widths; character counts are unreliable for Chinese token suffixes.
  function fitMetric(node: HTMLElement, value: string) {
    let current = value, active = true;
    const context = document.createElement('canvas').getContext('2d');
    const fit = () => {
      if (!active || !context || !node.isConnected) return;
      const style = getComputedStyle(node);
      context.font = `${style.fontWeight} 27px ${style.fontFamily}`;
      const natural = context.measureText(current).width;
      node.style.fontSize = `${Math.max(17.55, Math.min(27, natural > 0 ? 27 * Math.max(1, node.clientWidth - 12) / natural : 27))}px`;
    };
    const observer = new ResizeObserver(fit); observer.observe(node);
    queueMicrotask(fit); void document.fonts.ready.then(fit);
    return { update(value: string) { current = value; queueMicrotask(fit); }, destroy() { active = false; observer.disconnect(); } };
  }
  function keydown(event: KeyboardEvent) {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onclose(); }
    if (event.key !== 'Tab') return;
    const buttons = Array.from(dialog.querySelectorAll<HTMLElement>('button:not(:disabled), select:not(:disabled), [tabindex="0"]'));
    const first = buttons[0], last = buttons[buttons.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }
</script>

<div class="mac-report-backdrop">
  <div bind:this={dialog} class="mac-report-window" role="dialog" aria-modal="true" aria-label={tr('AI 使用报告')} tabindex="-1" onkeydown={keydown} style={`--mac-paper:${paper[0]};--mac-ink:${paper[1]};--mac-muted:${paper[2]};--mac-accent:${paper[3]};--mac-secondary:${paper[4]};--mac-warm:${paper[5]};--mac-surface:${paper[6]}`}>
    <div bind:this={reader} class="mac-report-reader" aria-busy={busy} onscroll={() => { scrollTop = reader.scrollTop; }}>
      {#if data.summary}
        <div class="mac-report-scaled" style:width={`${500 * scale}px`} style:height={`${height * scale}px`}>
          <article bind:this={card} class="mac-report-card" style:height={`${height}px`} style:transform={`scale(${scale})`}>
            <svg class="mac-report-paper" viewBox={`0 0 500 ${height}`} aria-hidden="true">
              <path d={`M18 80C16 58 28 10 42 19C57 11 96 67 123 75Q250 53 377 75C404 67 443 11 458 19C472 10 484 58 482 80L482 ${height-62}Q480 ${height-18} 428 ${height-18}L72 ${height-18}Q20 ${height-18} 18 ${height-62}Z`} fill={paper[0]} stroke={paper[3]} stroke-opacity=".15"/>
              <path d="M37 70Q39 40 48 34Q63 36 93 72ZM463 70Q461 40 452 34Q437 36 407 72Z" fill={paper[5]} opacity={style === 'bookmark' ? .11 : .28}/>
              {#if style === 'bookmark'}<path d={`M447 ${height*.19}C475 ${height*.29} 410 ${height*.43} 456 ${height*.58}C480 ${height*.72} 478 ${height-38} 431 ${height-70}C389 ${height-99} 384 ${height-50} 413 ${height-55}`} fill="none" stroke={paper[1]} stroke-opacity=".72" stroke-width="3" stroke-linecap="round"/>{/if}
            </svg>
            <div class="mac-report-content">
              <header class="mac-report-header"><img src="/ReportCards/brand-mark.png" alt=""/><div><strong>Codexio</strong><span>{tr(period === 'day' ? 'AI日报' : period === 'week' ? 'AI周报' : 'AI月报')}</span></div><time>{text(data.date_label)}</time></header>
              <div class="mac-report-rule mac-report-header-rule"></div>
              <section class="mac-report-hero">
                <div class="mac-report-hero-copy"><h1 style:font-size={style === 'bookmark' ? '41px' : '43px'}>{headline}</h1>
                  {#if peak}<div class="mac-report-peak"><strong>{numeric(peak.requests)} / {numeric(summary.user_requests)}</strong><span>{tr('次请求')}</span></div><p>{tr(String(peak.name))} · {sharePercent(peak.requests, summary.user_requests)}%</p>{/if}
                </div><img src={`/ReportCards/cat-${style}.png`} alt=""/>
              </section>
              <div class="mac-report-rule"></div>
              <div class="mac-report-metrics">{#each metrics as [value, label]}<div><strong use:fitMetric={String(value)}>{value}</strong><span>{label}</span></div>{/each}</div>
              <div class="mac-report-rule"></div>
              <section class="mac-report-rhythm">
                <div class="mac-report-heading"><h2>{tr('使用节奏')}</h2><small>{peak ? tr(String(peak.name)) + tr('最活跃') : ''}</small></div>
                <div class="mac-report-slices">{#each slices as slice}<div><strong>{numeric(slice.requests)}</strong><span style:height={`${Number(slice.requests) === 0 ? 1 : Math.max(3, Number(slice.requests) / biggest * 52)}px`} style:background={Number(slice.requests) === Number(peak?.requests) ? paper[3] : paper[4]}></span><small>{tr(String(slice.name))}</small></div>{/each}</div>
                <div class="mac-report-times"><div><span>{tr('最早')} · {clock(firstDate)}</span>{#if firstDate}<small>{timeNote(firstDate, true)}</small>{/if}</div><div><span>{tr('最晚')} · {clock(lastDate)}</span>{#if lastDate}<small>{timeNote(lastDate, false)}</small>{/if}</div></div>
              </section>
              <div class="mac-report-rule mac-report-rhythm-rule"></div>
              <section class="mac-report-models">
                <div class="mac-report-heading"><h2>{tr('模型使用')}</h2><small>{numeric(calls)} {tr('次调用')}</small></div>
                {#each top as model}<div class="mac-report-model-row"><div><strong>{modelName(model.model)}</strong><small>{costLabel(model)} · {tokenLabel(model.tokens, model.tokens_complete !== false && Number(model.skipped?.tokens ?? 0) === 0)} Token</small></div><span>{numeric(model.call_count ?? model.calls ?? model.requests)} {tr('次')} · {sharePercent(model.call_count ?? model.calls ?? model.requests, calls)}%</span></div>{/each}
                {#if !top.length}<p>{tr('暂无模型调用')}</p>{/if}
              </section>
              <div class="mac-report-spacer"></div>
              <div class="mac-report-insight"><svg width="18" height="20" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><ellipse cx="5" cy="8" rx="2.6" ry="3.6" transform="rotate(-25 5 8)"/><ellipse cx="10" cy="4.5" rx="2.5" ry="3.5"/><ellipse cx="16" cy="5" rx="2.5" ry="3.5" transform="rotate(15 16 5)"/><ellipse cx="21" cy="10" rx="2.5" ry="3.5" transform="rotate(25 21 10)"/><path d="M5 17c0-3 4-7 7-7s7 4 7 7c0 5-4 3-7 3s-7 2-7-3"/></svg><p>{tr('小猫发现：')}{insight}</p></div>
              <footer>{tr('专注 · 与 AI 共成长')}</footer>
            </div>
          </article>
        </div>
      {:else}<div class="mac-report-empty">{busy ? tr('小猫正在整理报告…') : error || tr('暂无报告数据')}</div>{/if}
    </div>
    <aside class="mac-report-controls">
      <button bind:this={closeButton} class="mac-report-close" onclick={onclose} aria-label={tr('关闭报告')}><Icon name="close" size={14}/></button>
      <div class="mac-report-periods">{#each [['day','日报'],['week','周报'],['month','月报']] as [p,label]}<button class:mac-report-selected={period === p} aria-pressed={period === p} disabled={exporting} onclick={() => { period = p; }}>{tr(label)}</button>{/each}</div>
      <label class="mac-report-style"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M12 3a9 9 0 1 0 0 18h1c3 0 3-4 1-5-1-1 0-3 2-3h2c6 0 3-10-6-10Z"/><circle cx="7" cy="9" r="1"/><circle cx="11" cy="6" r="1"/><circle cx="16" cy="7" r="1"/></svg><span>{tr('样式')}</span><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 9 7 6 7-6"/></svg><select aria-label={tr('样式')} value={style} disabled={savingStyle || exporting} onchange={e => selectStyle(e.currentTarget.value)}>{#each [['bookmark','奶油猫尾书签'],['garden','薄荷猫咪花园'],['afternoon','杏色时段图']] as [s,label]}<option value={s}>{tr(label)}</option>{/each}</select></label>
      <div class="mac-report-controls-spacer"></div>
      {#if !atBottom && data.summary}<div class="mac-report-scroll-hint"><img src={`/ReportCards/cat-${style}.png`} alt=""/><span>{tr('下滑看完整报告')}</span></div>{/if}
      <button class="mac-report-share" disabled={busy || exporting || !data.summary} onclick={share}><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M8 8H4v13h16V8h-4M12 15V2m-4 4 4-4 4 4"/></svg>{tr(exporting ? '处理中' : '分享')}</button>
      <p class="mac-report-caption">{tr('把小进展分享出去，喵')}</p>
      {#if exported}<p class="mac-report-exported" role="status" title={exported}>{tr('导出完成')}</p>{/if}
    </aside>
  </div>
</div>

<style>
  .mac-report-backdrop{position:fixed;inset:0;z-index:50;display:flex;align-items:center;justify-content:center;padding:12px;background:#0005}
  .mac-report-window{--mac-editorial:'Noto Serif SC',serif;display:flex;width:clamp(530px,calc((100vh - 12px)*.55 + 160px),620px);height:min(820px,calc(100vh - 24px));max-width:100%;overflow:hidden;border-radius:12px;background:var(--mac-surface);color:var(--mac-ink);box-shadow:0 12px 45px #0004;font-family:'Noto Sans SC','Segoe UI',sans-serif;font-size:13px;line-height:1.3;color-scheme:light}
  .mac-report-reader{flex:1;min-width:0;overflow-y:auto;overflow-x:hidden;padding:6px 0;scrollbar-width:thin}
  .mac-report-scaled{position:relative;margin:0 auto;flex:none}
  .mac-report-card{position:absolute;left:0;top:0;width:500px;max-width:none;transform-origin:top left;text-align:left;color:var(--mac-ink);font-family:'Noto Sans SC','Segoe UI',sans-serif;line-height:1.3;font-variant-numeric:tabular-nums}
  .mac-report-paper{position:absolute;inset:0;width:100%;height:100%;overflow:visible;filter:drop-shadow(0 12px 23px color-mix(in srgb,var(--mac-ink) 14%,transparent));pointer-events:none}
  .mac-report-content{position:relative;padding:76px 42px 42px;height:100%;display:flex;flex-direction:column}
  .mac-report-content h1,.mac-report-content h2,.mac-report-content p{margin:0;color:inherit}
  .mac-report-header{display:flex;align-items:center;gap:10px;flex:none}
  .mac-report-header>img{width:36px;height:36px;object-fit:contain}
  .mac-report-header>div{display:flex;flex-direction:column;gap:1px}
  .mac-report-header strong{font-family:var(--mac-editorial);font-size:27px;font-weight:600;line-height:1.2}
  .mac-report-header span{font-size:12px;font-weight:500;letter-spacing:3px;color:var(--mac-muted)}
  .mac-report-header time{margin-left:auto;font-size:12px;color:var(--mac-muted);white-space:nowrap}
  .mac-report-rule{height:1px;flex:none;background:color-mix(in srgb,var(--mac-accent) 40%,transparent)}
  .mac-report-header-rule{margin-top:16px}
  .mac-report-hero{height:150px;min-height:150px;position:relative;margin:10px 0;display:flex;align-items:center}
  .mac-report-hero-copy{width:100%;position:relative;z-index:1;display:flex;flex-direction:column;gap:7px}
  .mac-report-hero h1{font-family:var(--mac-editorial);font-weight:600;line-height:1.18;white-space:pre-line;letter-spacing:-.4px}
  .mac-report-peak{display:flex;align-items:baseline;gap:6px}
  .mac-report-peak strong{font-family:var(--mac-editorial);font-size:32px;font-weight:600}
  .mac-report-peak span{font-size:12px}
  .mac-report-hero-copy>p{font-size:14px;font-weight:500;color:var(--mac-accent)}
  .mac-report-hero>img{position:absolute;bottom:0;right:3px;width:100px;height:75px;object-fit:contain}
  .mac-report-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));padding:18px 0;flex:none}
  .mac-report-metrics>div{position:relative;text-align:center;display:flex;flex-direction:column;align-items:center;gap:4px;min-width:0}
  .mac-report-metrics>div+div::before{content:'';position:absolute;left:0;top:50%;height:35px;width:1px;transform:translateY(-50%);background:color-mix(in srgb,var(--mac-secondary) 65%,transparent)}
  .mac-report-metrics strong{width:100%;height:36px;display:flex;align-items:flex-end;justify-content:center;font-family:var(--mac-editorial);font-size:27px;font-weight:600;white-space:nowrap;line-height:1.1}
  .mac-report-metrics span{font-size:11px;color:var(--mac-muted)}
  .mac-report-rhythm{padding-top:20px;flex:none;display:flex;flex-direction:column;gap:10px}
  .mac-report-heading{display:flex;align-items:center;justify-content:space-between;gap:8px}
  .mac-report-heading h2{font-family:var(--mac-editorial);font-size:25px;font-weight:600;line-height:1.3}
  .mac-report-heading>small{font-size:11px;color:var(--mac-accent);font-weight:500}
  .mac-report-slices{height:82px;display:flex;align-items:flex-end;gap:14px}
  .mac-report-slices>div{flex:1;display:flex;flex-direction:column;align-items:stretch;gap:3px;text-align:center}
  .mac-report-slices strong{font-family:var(--mac-editorial);font-size:12px;font-weight:600}
  .mac-report-slices span{display:block;border-radius:5px}
  .mac-report-slices small{font-size:11px;color:var(--mac-muted)}
  .mac-report-times{display:flex;justify-content:space-between;gap:10px;font-size:10px}
  .mac-report-times>div{display:flex;flex-direction:column;gap:1px}
  .mac-report-times>div:last-child{text-align:right}
  .mac-report-times span{font-weight:500}
  .mac-report-times small{color:var(--mac-muted);font-size:10px;white-space:nowrap}
  .mac-report-rhythm-rule{margin-top:20px}
  .mac-report-models{display:flex;flex-direction:column;gap:16px;padding-top:18px;flex:none}
  .mac-report-models h2{font-size:26px}
  .mac-report-models .mac-report-heading>small{color:var(--mac-muted);font-weight:400}
  .mac-report-model-row{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
  .mac-report-model-row>div{min-width:0;display:flex;flex-direction:column;gap:1px}
  .mac-report-model-row strong{font-size:15px;font-weight:500;line-height:1.6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .mac-report-model-row small{font-size:12px;color:var(--mac-muted);white-space:nowrap}
  .mac-report-model-row>span{font-size:13px;white-space:nowrap}
  .mac-report-models>p{font-size:10px;color:var(--mac-muted)}
  .mac-report-spacer{flex:1;min-height:20px}
  .mac-report-insight{display:flex;align-items:flex-start;gap:10px;padding:14px;background:var(--mac-surface);border-radius:18px;flex:none}
  .mac-report-insight>svg{color:var(--mac-warm);flex:none}
  .mac-report-insight>p{font-size:13px;line-height:1.5}
  .mac-report-content footer{text-align:center;padding-top:14px;font-size:10px;letter-spacing:3px;color:var(--mac-muted);flex:none}
  .mac-report-controls{width:116px;flex:none;padding:14px 14px 14px 6px;display:flex;align-items:center;gap:14px;flex-direction:column}
  .mac-report-controls button{color:var(--mac-ink);font-family:inherit;box-shadow:none;flex:none;justify-content:center}
  .mac-report-close{width:30px;height:30px;min-height:30px;padding:0;align-self:flex-end;background:transparent;border:0;display:flex;align-items:center}
  .mac-report-periods{width:96px;display:flex;flex-direction:column;gap:5px;padding:4px;border-radius:16px;background:color-mix(in srgb,var(--mac-secondary) 35%,transparent)}
  .mac-report-periods button{border:0;width:100%;height:36px;padding:0;border-radius:12px;font-size:13px;font-weight:500;background:transparent}
  .mac-report-periods button.mac-report-selected{background:var(--mac-ink)!important;color:var(--mac-paper)!important}
  .mac-report-style{position:relative;display:flex;align-items:center;justify-content:center;gap:4px;font-size:12px;line-height:1.6;min-height:24px}
  .mac-report-style>svg{display:block;flex:none}
  .mac-report-style select{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%;min-height:0;padding:0}
  .mac-report-style:focus-within{outline:2px solid var(--mac-accent);outline-offset:3px;border-radius:4px}
  .mac-report-controls-spacer{flex:1;min-height:0}
  .mac-report-scroll-hint{display:flex;flex-direction:column;align-items:center;gap:14px;font-size:10px;text-align:center}
  .mac-report-scroll-hint img{width:52px;height:34px;object-fit:contain}
  .mac-report-share{width:102px;max-width:none;height:31px;min-height:31px;padding:0 10px;display:flex;align-items:center;gap:5px;font-size:12px;border:1px solid #0002;border-radius:6px;background:#fff9;box-shadow:0 1px 2px #0001!important}
  .mac-report-caption,.mac-report-exported{font-size:10px;text-align:center;line-height:1.4;margin:0}
  .mac-report-empty{height:100%;display:flex;align-items:center;justify-content:center;font-size:13px;padding:20px;text-align:center}
</style>

