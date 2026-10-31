/* Production PA workspace. All user records are read and written through Store. */
(() => {
  'use strict';
  const $ = (selector, parent = document) => parent.querySelector(selector);
  const $$ = (selector, parent = document) => Array.from(parent.querySelectorAll(selector));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icons = {
    home:'<path d="m3 11 9-8 9 8M5 10v11h5v-7h4v7h5V10"/>',
    trial:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    library:'<path d="M4 4c2-1 5-1 8 1 3-2 6-2 8-1v15c-2-1-5-1-8 1-3-2-6-2-8-1zM12 5v15"/>',
    universe:'<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="11" ry="4.5" transform="rotate(-28 12 12)"/><circle cx="12" cy="12" r="2"/>',
    growth:'<path d="M3 19V5m0 14h18M6 15l5-5 4 3 5-8m-4 0h4v4"/>',
    search:'<circle cx="11" cy="11" r="7"/><path d="m16.5 16.5 4 4"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',minus:'<path d="M5 12h14"/>',
    close:'<path d="m6 6 12 12M6 18 18 6"/>',back:'<path d="m15 18-6-6 6-6"/>',
    upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 16v4h16v-4"/>',
    paste:'<rect x="5" y="5" width="15" height="17" rx="2"/><path d="M9 5V2h7v3M9 11h7M9 15h7"/>',
    image:'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="m3 17 5-5 4 4 4-6 5 7"/><circle cx="8" cy="8" r="1"/>',
    zoom:'<circle cx="10" cy="10" r="7"/><path d="M10 7v6M7 10h6m8 11-6-6"/>',
    arrow:'<path d="M5 12h14m-6-6 6 6-6 6"/>',filter:'<path d="M3 5h18M6 12h12M9 19h6"/>',
    note:'<path d="M4 4h16v13H9l-5 4z"/>',edit:'<path d="m4 20 4-.7L19 8a2 2 0 0 0-3-3L5 16z"/>',
    layers:'<path d="m12 3 9 5-9 5-9-5zM3 12l9 5 9-5M3 16l9 5 9-5"/>',
    check:'<path d="m5 12 4 4L19 6"/>',pin:'<path d="M12 21V10M5 9l3-3V3h8v3l3 3z"/>',
    lock:'<rect x="4" y="10" width="16" height="12" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>'
  };
  const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.image}</svg>`;
  const navItems = [['home','首页'],['trial','PA 交易试炼'],['library','案例研究'],['universe','知识宇宙'],['growth','个人成长']];
  const S = {
    ready:false, page:'home', boot:{}, cases:[], topics:[], stats:{}, trials:[], records:[],
    query:'', filters:{symbol:'',timeframe:'',market:'',data_kind:''}, filterOpen:false,
    searchSeq:0, searchTimer:null, searchError:'', searchBusy:false, hasMore:false, case:null, imageIndex:0,
    studyPane:'memory', returnPage:'library', returnScroll:0, overlay:null, pending:[],
    captureCaseId:null, captureMetadata:{}, captureGroup:false, captureError:'', duplicateAllowed:false,
    saving:0, staging:0, draftText:{}, original:null, topicId:null, topicParent:null, topicCases:[], topicSeq:0, topicLoading:false,
    trialCase:null, trialDepth:'quick', judgment:'', trialThought:'', marks:[], marking:false,
    trialRecord:null, trialReview:{}, context:null, dirtyMetadata:false, bootFailed:false,
    caseSeq:0, imageError:null,
  };
  const originalCache = new Map();
  const ORIGINAL_CACHE_BYTES = 64 * 1024 * 1024;
  let originalCacheBytes = 0, imageWanted = null, imageWorker = false;
  let bridgeResolve;
  const bridgeReady = new Promise(resolve => { bridgeResolve = resolve; });
  function resolveBridge() { if (window.pywebview?.api) bridgeResolve(); }
  window.addEventListener('pywebviewready', resolveBridge);
  resolveBridge();
  async function api(method, ...args) {
    await bridgeReady;
    const fn = window.pywebview.api[method];
    if (typeof fn !== 'function') throw new Error('当前窗口未能连接此操作，请重新打开成长空间。');
    const response = await fn(...args);
    if (!response || response.ok === false) throw new Error(response?.error || '操作未完成，请重试。');
    return response;
  }
  function toast(message, error = false) {
    const node = document.createElement('div');
    node.className = `toast${error ? ' error' : ''}`;
    node.textContent = message;
    $('#toast-zone').append(node);
    setTimeout(() => node.remove(), error ? 7500 : 4300);
  }
  function date(value) {
    if (!value) return '';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('zh-CN', {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  }
  const caseTitle = c => c?.title || c?.memory || c?.symbol || '未命名案例';
  const sourceLabel = c => c?.data_kind === 'test' ? '测试样本' : '个人导入';
  const summary = c => [c.symbol,c.timeframe,c.market].filter(Boolean).join(' · ') || sourceLabel(c);
  const notesOf = c => Array.isArray(c?.notes) ? c.notes : [];
  function imageOf(c, index = 0) {
    return c?.images?.[index] || (c?.thumbnail ? {data_url:c.thumbnail} : null);
  }
  function imageUrl(image) {
    const value = image?.data_url || image?.thumbnail_data_url || image?.thumbnail || originalCache.get(image?.id)?.data_url || '';
    return /^data:image\/(png|jpeg|webp|gif|bmp);base64,[A-Za-z0-9+/=\s]+$/i.test(value) ? value : '';
  }
  function thumbOf(c) {
    return imageUrl({data_url:c?.thumbnail_data_url || c?.thumbnail || c?.image_data_url}) || imageUrl(imageOf(c));
  }
  function topicName(id) { return S.topics.find(topic => topic.id === id)?.name || id; }
  function imageTarget() {
    const c = S.original?.case || (S.page === 'study' ? S.case : S.page === 'trial' ? S.trialCase : null);
    const index = S.original ? S.original.index : S.imageIndex;
    const image = imageOf(c,index);
    return c?.id && image?.id ? {case:c,image,key:`${c.id}/${image.id}`} : null;
  }
  function imageMessage(image) {
    return S.imageError?.id === image?.id ? S.imageError.text : '正在读取原图…';
  }
  function rememberOriginal(image) {
    // These are ASCII data URLs, bounded by encoded payload bytes and count.
    // Case metadata never retains a second copy or every original in a gallery.
    const previous = originalCache.get(image.id);
    if (previous) { originalCacheBytes -= previous.data_url.length; originalCache.delete(image.id); }
    while (originalCache.size && (originalCache.size >= 2 || originalCacheBytes + image.data_url.length > ORIGINAL_CACHE_BYTES)) {
      const oldest = originalCache.keys().next().value;
      originalCacheBytes -= originalCache.get(oldest).data_url.length; originalCache.delete(oldest);
    }
    if (image.data_url.length > ORIGINAL_CACHE_BYTES) throw new Error('原图过大，无法在当前窗口预览。');
    originalCache.set(image.id,image); originalCacheBytes += image.data_url.length;
  }
  function paintVisibleImage(key) {
    const target = imageTarget();
    if (!target || target.key !== key) return;
    const src = imageUrl(target.image);
    if (S.original) { renderOriginal(); return; }
    if (S.page === 'study') {
      const panel = $('.study-image');
      if (panel) panel.innerHTML = src ? `<img src="${esc(src)}" alt="${esc(target.image.name || caseTitle(target.case))}">` : `<span class="muted">${esc(imageMessage(target.image))}</span>`;
    } else if (S.page === 'trial') {
      const panel = $('#trial-image-wrap');
      if (panel) panel.innerHTML = `${src ? `<img id="trial-image" src="${esc(src)}" alt="试炼截图">` : `<span class="muted">${esc(imageMessage(target.image))}</span>`}<div class="mark-layer" id="trial-mark-layer"></div>`;
      const save = $('[data-action="save-judgment"]');
      if (save) save.disabled = !S.judgment || !src;
      renderMarks();
    }
  }
  function requestVisibleImage() {
    const target = imageTarget();
    if (!target || imageUrl(target.image) || S.imageError?.id === target.image.id) return;
    imageWanted = target;
    if (imageWorker) return;
    imageWorker = true;
    (async () => {
      try {
        while (imageWanted) {
          const wanted = imageWanted; imageWanted = null;
          if (imageTarget()?.key !== wanted.key || imageUrl(wanted.image)) continue;
          try {
            const result = await api('get_image',wanted.case.id,wanted.image.id,false);
            if (imageTarget()?.key !== wanted.key) continue;
            if (result.image?.id !== wanted.image.id || !imageUrl(result.image)) throw new Error('未能读取原图，请重试。');
            rememberOriginal(result.image); S.imageError = null;
          } catch (error) {
            if (imageTarget()?.key !== wanted.key) continue;
            S.imageError = {id:wanted.image.id,text:error.message};
          }
          paintVisibleImage(wanted.key);
        }
      } finally { imageWorker = false; }
    })();
  }
  function canLeaveDraft() {
    return S.saving === 0 && S.staging === 0 && S.pending.length === 0 && !S.dirtyMetadata &&
      !Object.values(S.draftText).some(value => String(value || '').trim()) &&
      !S.trialThought.trim() && !Object.values(S.trialReview).some(value => String(value || '').trim()) &&
      !(S.judgment && !S.trialRecord) && !(S.marks.length && !S.trialRecord);
  }
  window.aplusGrowthCanClose = canLeaveDraft;
  window.aplusGrowthBusy = () => S.saving > 0 || S.staging > 0;
  window.addEventListener('beforeunload', event => {
    if (!canLeaveDraft()) { event.preventDefault(); event.returnValue = ''; }
  });
  async function boot() {
    try {
      const data = await api('bootstrap');
      S.boot = data;
      S.cases = data.cases || [];
      S.hasMore = data.has_more === true;
      S.topics = data.topics || [];
      S.stats = data.stats || {};
      S.trials = data.trials || [];
      S.records = data.records || [];
      S.context = data.context || null;
      if (S.context?.code || S.context?.symbol) S.page = 'library';
      S.ready = true;
      S.bootFailed = false;
      render();
      if (data.context?.case_id) await openCase(data.context.case_id,false);
    } catch (error) {
      S.bootFailed = true;
      $('#main').innerHTML = `<div class="failure-screen"><h1>成长空间暂时无法打开</h1><p>${esc(error.message)}</p><button class="btn" data-action="retry-boot">重新打开</button></div>`;
    }
  }
  async function refreshData() {
    const data = await api('bootstrap');
    S.boot = data;
    S.stats = data.stats || {};
    S.topics = data.topics || S.topics;
    S.trials = data.trials || [];
    S.records = data.records || [];
    if (S.page === 'home') { S.cases = data.cases || []; S.hasMore = data.has_more === true; }
    return data;
  }
  function navigation() {
    $('#navigation').innerHTML = navItems.map(([id,label]) => `<button class="${S.page === id || (S.page === 'study' && id === 'library') ? 'active' : ''}" data-action="navigate" data-page="${id}" title="${label}">${icon(id)}<span>${label}</span></button>`).join('');
  }
  function contextBanner() {
    if (!S.context?.code && !S.context?.symbol) return '';
    const label = [S.context.name || S.context.code || S.context.symbol,S.context.timeframe].filter(Boolean).join(' · ');
    return `<div class="context-banner"><span>${icon('layers')} 当前行情 · ${esc(label)}</span><button class="btn small" data-action="return-chart">返回行情</button></div>`;
  }
  function searchBox(value, id = 'search-input') {
    return `<div class="search-box">${icon('search')}<input id="${id}" type="search" value="${esc(value)}" autocomplete="off" placeholder="品种、关键词或一句记忆" aria-label="搜索案例"><span class="key">Ctrl K</span><button class="btn small" data-action="search-now">搜索</button></div>`;
  }
  function card(c) {
    const src = thumbOf(c);
    const reasons = c.match_reasons || c.hit_reasons || c.matches || [];
    const hit = Array.isArray(reasons) ? reasons.map(v => typeof v === 'string' ? v : v.label || v.field || '').join(' · ') : String(reasons);
    return `<button class="case-card" data-action="quick-open" data-id="${esc(c.id)}" aria-label="查看 ${esc(caseTitle(c))}"><div class="case-photo">${src ? `<img src="${esc(src)}" alt="${esc(caseTitle(c))}" loading="lazy">` : '<span class="micro">图片暂不可用</span>'}${c.data_kind === 'test' ? '<span class="test-tag">测试样本</span>' : ''}</div><div class="case-info"><small class="between"><span>${esc(summary(c))}</span><span class="privacy">私密</span></small><b>${esc(caseTitle(c))}</b>${hit ? `<span class="hit">命中：${esc(hit)}</span>` : `<small>${esc((c.keywords || []).join ? (c.keywords || []).join(' / ') : c.keywords || '')}${c.image_count > 1 ? ` · ${c.image_count} 张图` : ''}</small>`}</div></button>`;
  }
  function grid(cases, emptyText = '还没有案例') {
    return cases.length ? `<div class="case-grid">${cases.map(card).join('')}</div>` : `<div class="empty"><strong>${esc(emptyText)}</strong><p>粘贴或导入一张截图。</p><button class="btn primary" data-action="paste">${icon('paste')} 粘贴截图</button><button class="btn" style="margin-left:8px" data-action="import">${icon('upload')} 导入图片</button></div>`;
  }
  function home() {
    const hasUse = (S.stats.cases || S.cases.length || 0) > 0 || (S.stats.trials || 0) > 0;
    const recent = S.boot.recent_case || (S.boot.recent_case_id && S.cases.find(c => c.id === S.boot.recent_case_id));
    return `<section class="page home-page">${contextBanner()}<div class="hero"><div class="hero-copy"><span class="eyebrow">MY TRADING MEMORY</span><h1>${hasUse ? '欢迎回来。' : '我的交易记忆'}</h1>${searchBox(S.query,'home-search')}<div class="hero-shortcuts"><button class="btn ghost small" data-action="navigate" data-page="library">${icon('library')} 全部案例</button><button class="btn ghost small" data-action="navigate" data-page="trial">${icon('trial')} 开始 PA 试炼</button></div>${!hasUse ? '<div class="first-hint">Ctrl+V 收录截图；不填标题也可以保存。</div>' : ''}</div><div class="drop-box" data-action="import" role="button" tabindex="0" aria-label="导入截图"><div class="drop-icon">${icon('paste')}</div><h2>收录截图</h2><p>拖入图片 · 批量导入</p><span class="paste-key">Ctrl + V</span><button class="btn small" data-action="paste">粘贴截图</button></div></div>${recent ? `<div class="resume"><div><h3>继续研究 · ${esc(caseTitle(recent))}</h3><p>${esc(summary(recent))}</p></div><button class="btn" data-action="study-open" data-id="${esc(recent.id)}">继续 ${icon('arrow')}</button></div>` : ''}<div class="section-head"><h2>${hasUse ? '最近案例' : '案例素材'}</h2>${hasUse ? '<button class="btn ghost small" data-action="navigate" data-page="library">全部案例 →</button>' : ''}</div>${grid(S.cases.slice(0,6))}</section>`;
  }
  function library() {
    const count = S.cases.length;
    const results = S.searchError && !count
      ? `<div class="save-error">${esc(S.searchError)}<br><button class="link-button" data-action="search-now">重新搜索</button></div>`
      : count ? grid(S.cases) : `<div class="empty"><strong>${S.query || Object.values(S.filters).some(Boolean) ? '没有找到对应案例' : '还没有案例'}</strong><p>${S.query || Object.values(S.filters).some(Boolean) ? '试试更短的关键词，或清除筛选。' : '粘贴或导入一张截图。'}</p>${S.query || Object.values(S.filters).some(Boolean) ? '<button class="btn" data-action="reset-search">查看全部案例</button>' : '<button class="btn primary" data-action="import">收录截图</button>'}</div>`;
    const more = S.hasMore ? `${S.searchError && count ? `<div class="save-error" style="margin-top:18px">${esc(S.searchError)}</div>` : ''}<div style="display:flex;justify-content:center;margin-top:22px"><button class="btn" data-action="load-more" ${S.searchBusy ? 'disabled' : ''}>${S.searchBusy ? '正在加载…' : S.searchError ? '重试加载更多' : '加载更多案例'}</button></div>` : '';
    return `<section class="page">${contextBanner()}<div class="library-header between"><div><span class="eyebrow">CASE STUDIES</span><h1>案例研究</h1></div><div class="button-group"><button class="btn" data-action="paste">${icon('paste')} 粘贴</button><button class="btn primary" data-action="import">${icon('plus')} 收录截图</button></div></div>${searchBox(S.query)}<div class="between results-head"><span>${S.searchBusy ? '正在找图…' : `${S.hasMore ? '已显示 ' : ''}${count} 个案例${S.query ? ` · “${esc(S.query)}”` : ''}`}</span><button class="btn ghost small" data-action="toggle-filters" aria-expanded="${S.filterOpen}">${icon('filter')} 筛选${Object.values(S.filters).some(Boolean) ? ' · 已启用' : ''}</button></div>${S.filterOpen ? `<div class="filter-bar"><label>品种<input class="field" data-filter="symbol" value="${esc(S.filters.symbol)}" placeholder="名称或代码"></label><label>周期<input class="field" data-filter="timeframe" value="${esc(S.filters.timeframe)}" placeholder="如 日线、5m"></label><label>市场<input class="field" data-filter="market" value="${esc(S.filters.market)}" placeholder="如 A股、加密"></label><label>来源<select class="field" data-filter="data_kind"><option value="">全部</option><option value="imported" ${S.filters.data_kind === 'imported' ? 'selected' : ''}>个人导入</option><option value="test" ${S.filters.data_kind === 'test' ? 'selected' : ''}>测试样本</option></select></label><button class="btn ghost small" data-action="clear-filters">清除</button></div>` : ''}<div id="search-results">${results}${more}</div></section>`;
  }
  function study() {
    const c = S.case;
    if (!c) return '<div class="loading">正在打开案例…</div>';
    const image = imageOf(c,S.imageIndex);
    const src = imageUrl(image);
    const topics = c.topic_ids || [];
    return `<section class="page study-page">${contextBanner()}<div class="study-header between"><div><h1>${esc(caseTitle(c))}</h1><p>${esc(summary(c))} · ${sourceLabel(c)} · 本机私密草稿</p></div><div class="button-group"><button class="btn" data-action="study-back">${icon('back')} 返回搜索</button><button class="btn" data-action="original">${icon('zoom')} 查看原图</button>${S.context?.can_compare === true && !S.context?.comparison ? '<button class="btn primary" data-action="compare">' + icon('layers') + ' 与当前行情对照</button>' : ''}</div></div><div class="study-layout"><section class="photo-panel"><div class="photo-heading"><span>${icon('image')} 历史原图</span><span class="muted">${c.data_kind === 'test' ? '测试样本 · ' : ''}${S.imageIndex + 1} / ${(c.images || []).length}</span></div><button class="study-image" data-action="original" title="放大原图">${src ? `<img src="${esc(src)}" alt="${esc(image?.name || caseTitle(c))}">` : `<span class="muted">${esc(imageMessage(image))}</span>`}</button>${gallery(c)}<div class="photo-footer"><div class="topic-list">${(c.tags || []).map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div><button class="btn ghost small" data-action="append-images">${icon('plus')} 追加图片</button></div></section><aside class="study-side"><h2>案例笔记</h2><nav class="tabs">${[['memory','当时的记忆'],['notes','研究记录'],['topics','知识线索']].map(([id,name]) => `<button data-action="study-pane" data-pane="${id}" class="${S.studyPane === id ? 'active' : ''}">${name}</button>`).join('')}</nav><div class="side-content">${studyPane(c,topics)}</div></aside></div></section>`;
  }
  function gallery(c, action = 'case-image') {
    return c.images?.length > 1 ? `<div class="small-gallery">${c.images.map((image,index) => `<button class="${S.imageIndex === index ? 'active' : ''}" data-action="${action}" data-index="${index}" aria-label="第 ${index + 1} 张图片" title="${esc(image.name || '案例图片')}"><span class="micro">第 ${index + 1} 张</span></button>`).join('')}</div>` : '';
  }
  function studyPane(c,topics) {
    if (S.studyPane === 'memory') return `<div class="memory">${esc(c.memory || '尚未添加记忆描述')}</div><div class="rule"></div><div class="source-details">${c.keywords?.length ? `<div>关键词 <strong>${esc(Array.isArray(c.keywords) ? c.keywords.join(' / ') : c.keywords)}</strong></div>` : ''}<div>收录于 <strong>${esc(date(c.created))}</strong></div><div>图片 <strong>${c.images?.length || 0} 张</strong></div></div><div class="form-actions"><button class="btn small" data-action="edit-case">${icon('edit')} 编辑资料</button></div><div class="rule"></div><button class="btn ghost small" data-action="start-trial" data-id="${esc(c.id)}">${icon('trial')} 用这张图自主试炼</button>`;
    if (S.studyPane === 'topics') return `<div class="topic-list">${topics.length ? topics.map(id => `<button class="topic-chip" data-action="open-topic" data-id="${esc(id)}">${esc(topicName(id))}</button>`).join('') : '<span class="micro">尚未关联知识主题</span>'}</div><div class="form-actions"><button class="btn small" data-action="edit-case">关联主题</button></div>`;
    return `${notesOf(c).length ? `<div class="note-list">${notesOf(c).map(note => `<article class="note-item"><small>${esc(note.author || '我')} · ${esc(date(note.created))}</small><p>${esc(note.text || note.content || '')}</p></article>`).join('')}</div>` : '<div class="inline-empty">还没有研究记录。</div>'}<div class="note-composer"><label class="field-label" for="case-note">添加个人研究</label><textarea id="case-note" class="field" data-draft="case-note:${esc(c.id)}" placeholder="现在看到了什么？">${esc(S.draftText['case-note:' + c.id] || '')}</textarea><button class="btn primary small" data-action="save-note">保存研究笔记</button></div>`;
  }
  function trial() {
    const c = S.trialCase;
    if (!c) return `<section class="page"><span class="eyebrow">PA PRACTICE</span><h1>PA 交易试炼</h1><div class="empty"><strong>从一张自己的图开始</strong><p>选择案例，记录当时的判断。</p>${Number(S.stats.cases || S.cases.length) ? '<button class="btn primary" data-action="choose-trial">选择案例</button>' : '<button class="btn primary" data-action="import">收录截图</button><button class="btn" style="margin-left:8px" data-action="paste">粘贴截图</button>'}</div>${S.trials.length ? trialHistory() : ''}</section>`;
    const image = imageOf(c,S.imageIndex);
    const judged = !!S.trialRecord;
    return `<section class="page"><div class="between wrap"><div><span class="eyebrow">PA PRACTICE</span><h1>PA 交易试炼</h1></div><button class="btn small" data-action="choose-trial">更换案例</button></div><div class="trial-layout"><section class="photo-panel"><div class="photo-heading"><span>${esc(caseTitle(c))}</span><span class="trial-label">截图自主试炼${c.data_kind === 'test' ? ' · 测试样本' : ''}</span></div><div class="trial-image-wrap ${S.marking ? 'marking' : ''}" id="trial-image-wrap">${imageUrl(image) ? `<img id="trial-image" src="${esc(imageUrl(image))}" alt="试炼截图">` : `<span class="muted">${esc(imageMessage(image))}</span>`}<div class="mark-layer" id="trial-mark-layer"></div></div>${judged ? gallery(c,'trial-image') : ''}<div class="trial-footer"><span>原截图可能含后续走势 · 无自动评分</span><div class="button-group">${!judged ? `<button class="btn ghost small ${S.marking ? 'active' : ''}" data-action="toggle-mark">${icon('pin')} ${S.marking ? '完成标记' : '标记位置'}</button>` : `<span class="micro">${(S.trialRecord.details?.marks || S.marks).length} 个判断位置</span>`}<button class="btn ghost small" data-action="trial-original">${icon('zoom')} 原图</button></div></div></section><aside class="trial-panel"><div class="trial-depth">${[['quick','快速判断'],['structure','结构辨析'],['deep','深度推演']].map(([id,name]) => `<button class="${S.trialDepth === id ? 'active' : ''}" data-action="trial-depth" data-depth="${id}" ${judged ? 'disabled' : ''}>${name}</button>`).join('')}</div>${!judged ? `<h2>看到这张图，<br>你会怎么做？</h2><div class="judgments">${[['consider','考虑计划'],['wait','继续观察'],['pass','主动放弃']].map(([id,name]) => `<button data-action="judgment" data-value="${id}" class="${S.judgment === id ? 'active' : ''}">${name}</button>`).join('')}</div><label class="field-label" for="trial-thought">一句判断（可选）</label><textarea id="trial-thought" class="field" placeholder="先留下自己的看法">${esc(S.trialThought)}</textarea><div class="form-actions"><button class="btn primary" data-action="save-judgment" ${!S.judgment || !imageUrl(image) ? 'disabled' : ''}>记录判断</button></div>` : `<div class="judgment-record"><b>${esc(judgmentName(S.trialRecord.judgment || S.judgment))}</b><p>${esc(S.trialRecord.details?.thought || S.trialThought || '')}</p><p>判断已保存 · ${esc(date(S.trialRecord.created))}</p></div><div class="trial-review"><label class="field-label" for="trial-evidence">${S.trialDepth === 'quick' ? '后来看到的证据' : '支持与反对的结构证据'}</label><textarea id="trial-evidence" class="field" data-review="evidence" placeholder="可直接结束，也可以继续研究">${esc(S.trialReview.evidence || '')}</textarea>${S.trialDepth !== 'quick' ? `<label class="field-label" for="trial-invalidation">什么会使原判断失效？</label><textarea id="trial-invalidation" class="field" data-review="invalidation">${esc(S.trialReview.invalidation || '')}</textarea>` : ''}${S.trialDepth === 'deep' ? `<label class="field-label" for="trial-reflection">不同后续走势下的应对</label><textarea id="trial-reflection" class="field" data-review="reflection">${esc(S.trialReview.reflection || '')}</textarea>` : ''}<div class="form-actions"><button class="btn small" data-action="save-trial-review">保存事后研究</button><button class="btn primary small" data-action="finish-trial">完成</button></div></div>`}</aside></div>${S.trials.length ? trialHistory() : ''}</section>`;
  }
  function judgmentName(value) { return ({consider:'考虑计划',wait:'继续观察',pass:'主动放弃'})[value] || value || '已记录判断'; }
  function trialHistory() {
    return `<div class="section-head"><h2>我的判断记录</h2></div><div class="timeline">${S.trials.slice(0,8).map(record => `<article class="timeline-item"><b>${esc(judgmentName(record.judgment))}</b><p>${esc(record.case_title || record.title || '截图自主试炼')}${record.data_kind === 'test' ? ' · 测试样本' : ''}</p><small>${esc(date(record.created))}</small>${record.case_id ? ` · <button class="link-button" data-action="study-open" data-id="${esc(record.case_id)}">查看案例</button>` : ''}</article>`).join('')}</div>`;
  }
  function universe() {
    const selected = S.topics.find(topic => topic.id === S.topicId);
    const parent = S.topics.find(topic => topic.id === S.topicParent);
    const nodes = S.topics.filter(topic => (topic.parent_id || null) === (S.topicParent || null));
    const center = parent || {id:'root',name:'价格行为'};
    const positions = nodes.map((node,index) => {
      const angle = -Math.PI / 2 + index * (Math.PI * 2 / Math.max(1,nodes.length));
      return {node,x:420 + Math.cos(angle) * 242,y:360 + Math.sin(angle) * 215};
    });
    return `<section class="universe-page"><div class="universe-top"><span class="eyebrow">KNOWLEDGE UNIVERSE</span><h1>知识宇宙</h1><div class="universe-breadcrumbs"><button data-action="universe-root">价格行为</button>${parent ? `<span>›</span><span>${esc(parent.name)}</span>` : ''}</div></div><div class="universe-stage"><svg viewBox="0 0 840 720" role="group" aria-label="知识主题关系图"><circle class="halo" cx="420" cy="360" r="275"/><circle class="halo" cx="420" cy="360" r="160"/>${positions.map(({x,y}) => `<line class="relationship" x1="420" y1="360" x2="${x}" y2="${y}"/>`).join('')}<g class="${parent ? 'topic-node' : 'universe-center'}" ${parent ? 'data-action="universe-root"' : ''}><circle class="node-circle" cx="420" cy="360" r="52"/><circle class="node-core" cx="420" cy="344" r="5"/><text x="420" y="372">${esc(center.name)}</text></g>${positions.map(({node,x,y}) => `<g class="topic-node ${S.topicId === node.id ? 'selected' : ''}" data-action="select-topic" data-id="${esc(node.id)}" tabindex="0" role="button" aria-label="${esc(node.name)}"><circle class="halo" cx="${x}" cy="${y}" r="58"/><circle class="node-circle" cx="${x}" cy="${y}" r="42"/><circle class="node-core" cx="${x}" cy="${y - 8}" r="4"/><text x="${x}" y="${y + 12}">${esc(node.name)}</text><text class="node-count" x="${x}" y="${y + 68}">${Number(node.case_count || 0)} 个关联案例</text></g>`).join('')}</svg></div><aside class="universe-inspector"><span class="eyebrow">${parent ? '知识主题' : '主题探索'}</span><h2>${esc(selected?.name || parent?.name || '价格行为')}</h2><div class="topic-description">${esc(selected?.description || parent?.description || '从背景、结构与计划逐层探索。')}</div>${selected && S.topics.some(topic => topic.parent_id === selected.id) ? `<div class="form-actions"><button class="btn primary small" data-action="universe-enter" data-id="${esc(selected.id)}">进入主题 ${icon('arrow')}</button></div>` : ''}<div class="topic-case-list">${S.topicLoading ? '<div class="universe-empty">正在查找关联案例…</div>' : S.topicCases.length ? S.topicCases.slice(0,7).map(c => `<button class="topic-case" data-action="study-open" data-id="${esc(c.id)}"><img src="${esc(thumbOf(c))}" alt=""><span>${esc(caseTitle(c))}</span></button>`).join('') : '<div class="universe-empty">尚无关联案例。<br>可在案例资料中关联主题。</div>'}</div>${selected ? `<div class="topic-note-entry"><div class="rule"></div><label class="field-label" for="topic-note">个人研究</label><textarea id="topic-note" class="field" data-draft="topic-note:${esc(S.topicId)}" placeholder="留下新的认识">${esc(S.draftText['topic-note:' + S.topicId] || '')}</textarea><div class="form-actions"><button class="btn small" data-action="save-topic-note">保存记录</button></div></div>` : ''}</aside></section>`;
  }
  function growth() {
    const stats = S.stats;
    const cases = Number(stats.cases || 0), images = Number(stats.images || stats.assets || 0), topics = Number(stats.researched_topics || stats.topics || 0), trials = Number(stats.trials || 0);
    const records = S.records || [];
    return `<section class="page"><span class="eyebrow">MY JOURNEY</span><h1>个人成长</h1><div class="growth-grid"><section class="growth-cell"><h3>案例素材积累</h3><strong class="growth-number">${cases}</strong><p>${images} 张原始图片 · 私密草稿${Number(stats.test_cases || 0) ? `<br>含 ${Number(stats.test_cases)} 个测试样本` : ''}</p><button class="btn ghost small" data-action="navigate" data-page="library">查看案例 →</button></section><section class="growth-cell"><h3>已开展的知识研究</h3><strong class="growth-number">${topics}</strong><p>${Number(stats.topic_notes || 0)} 条主题研究记录</p><button class="btn ghost small" data-action="navigate" data-page="universe">进入知识宇宙 →</button></section><section class="growth-cell"><h3>真实判断记录</h3><strong class="growth-number">${trials}</strong><p>截图自主试炼 · 不自动评判盈亏${Number(stats.test_trials || 0) ? `<br>含 ${Number(stats.test_trials)} 条测试样本判断` : ''}</p><button class="btn ghost small" data-action="navigate" data-page="trial">查看判断 →</button></section></div><div class="growth-lower"><section class="growth-panel"><h2>个人研究轨迹</h2>${records.length ? `<div class="timeline">${records.slice(0,12).map(record => `<article class="timeline-item"><b>${esc(record.title || record.case_title || (record.topic_id ? topicName(record.topic_id) : '个人研究'))}</b><p>${esc(record.text || record.content || record.memory || '')}</p><small>${esc(record.author || '我')} · ${esc(date(record.created))}</small>${record.case_id ? ` · <button class="link-button" data-action="study-open" data-id="${esc(record.case_id)}">查看案例</button>` : ''}</article>`).join('')}</div>` : '<div class="inline-empty">案例笔记和主题研究保存后，会出现在这里。</div>'}</section><section class="growth-panel"><h2>判断与回看</h2>${S.trials.length ? `<div class="timeline">${S.trials.slice(0,8).map(record => `<article class="timeline-item"><b>${esc(judgmentName(record.judgment))}</b><p>${esc(record.case_title || record.title || '截图自主试炼')}${record.data_kind === 'test' ? ' · 测试样本' : ''}<br>${esc(record.details?.thought || '')}</p><small>${esc(date(record.created))}</small>${record.case_id ? ` · <button class="link-button" data-action="study-open" data-id="${esc(record.case_id)}">回看原图</button>` : ''}</article>`).join('')}</div>` : '<div class="inline-empty">还没有判断记录。</div>'}</section></div></section>`;
  }
  function render() {
    navigation();
    const scroll = $('#main').scrollTop;
    $('#main').innerHTML = ({home,library,study,trial,universe,growth}[S.page] || home)();
    $('#main').scrollTop = scroll;
    if (S.page === 'trial') requestAnimationFrame(renderMarks);
    requestVisibleImage();
  }
  async function navigate(page) {
    if (S.overlay) { if (!closeOverlay()) return; }
    cancelSearch();
    S.caseSeq++; S.page = page;
    $('#main').scrollTop = 0;
    if (page === 'library') { render(); await search(false); }
    else if (['home','growth','universe','trial'].includes(page)) {
      try { await refreshData(); } catch (error) { toast(error.message,true); }
      render();
    } else render();
  }
  async function search(preserveFocus = true, append = false) {
    if (append && (S.searchBusy || !S.hasMore)) return;
    const seq = ++S.searchSeq;
    const query = S.query;
    const filters = {...S.filters};
    const offset = append ? S.cases.length : 0;
    if (!append) { S.cases = []; S.hasMore = false; }
    S.searchBusy = true; S.searchError = '';
    const resultsLabel = $('.results-head > span');
    if (resultsLabel) resultsLabel.textContent = append ? '正在加载…' : '正在找图…';
    const moreButton = $('[data-action="load-more"]');
    if (moreButton) { moreButton.disabled = true; moreButton.textContent = '正在加载…'; }
    try {
      const result = await api('search_cases',query,filters,offset);
      if (seq !== S.searchSeq || S.page !== 'library' || query !== S.query || JSON.stringify(filters) !== JSON.stringify(S.filters)) return;
      S.cases = append ? [...S.cases,...(result.cases || [])] : result.cases || [];
      S.hasMore = result.has_more === true;
    } catch (error) {
      if (seq !== S.searchSeq) return;
      if (!append) { S.cases = []; S.hasMore = false; }
      S.searchError = error.message;
    } finally {
      if (seq === S.searchSeq) {
        S.searchBusy = false;
        if (S.page === 'library') {
          const active = document.activeElement;
          const focused = preserveFocus && active?.id === 'search-input';
          const start = focused ? active.selectionStart : null;
          const end = focused ? active.selectionEnd : null;
          const filter = preserveFocus && active?.dataset?.filter;
          const filterSelection = filter ? active.selectionStart : null;
          const moreFocused = preserveFocus && active?.dataset?.action === 'load-more';
          render();
          if (focused) { $('#search-input')?.focus({preventScroll:true}); try { $('#search-input')?.setSelectionRange(start,end); } catch (_) { /* search fields may not expose a selection */ } }
          if (filter) {
            const field = $(`[data-filter="${filter}"]`); field?.focus({preventScroll:true});
            try { field?.setSelectionRange(filterSelection,filterSelection); } catch (_) { /* select fields have no text selection */ }
          }
          if (moreFocused && S.hasMore) $('[data-action="load-more"]')?.focus({preventScroll:true});
        }
      }
    }
  }
  function cancelSearch() {
    clearTimeout(S.searchTimer); S.searchTimer = null;
    S.searchSeq++; S.searchBusy = false;
  }
  function scheduleSearch() {
    clearTimeout(S.searchTimer);
    S.searchTimer = setTimeout(() => {
      if (S.page === 'home' && !S.overlay) { S.page = 'library'; render(); $('#search-input')?.focus(); }
      search();
    },180);
  }
  async function openCase(id, quick = false) {
    cancelSearch();
    const seq = ++S.caseSeq;
    try {
      const result = await api('get_case',id);
      if (seq !== S.caseSeq) return;
      S.case = result.case;
      S.imageIndex = 0;
      S.boot.recent_case_id = id;
      S.boot.recent_case = S.case;
      if (quick) { S.returnPage = S.page; S.returnScroll = $('#main').scrollTop; openOriginal(false); }
      else {
        if (S.page !== 'study') { S.returnPage = S.page; S.returnScroll = $('#main').scrollTop; }
        S.page = 'study'; S.studyPane = 'memory'; render(); $('#main').scrollTop = 0;
      }
    } catch (error) { if (seq === S.caseSeq) toast(error.message,true); }
  }
  async function backToSearch() {
    S.caseSeq++;
    S.page = S.returnPage === 'study' ? 'library' : S.returnPage;
    if (!['home','library','universe','growth','trial'].includes(S.page)) S.page = 'library';
    render(); $('#main').scrollTop = S.returnScroll;
  }
  function dialog(title,body,foot,wide = false) {
    return `<div class="overlay"><section class="dialog ${wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="dialog-head"><h2>${title}</h2><button class="btn ghost icon-button" data-action="close-overlay" aria-label="关闭">${icon('close')}</button></div><div class="dialog-body">${body}</div>${foot ? `<div class="dialog-foot">${foot}</div>` : ''}</section></div>`;
  }
  function overlayHTML(html,type) {
    S.overlay = type;
    $('#overlay-root').innerHTML = html;
    const focus = $('#overlay-root button') || $('#overlay-root input');
    focus?.focus();
  }
  function closeOverlay(force = false) {
    if (S.saving && !force) return false;
    if (S.overlay === 'capture' && S.pending.length && !force) {
      captureMetadata();
      if (!window.confirm('这些截图尚未保存。放弃本次收录？')) return false;
      const tokens = S.pending.map(p => p.token);
      api('discard_capture',tokens).catch(() => {});
      S.pending = []; S.captureMetadata = {}; S.captureError = ''; S.captureCaseId = null; S.duplicateAllowed = false;
    }
    if (S.overlay === 'metadata' && S.dirtyMetadata && !force) {
      if (!window.confirm('资料尚未保存。放弃修改？')) return false;
      S.dirtyMetadata = false;
    }
    S.caseSeq++;
    S.overlay = null; S.original = null;
    $('#overlay-root').innerHTML = '';
    return true;
  }
  function captureMetadata() {
    if (!$('#capture-form')) return;
    S.captureMetadata = readMetadata($('#capture-form'));
    S.captureGroup = !!$('#capture-group')?.checked;
    S.duplicateAllowed = !!$('#capture-duplicate')?.checked;
  }
  function readMetadata(form) {
    const result = {};
    $$('[data-meta]',form).forEach(field => { result[field.dataset.meta] = field.value.trim(); });
    result.keywords = String(result.keywords || '').split(/[,，;；\n]+/).map(v => v.trim()).filter(Boolean);
    result.tags = String(result.tags || '').split(/[,，;；\n]+/).map(v => v.trim()).filter(Boolean);
    result.topic_ids = $$('[data-topic-select]:checked',form).map(field => field.dataset.topicSelect);
    return result;
  }
  function metadataForm(metadata = {}, id = 'capture-form') {
    const keywords = Array.isArray(metadata.keywords) ? metadata.keywords.join('，') : metadata.keywords || '';
    const tags = Array.isArray(metadata.tags) ? metadata.tags.join('，') : metadata.tags || '';
    return `<div id="${id}" class="capture-form"><label class="field-label" for="${id}-memory">一句记忆（可选）</label><textarea id="${id}-memory" class="field" data-meta="memory" placeholder="当时最想记住什么？">${esc(metadata.memory || '')}</textarea><label class="field-label" for="${id}-keywords">关键词（可选）</label><input id="${id}-keywords" class="field" data-meta="keywords" value="${esc(keywords)}" placeholder="如 microDB，ETHUSD，锦和商业"><details class="advanced-fields"><summary>补充资料</summary><label class="field-label" for="${id}-title">标题</label><input id="${id}-title" class="field" data-meta="title" value="${esc(metadata.title || '')}"><div class="metadata-grid" style="margin-top:13px"><label class="field-label">品种<input class="field" data-meta="symbol" value="${esc(metadata.symbol || '')}" placeholder="代码或名称"></label><label class="field-label">周期<input class="field" data-meta="timeframe" value="${esc(metadata.timeframe || '')}" placeholder="如 日线、5m"></label><label class="field-label">市场<input class="field" data-meta="market" value="${esc(metadata.market || '')}" placeholder="如 A股、加密"></label><label class="field-label">资料来源<select class="field" data-meta="data_kind"><option value="imported" ${metadata.data_kind !== 'test' ? 'selected' : ''}>个人导入</option><option value="test" ${metadata.data_kind === 'test' ? 'selected' : ''}>测试样本</option></select></label></div><label class="field-label">已确认的 PA 标签</label><input class="field" data-meta="tags" value="${esc(tags)}" placeholder="多个标签用逗号分隔"><label class="field-label">关联知识主题</label><div class="topic-selector">${S.topics.filter(topic => topic.parent_id).map(topic => `<label><input type="checkbox" data-topic-select="${esc(topic.id)}" ${(metadata.topic_ids || []).includes(topic.id) ? 'checked' : ''}>${esc(topic.name)}</label>`).join('')}</div></details></div>`;
  }
  function renderCapture() {
    const hasDuplicates = S.pending.some(p => p.duplicate_cases?.length);
    const append = !!S.captureCaseId;
    const body = `<div class="save-error">${esc(S.captureError)}</div><div class="capture-layout"><div><div class="pending-list">${S.pending.map((pending,index) => `<div class="pending-photo"><img src="${esc(imageUrl(pending))}" alt="待保存截图 ${index + 1}"><div><span>${esc(pending.name || `截图 ${index + 1}`)}</span><span>未保存</span></div>${pending.duplicate_cases?.length ? `<p>已收录过相同图片：${esc(pending.duplicate_cases.map(c => typeof c === 'string' ? c : caseTitle(c)).join('、'))}</p>` : ''}</div>`).join('')}</div><div class="button-group" style="margin-top:11px"><button class="btn small" data-action="import">${icon('plus')} 继续导入</button><button class="btn ghost small" data-action="paste">${icon('paste')} 粘贴</button></div></div><div>${append ? `<div class="memory">追加到 · ${esc(caseTitle(S.case))}<br>新图保存后会与原图并列保留。</div>` : metadataForm(S.captureMetadata)}${!append && S.pending.length > 1 ? `<div class="grouping"><label><input type="radio" name="capture-grouping" ${!S.captureGroup ? 'checked' : ''}>每张图保存为独立案例</label><label><input id="capture-group" type="radio" name="capture-grouping" ${S.captureGroup ? 'checked' : ''}>组成同一个研究案例</label></div>` : ''}${hasDuplicates ? `<div class="alert-line">图片重复。原有案例会保留。</div><label class="field-label row"><input id="capture-duplicate" type="checkbox" ${S.duplicateAllowed ? 'checked' : ''}>我知道图片重复，仍${append ? '追加原图' : '保存新案例'}</label>` : ''}</div></div>`;
    const foot = `<span class="micro">${icon('lock')} 本机私密草稿 · ${S.pending.length} 张待保存</span><div class="button-group"><button class="btn ghost" data-action="close-overlay">取消</button><button id="capture-save" class="btn primary" data-action="save-capture" ${S.saving ? 'disabled' : ''}>${S.saving ? '正在保存…' : append ? '保存追加图片' : '保存案例'}</button></div>`;
    overlayHTML(dialog(append ? '追加图片' : '收录截图',body,foot,true),'capture');
    if (!append && !S.captureMetadata.memory) setTimeout(() => $('#capture-form-memory')?.focus(),0);
  }
  let stageQueue = Promise.resolve();
  function queuePending(action) {
    S.staging++;
    stageQueue = stageQueue.then(async () => {
      if (S.overlay === 'capture') captureMetadata();
      try {
        const result = await action();
        const pending = result.pending || [];
        if (!pending.length) return;
        S.pending.push(...pending);
        S.captureError = '';
        renderCapture();
      } catch (error) { toast(error.message,true); }
      finally { S.staging--; }
    });
    return stageQueue;
  }
  function importFiles() { return queuePending(() => api('import_files',S.captureCaseId)); }
  function pasteNative() { return queuePending(() => api('paste_image',S.captureCaseId)); }
  async function stageFiles(files) {
    const images = Array.from(files).filter(file => /^image\/(png|jpeg|webp)$/i.test(file.type) || /\.(png|jpe?g|webp)$/i.test(file.name));
    if (!images.length) { toast('请选择 PNG、JPEG 或 WebP 图片。',true); return; }
    if (images.length > 100 || images.reduce((sum,file) => sum + file.size,0) > 128 * 1024 * 1024) { toast('每批最多 100 张、合计 128 MB，请分批导入。',true); return; }
    S.staging++;
    try {
    const values = [];
    for (const file of images) {
      if (file.size > 32 * 1024 * 1024) { toast(`${file.name} 超过 32 MB，请使用较小图片。`,true); continue; }
      const data = await new Promise((resolve,reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error(`读取 ${file.name} 失败`)); reader.readAsDataURL(file);
      });
      values.push({name:file.name || `剪贴板截图-${new Date().toISOString().replace(/[:.]/g,'-')}.png`,data_url:data});
    }
    if (values.length) return queuePending(() => api('stage_images',values,S.captureCaseId));
    } finally { S.staging--; }
  }
  async function saveCapture() {
    if (S.saving || !S.pending.length) return;
    captureMetadata();
    if (S.pending.some(p => p.duplicate_cases?.length) && !S.duplicateAllowed) {
      S.captureError = '这批图片中有重复文件。确认上方选项后才能继续保存。'; renderCapture(); return;
    }
    const tokens = S.pending.map(p => p.token);
    S.saving++; renderCapture();
    let committed = false;
    try {
      const result = await api('save_capture',tokens,S.captureMetadata,{allow_duplicates:S.duplicateAllowed,case_id:S.captureCaseId,group:S.captureGroup});
      if (result.duplicates?.length && !result.cases?.length) {
        S.captureError = '发现重复图片。请确认仍要保存。';
        S.pending[0].duplicate_cases = result.duplicates;
        result.duplicates.forEach(duplicate => {
          const pending = S.pending.find(p => p.token === duplicate.token);
          if (pending) pending.duplicate_cases = duplicate.cases || duplicate.duplicate_cases || [duplicate];
        });
        return;
      }
      if (!result.cases?.length) throw new Error('未收到保存确认，请重新打开案例库核对。');
      committed = true;
      const appended = S.captureCaseId;
      const savedCount = S.pending.length;
      S.pending = []; S.captureMetadata = {}; S.captureError = ''; S.captureGroup = false; S.captureCaseId = null; S.duplicateAllowed = false;
      $('#overlay-root').innerHTML = ''; S.overlay = null;
      toast(`已保存 ${savedCount} 张原图 · 本机私密草稿`);
      await refreshData();
      if (appended) {
        const data = await api('get_case',appended); S.case = data.case; render();
      } else if (S.page === 'library') await search(false);
      else if (S.page === 'study') render();
      else { S.page = 'home'; render(); }
    } catch (error) {
      if (committed) toast(`案例已保存，列表暂时无法刷新。${error.message}`,true);
      else S.captureError = error.message;
    }
    finally {
      S.saving--;
      if (S.pending.length) renderCapture();
    }
  }
  function editCase() {
    S.dirtyMetadata = false;
    const body = `<div id="metadata-error" class="save-error"></div>${metadataForm(S.case,'metadata-form')}`;
    overlayHTML(dialog('编辑案例资料',body,'<span class="micro">本机私密草稿</span><button class="btn primary" data-action="save-metadata">保存资料</button>'),'metadata');
  }
  async function saveMetadata() {
    if (S.saving) return;
    const metadata = readMetadata($('#metadata-form'));
    S.saving++;
    const button = $('[data-action="save-metadata"]'); button.disabled = true; button.textContent = '正在保存…';
    try {
      const result = await api('update_case',S.case.id,metadata);
      if (result.case) S.case = result.case;
      S.dirtyMetadata = false; closeOverlay(true); render(); toast('资料已保存');
    } catch (error) { $('#metadata-error').textContent = error.message; }
    finally { S.saving--; if (S.overlay === 'metadata') { button.disabled = false; button.textContent = '保存资料'; } else $('#overlay-root').innerHTML = ''; }
  }
  async function saveNote() {
    const text = $('#case-note')?.value.trim();
    if (!text) { toast('先写下一句研究笔记。'); return; }
    if (S.saving) return;
    S.saving++;
    let committed = false;
    const button = $('[data-action="save-note"]'); button.disabled = true; button.textContent = '正在保存…';
    try {
      const result = await api('add_note',S.case.id,text);
      committed = true;
      S.draftText['case-note:' + S.case.id] = '';
      if (result.note) S.case.notes = [...notesOf(S.case),result.note];
      render(); toast('研究笔记已保存');
      await refreshData();
    } catch (error) { toast(committed ? `笔记已保存，列表暂时无法刷新。${error.message}` : error.message,true); button.disabled = false; button.textContent = '保存研究笔记'; }
    finally { S.saving--; }
  }
  function openOriginal(fromStudy = true, fromTrial = false) {
    const c = fromTrial ? S.trialCase : S.case;
    if (!c || !imageOf(c,S.imageIndex)) return;
    S.imageError = null;
    const index = fromTrial && !S.trialRecord ? 0 : S.imageIndex;
    S.original = {case:c,index,zoom:1,x:0,y:0,drag:null,fromStudy,fromTrial};
    renderOriginal();
  }
  function renderOriginal() {
    const original = S.original;
    const c = original.case;
    const image = imageOf(c,original.index);
    // Detach the covered original so it cannot outlive eviction from the cache.
    const background = S.page === 'study' ? $('.study-image') : S.page === 'trial' ? $('#trial-image-wrap') : null;
    if (background) background.innerHTML = '';
    const canBrowse = c.images.length > 1 && (!original.fromTrial || !!S.trialRecord);
    const html = `<div class="overlay original-overlay"><section class="original-dialog" role="dialog" aria-modal="true" aria-label="原图速览"><div class="original-toolbar"><span class="image-name">${esc(image?.name || caseTitle(c))}${c.data_kind === 'test' ? ' · 测试样本' : ''} · 私密</span><div class="button-group"><button class="btn ghost icon-button" data-action="zoom-out" aria-label="缩小">${icon('minus')}</button><span class="zoom-label">${Math.round(original.zoom * 100)}%</span><button class="btn ghost icon-button" data-action="zoom-in" aria-label="放大">${icon('plus')}</button><button class="btn small" data-action="zoom-fit">适应</button><button class="btn small" data-action="zoom-native">1:1</button>${!original.fromStudy && !original.fromTrial ? '<button class="btn primary small" data-action="quick-study">研究此案例</button>' : ''}<button class="btn ghost icon-button" data-action="close-original" aria-label="返回">${icon('close')}</button></div></div><div class="original-stage" id="original-stage">${imageUrl(image) ? `<img id="original-image" src="${esc(imageUrl(image))}" alt="${esc(image?.name || caseTitle(c))}" draggable="false">` : `<div class="loading"><p>${esc(imageMessage(image))}</p>${S.imageError?.id === image?.id ? '<button class="btn" data-action="retry-image">重新读取原图</button>' : ''}</div>`}</div><div class="original-footer"><span>${original.index + 1} / ${c.images.length} · ${image?.width || ''}${image?.height ? ' × ' + image.height : ''}</span><span class="pan-hint">滚轮缩放 · 拖动平移 · Esc 返回</span><div class="button-group">${canBrowse ? `<button class="btn ghost small" data-action="original-prev" ${original.index <= 0 ? 'disabled' : ''}>上一张</button><button class="btn ghost small" data-action="original-next" ${original.index >= c.images.length - 1 ? 'disabled' : ''}>下一张</button>` : ''}</div></div></section></div>`;
    S.overlay = 'original'; $('#overlay-root').innerHTML = html;
    applyOriginalTransform();
    requestVisibleImage();
  }
  function applyOriginalTransform() {
    const image = $('#original-image');
    if (image && S.original) image.style.transform = `translate(${S.original.x}px,${S.original.y}px) scale(${S.original.zoom})`;
    const label = $('.zoom-label'); if (label && S.original) label.textContent = `${Math.round(S.original.zoom * 100)}%`;
  }
  function zoomOriginal(factor) {
    if (!S.original) return;
    S.original.zoom = Math.min(12,Math.max(.25,S.original.zoom * factor)); applyOriginalTransform();
  }
  function closeOriginal() {
    const original = S.original;
    S.imageIndex = original?.fromTrial && !S.trialRecord ? 0 : original?.index || 0;
    S.original = null; S.overlay = null; $('#overlay-root').innerHTML = '';
    if (S.page === 'study' || S.page === 'trial') render();
  }
  async function chooseTrial() {
    const result = await api('search_cases','',{});
    if (!result.cases?.length) { toast('先收录一张自己的截图。'); return; }
    overlayHTML(dialog('选择试炼案例',`<div class="case-grid">${result.cases.map(c => card(c).replace('data-action="quick-open"','data-action="start-trial"')).join('')}</div>`,null,true),'trial-picker');
  }
  async function startTrial(id) {
    if ((S.trialThought.trim() || Object.values(S.trialReview).some(Boolean)) && !window.confirm('当前试炼还有未保存的文字。更换案例？')) return;
    const seq = ++S.caseSeq;
    const data = await api('get_case',id);
    if (seq !== S.caseSeq) return;
    closeOverlay(true);
    S.trialCase = data.case; S.trialRecord = null; S.judgment = ''; S.trialThought = ''; S.trialReview = {}; S.marks = []; S.imageIndex = 0; S.marking = false;
    S.page = 'trial'; render(); $('#main').scrollTop = 0;
  }
  function renderMarks() {
    const layer = $('#trial-mark-layer'), image = $('#trial-image');
    if (!layer || !image || !image.complete) { if (image) image.onload = renderMarks; return; }
    const wrapRect = $('#trial-image-wrap').getBoundingClientRect(), imageRect = image.getBoundingClientRect();
    layer.innerHTML = S.marks.filter(mark => mark.image_id === imageOf(S.trialCase,S.imageIndex)?.id).map(mark => `<span class="price-mark" style="left:${imageRect.left - wrapRect.left + mark.x * imageRect.width}px;top:${imageRect.top - wrapRect.top + mark.y * imageRect.height}px"></span>`).join('');
  }
  function markPosition(event) {
    if (!S.marking || S.trialRecord) return;
    const image = $('#trial-image');
    if (!image || !image.complete) return;
    const rect = image.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) return;
    S.marks.push({image_id:imageOf(S.trialCase,S.imageIndex)?.id,x:(event.clientX - rect.left) / rect.width,y:(event.clientY - rect.top) / rect.height}); renderMarks();
  }
  async function saveJudgment() {
    if (!S.judgment || S.saving || S.trialRecord) return;
    if (!imageUrl(imageOf(S.trialCase,S.imageIndex))) { toast('请等待原图读取完成，再记录判断。'); return; }
    S.trialThought = $('#trial-thought')?.value || S.trialThought;
    const value = {judgment:S.judgment,depth:S.trialDepth,details:{thought:S.trialThought,marks:S.marks,source:'screenshot-self-practice',image_id:imageOf(S.trialCase,S.imageIndex)?.id}};
    S.saving++;
    let committed = false;
    const button = $('[data-action="save-judgment"]'); button.disabled = true; button.textContent = '正在保存…';
    try {
      const data = await api('save_trial',S.trialCase.id,value);
      S.trialRecord = data.trial || data.record;
      if (!S.trialRecord) throw new Error('未收到判断记录，请重新打开案例核对。');
      committed = true;
      S.marking = false; S.trialThought = '';
      render(); toast('判断已保存');
      await refreshData(); render();
    } catch (error) { toast(committed ? `判断已保存，记录列表暂时无法刷新。${error.message}` : error.message,true); button.disabled = false; button.textContent = '记录判断'; }
    finally { S.saving--; }
  }
  async function saveTrialReview() {
    if (S.saving || !S.trialRecord) return false;
    const parts = [['evidence','证据'],['invalidation','失效条件'],['reflection','后续推演']].filter(([key]) => S.trialReview[key]?.trim()).map(([key,label]) => `${label}：${S.trialReview[key].trim()}`);
    if (!parts.length) { toast('先写下研究内容。'); return false; }
    S.saving++;
    let committed = false;
    try {
      await api('add_note',S.trialCase.id,`[试炼事后研究 · ${S.trialRecord.id || ''} · ${date(S.trialRecord.created)}]\n${parts.join('\n')}`);
      committed = true;
      S.trialReview = {}; render(); toast('事后研究已保存');
      await refreshData(); return true;
    } catch (error) { toast(committed ? `事后研究已保存，列表暂时无法刷新。${error.message}` : error.message,true); return committed; }
    finally { S.saving--; }
  }
  async function finishTrial() {
    if (Object.values(S.trialReview).some(value => value.trim())) {
      const saved = await saveTrialReview(); if (!saved) return;
    }
    S.trialCase = null; S.trialRecord = null; S.judgment = ''; S.trialThought = ''; S.marks = []; S.trialReview = {};
    await refreshData(); render(); toast('本次试炼已记录');
  }
  async function selectTopic(id,enter = false) {
    if (id === 'root') return;
    const previous = $('#topic-note');
    if (previous?.dataset?.draft?.startsWith('topic-note:')) S.draftText[previous.dataset.draft] = previous.value;
    const seq = ++S.topicSeq;
    S.topicId = id;
    if (enter) S.topicParent = id;
    S.topicCases = []; S.topicLoading = true;
    render();
    try {
      const result = await api('search_cases','',{topic_id:id});
      if (seq !== S.topicSeq || S.topicId !== id || S.page !== 'universe') return;
      const field = $('#topic-note');
      const key = 'topic-note:' + id;
      const currentField = field?.dataset?.draft === key;
      const focused = currentField && document.activeElement === field;
      const start = focused ? field.selectionStart : null;
      const end = focused ? field.selectionEnd : null;
      if (currentField) S.draftText[key] = field.value;
      S.topicCases = result.cases || []; S.topicLoading = false;
      render();
      if (focused) {
        const updated = $('#topic-note'); updated?.focus({preventScroll:true});
        updated?.setSelectionRange(start,end);
      }
    } catch (error) {
      if (seq !== S.topicSeq || S.topicId !== id || S.page !== 'universe') return;
      S.topicLoading = false; render(); toast(error.message,true);
    }
  }
  async function openTopic(id) {
    S.page = 'universe'; S.topicId = id;
    S.topicParent = S.topics.find(topic => topic.id === id)?.parent_id || null;
    await selectTopic(id,false);
  }
  async function saveTopicNote() {
    const field = $('#topic-note');
    const topicId = S.topicId;
    const text = field?.value.trim();
    if (!text || !topicId || field.dataset.draft !== 'topic-note:' + topicId) { toast('先写下个人研究。'); return; }
    if (S.saving) return;
    S.saving++;
    let committed = false;
    const button = $('[data-action="save-topic-note"]'); button.disabled = true; button.textContent = '正在保存…';
    try {
      await api('add_topic_note',topicId,text); committed = true; S.draftText['topic-note:' + topicId] = '';
      render(); toast('主题研究已保存');
      await refreshData();
    } catch (error) { toast(committed ? `主题研究已保存，列表暂时无法刷新。${error.message}` : error.message,true); button.disabled = false; button.textContent = '保存记录'; }
    finally { S.saving--; }
  }
  function backupDialog() {
    overlayHTML(dialog('备份与恢复',`<div class="save-error" id="backup-error"></div><div class="backup-options"><section class="backup-option"><h3>导出私密备份</h3><p>保存案例资料、原始图片、研究笔记和判断记录。</p><button class="btn primary" data-action="export-backup">导出备份</button></section><section class="backup-option"><h3>恢复案例资料</h3><p>从成长空间备份恢复。不会覆盖现有交易记录。</p><button class="btn" data-action="restore-backup">选择备份</button></section></div><div id="backup-result" class="micro" style="margin-top:16px"></div>`,null),'backup');
  }
  async function backupAction(restore) {
    if (S.saving) return;
    S.saving++;
    $$('[data-action="export-backup"],[data-action="restore-backup"]').forEach(button => { button.disabled = true; });
    try {
      const result = await api(restore ? 'restore_backup' : 'export_backup');
      if (result.cancelled || result.canceled) return;
      const restored = result.cases_restored ?? result.count ?? result.cases?.length ?? result.result?.cases;
      $('#backup-result').textContent = restore ? (restored === undefined ? '恢复完成。' : `恢复完成 · ${restored} 个案例`) : '备份已保存到所选位置。';
      await refreshData(); render(); toast(restore ? '案例资料已恢复' : '备份已保存');
    } catch (error) { $('#backup-error').textContent = error.message; }
    finally { S.saving--; $$('[data-action="export-backup"],[data-action="restore-backup"]').forEach(button => { button.disabled = false; }); }
  }
  async function dispatch(action,element,event) {
    if (S.saving && !['zoom-in','zoom-out','zoom-fit','zoom-native'].includes(action)) return;
    switch (action) {
      case 'navigate': return navigate(element.dataset.page);
      case 'retry-boot': return boot();
      case 'search-now': S.query = ($('#home-search') || $('#search-input'))?.value.trim() || ''; if (S.page !== 'library') { S.page = 'library'; render(); } return search();
      case 'load-more': return search(true,true);
      case 'toggle-filters': S.filterOpen = !S.filterOpen; render(); break;
      case 'clear-filters': S.filters = {symbol:'',timeframe:'',market:'',data_kind:''}; render(); return search(false);
      case 'reset-search': S.query = ''; S.filters = {symbol:'',timeframe:'',market:'',data_kind:''}; render(); return search(false);
      case 'quick-open': return openCase(element.dataset.id,true);
      case 'study-open': return openCase(element.dataset.id,false);
      case 'study-back': return backToSearch();
      case 'study-pane': S.studyPane = element.dataset.pane; render(); break;
      case 'case-image': S.imageIndex = Number(element.dataset.index); render(); break;
      case 'import': return importFiles();
      case 'paste': return pasteNative();
      case 'append-images': S.captureCaseId = S.case.id; S.captureMetadata = {}; await importFiles(); if (!S.pending.length) S.captureCaseId = null; return;
      case 'save-capture': return saveCapture();
      case 'capture-separate': S.captureGroup = false; break;
      case 'close-overlay': return closeOverlay();
      case 'edit-case': return editCase();
      case 'save-metadata': return saveMetadata();
      case 'save-note': return saveNote();
      case 'original': return openOriginal(true,false);
      case 'trial-original': return openOriginal(false,true);
      case 'close-original': return closeOriginal();
      case 'zoom-in': return zoomOriginal(1.25);
      case 'zoom-out': return zoomOriginal(.8);
      case 'zoom-fit': S.original.zoom = 1; S.original.x = 0; S.original.y = 0; return applyOriginalTransform();
      case 'zoom-native': {
        const image = $('#original-image');
        if (image?.width && image.naturalWidth) { S.original.zoom = image.naturalWidth / image.width; S.original.x = 0; S.original.y = 0; applyOriginalTransform(); }
        break;
      }
      case 'original-prev': case 'original-next': {
        if (!S.original || (S.original.fromTrial && !S.trialRecord)) return;
        S.original.index = Math.max(0,Math.min(S.original.case.images.length - 1,S.original.index + (action === 'original-next' ? 1 : -1)));
        S.original.zoom = 1; S.original.x = 0; S.original.y = 0;
        return renderOriginal();
      }
      case 'retry-image': S.imageError = null; if (S.original) renderOriginal(); else render(); return;
      case 'quick-study': { const id = S.original.case.id; closeOriginal(); return openCase(id,false); }
      case 'choose-trial': return chooseTrial();
      case 'start-trial': return startTrial(element.dataset.id);
      case 'trial-depth': S.trialDepth = element.dataset.depth; render(); break;
      case 'judgment': S.judgment = element.dataset.value; render(); break;
      case 'toggle-mark': if (S.trialRecord) { toast('判断位置已随原判断保存。'); return; } S.marking = !S.marking; render(); break;
      case 'save-judgment': return saveJudgment();
      case 'save-trial-review': return saveTrialReview();
      case 'finish-trial': return finishTrial();
      case 'trial-image': if (!S.trialRecord) return; S.imageIndex = Number(element.dataset.index); render(); break;
      case 'select-topic': return selectTopic(element.dataset.id,false);
      case 'universe-enter': return selectTopic(element.dataset.id,true);
      case 'universe-root': S.topicSeq++; S.topicParent = null; S.topicId = null; S.topicCases = []; S.topicLoading = false; render(); break;
      case 'open-topic': return openTopic(element.dataset.id);
      case 'save-topic-note': return saveTopicNote();
      case 'backup': return backupDialog();
      case 'export-backup': return backupAction(false);
      case 'restore-backup': return backupAction(true);
      case 'return-chart': return api('return_chart');
      case 'compare': return api('compare_case',S.case.id);
      default: break;
    }
  }
  document.addEventListener('click', event => {
    const element = event.target.closest('[data-action]');
    if (element) { event.preventDefault(); dispatch(element.dataset.action,element,event).catch(error => toast(error.message,true)); }
    else if (event.target.closest('#trial-image-wrap')) markPosition(event);
  });
  document.addEventListener('input', event => {
    const target = event.target;
    if (target.id === 'search-input') { S.query = target.value; scheduleSearch(); }
    if (target.id === 'home-search') { S.query = target.value; scheduleSearch(); }
    if (target.dataset.filter) { S.filters[target.dataset.filter] = target.value; scheduleSearch(); }
    if (target.dataset.draft) S.draftText[target.dataset.draft] = target.value;
    if (target.id === 'trial-thought') S.trialThought = target.value;
    if (target.dataset.review) S.trialReview[target.dataset.review] = target.value;
    if (target.closest('#metadata-form')) S.dirtyMetadata = true;
  });
  document.addEventListener('change', event => {
    if (event.target.dataset.filter) { S.filters[event.target.dataset.filter] = event.target.value; search(false); }
    if (event.target.name === 'capture-grouping') S.captureGroup = !!$('#capture-group')?.checked;
    if (event.target.id === 'capture-duplicate') S.duplicateAllowed = event.target.checked;
    if (event.target.closest('#metadata-form')) S.dirtyMetadata = true;
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      if (S.overlay === 'original') closeOriginal(); else if (S.overlay) closeOverlay(); else if (S.page === 'study') backToSearch();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (S.overlay) return;
      if (S.page === 'home') $('#home-search')?.focus();
      else if (S.page === 'library') $('#search-input')?.focus();
      else navigate('library').then(() => $('#search-input')?.focus());
    }
    if (event.key === 'Enter' && ['home-search','search-input'].includes(event.target.id)) {
      event.preventDefault(); const value = event.target.value; S.query = value.trim(); S.page = 'library'; render(); search(false);
    }
    if (event.key === 'Enter' && event.target.matches('.drop-box,[role="button"]')) {
      const element = event.target.closest('[data-action]'); if (element) dispatch(element.dataset.action,element,event).catch(error => toast(error.message,true));
    }
  });
  document.addEventListener('paste', event => {
    const files = Array.from(event.clipboardData?.items || []).filter(item => item.kind === 'file' && item.type.startsWith('image/')).map(item => item.getAsFile()).filter(Boolean);
    if (!files.length) return;
    event.preventDefault();
    if (S.saving) { toast('正在保存，请稍后再粘贴。'); return; }
    if (S.overlay && !['capture'].includes(S.overlay)) { toast('先关闭当前窗口，再收录截图。'); return; }
    stageFiles(files).catch(error => toast(error.message,true));
  });
  let dragDepth = 0;
  document.addEventListener('dragenter', event => {
    if (!Array.from(event.dataTransfer?.types || []).includes('Files')) return;
    event.preventDefault(); dragDepth++; $('#drop-cover').hidden = false;
  });
  document.addEventListener('dragover', event => { if (Array.from(event.dataTransfer?.types || []).includes('Files')) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; } });
  document.addEventListener('dragleave', event => { event.preventDefault(); dragDepth = Math.max(0,dragDepth - 1); if (!dragDepth) $('#drop-cover').hidden = true; });
  document.addEventListener('drop', event => {
    event.preventDefault(); dragDepth = 0; $('#drop-cover').hidden = true;
    if (S.saving || (S.overlay && S.overlay !== 'capture')) { toast('先完成当前操作，再收录截图。'); return; }
    stageFiles(event.dataTransfer?.files || []).catch(error => toast(error.message,true));
  });
  document.addEventListener('wheel', event => {
    if (!event.target.closest('#original-stage') || !S.original) return;
    event.preventDefault(); zoomOriginal(event.deltaY < 0 ? 1.12 : 1 / 1.12);
  },{passive:false});
  document.addEventListener('pointerdown', event => {
    if (!event.target.closest('#original-stage') || !S.original) return;
    const stage = $('#original-stage'); stage.setPointerCapture(event.pointerId); stage.classList.add('dragging');
    S.original.drag = {x:event.clientX,y:event.clientY,startX:S.original.x,startY:S.original.y};
  });
  document.addEventListener('pointermove', event => {
    const original = S.original;
    if (!original?.drag) return;
    original.x = original.drag.startX + event.clientX - original.drag.x;
    original.y = original.drag.startY + event.clientY - original.drag.y; applyOriginalTransform();
  });
  document.addEventListener('pointerup', () => { if (S.original) S.original.drag = null; $('#original-stage')?.classList.remove('dragging'); });
  document.addEventListener('pointercancel', () => { if (S.original) S.original.drag = null; });
  window.addEventListener('resize', () => { if (S.page === 'trial') renderMarks(); });
  navigation();
  const bridgeTimeout = setTimeout(() => {
    if (!S.ready && !S.bootFailed) $('#main').innerHTML = '<div class="failure-screen"><h1>尚未连接成长空间</h1><p>请从 Aplus 的「PA 成长空间」入口打开。</p><button class="btn" data-action="retry-boot">重试连接</button></div>';
  },12000);
  bridgeReady.then(() => clearTimeout(bridgeTimeout));
  boot();
})();
