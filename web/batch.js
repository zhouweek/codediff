(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const h = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pct = n => Number(n || 0).toFixed(1);
  let bridge, mode = 'batch', branches = [], selected = new Set(), job = null;
  let scope = 'adjusted', query = '', onlyReview = true, page = 0;
  const labels = {ready:'可分析',empty:'无独立代码',failed:'处理失败',completed:'已完成',partial:'部分完成',queued:'排队中',fetching:'获取代码中',analyzing:'分析中'};

  function init(apiBridge) {
    bridge = apiBridge;
    $('batch-controls').innerHTML = `
      <div class="section-title"><div class="step-number">01</div><div><h2>选择学生作品分支</h2><p>一个仓库，一个批次；作品标签仅用于展示。</p></div></div>
      <section class="source-card batch-source"><label for="batch-url">Git 仓库地址 *</label><input id="batch-url" type="url" placeholder="https://github.com/owner/graduation.git" maxlength="1000">
      <details class="credentials"><summary>私有仓库访问凭据</summary><label for="batch-username">Git 用户名</label><input id="batch-username" autocomplete="off" maxlength="4096"><label for="batch-token">只读访问令牌</label><input id="batch-token" type="password" autocomplete="off" maxlength="4096"></details>
      <div class="batch-toolbar"><button type="button" id="load-branches" class="button secondary">加载远端分支</button><span id="branch-feedback" role="status"></span></div>
      <div id="branch-picker" hidden><input id="branch-search" placeholder="搜索分支…" aria-label="搜索远端分支"><div class="batch-toolbar"><button type="button" id="select-visible" class="text-button">选择搜索结果</button><button type="button" id="clear-visible" class="text-button">清空搜索结果</button></div><div id="branch-list" class="branch-list"></div></div>
      <label for="manual-branches">学生分支与作品标签</label><textarea id="manual-branches" rows="6" placeholder="每行一个分支；标签用 Tab 分隔，例如：&#10;student-001&#9;作品 001&#10;student-002&#9;作品 002"></textarea><p class="small-meta">勾选同步到此处；可直接编辑。最多 100 个学生分支。</p>
      <div class="batch-settings"><label for="batch-baseline">教师模板分支（可选）<input id="batch-baseline" placeholder="例如 teacher-template" maxlength="200" list="branch-options"><datalist id="branch-options"></datalist></label><label for="review-threshold">优先复核阈值<input id="review-threshold" type="number" min="0" max="100" value="70" required><span class="small-meta">任一侧覆盖达到此值且有匹配证据。</span></label></div>
      <p id="batch-count" class="batch-count" aria-live="polite"></p><p id="template-notice" class="small-meta"></p></section>`;
    $('load-branches').addEventListener('click', loadBranches);
    $('branch-search').addEventListener('input', renderBranches);
    $('manual-branches').addEventListener('input', () => { selected = new Set(items().map(i => i.branch)); renderBranches(); counts(); });
    $('batch-baseline').addEventListener('input', counts);
    $('batch-url').addEventListener('input', () => { branches = []; $('branch-picker').hidden = true; $('branch-feedback').textContent = ''; });
    $('select-visible').addEventListener('click', () => { visible().forEach(b => selected.add(b)); sync(); });
    $('clear-visible').addEventListener('click', () => { visible().forEach(b => selected.delete(b)); sync(); });
    $('branch-list').addEventListener('change', e => { if (e.target.checked) selected.add(e.target.value); else selected.delete(e.target.value); sync(); });
    document.addEventListener('click', async e => {
      const t = e.target.closest('button');
      if (!t) return;
      if (t.dataset.mode) setMode(t.dataset.mode);
      if (t.hasAttribute('data-batch-back')) await bridge.openJob(t.dataset.batchBack);
      if (t.dataset.batchPair) await openPair(t.dataset.batchPair);
      if (t.dataset.batchScope) { scope = t.dataset.batchScope; page = 0; render(job); }
      if (t.dataset.pairScope) await openPair(t.dataset.pairScope, t.dataset.scope, t.dataset.parent);
      if (t.dataset.batchPage) { page += Number(t.dataset.batchPage); renderList(); }
      if (t.hasAttribute('data-batch-retry')) prefill(job);
      if (t.dataset.exportPackage) await exportPackage(t);
    });
    counts(); setMode('batch');
  }
  function setMode(value) {
    mode = value; $('batch-controls').hidden = mode !== 'batch'; $('pair-sources').hidden = mode !== 'pair';
    $('batch-url').required = mode === 'batch';
    $('review-threshold').required = mode === 'batch';
    ['a-url','b-url'].forEach(id => $(id).required = mode === 'pair');
    document.querySelectorAll('[data-mode]').forEach(b => b.className = 'button ' + (b.dataset.mode === mode ? 'primary' : 'secondary'));
    $('submit-analysis').textContent = mode === 'batch' ? '开始批量查重 →' : '开始双版本分析 →';
  }
  function items() { return $('manual-branches').value.split('\n').filter(s => s.trim()).map(s => { const [branch, ...label] = s.split('\t'); return {branch:branch.trim(),label:label.join('\t').trim() || branch.trim()}; }); }
  function counts() {
    const n = items().length; $('batch-count').textContent = `${n} 个学生作品 · ${n * (n-1) / 2} 个待比较作品对`;
    $('template-notice').textContent = $('batch-baseline').value.trim() ? '保留原始与扣模板后的两种口径；只扣除确认匹配的模板片段。' : '未扣除教师模板，统一脚手架可能抬高相似度。';
  }
  function repository() { return {url:$('batch-url').value.trim(), username:$('batch-username').value,token:$('batch-token').value}; }
  function payload(options) {
    const list = items(), names = list.map(i => i.branch), baseline = $('batch-baseline').value.trim();
    if (list.length < 2 || list.length > 100) throw new Error('请选择 2–100 个学生分支。');
    if (new Set(names).size !== names.length) throw new Error('学生分支不能重复。');
    if (names.includes(baseline)) throw new Error('教师模板不能同时作为学生作品。');
    return {name:$('task-name').value.trim(), repository:repository(),branches:list,baseline,review_threshold:Number($('review-threshold').value),options};
  }
  function visible() { const q = $('branch-search').value.toLowerCase(); return branches.filter(b => b.toLowerCase().includes(q)); }
  function renderBranches() {
    $('branch-list').innerHTML = visible().map(b => `<label><input type="checkbox" value="${h(b)}" ${selected.has(b) ? 'checked' : ''}>${h(b)}</label>`).join('') || '<p>没有符合搜索条件的分支。</p>';
  }
  function sync() {
    const existing = new Map(items().map(i => [i.branch, i.label]));
    $('manual-branches').value = [...selected].map(b => b + (existing.get(b) && existing.get(b) !== b ? '\t' + existing.get(b) : '')).join('\n');
    renderBranches(); counts();
  }
  async function loadBranches() {
    $('load-branches').disabled = true; $('branch-feedback').textContent = '正在读取远端分支…';
    try {
      const data = await bridge.api('/api/branches', {method:'POST',body:{repository:repository()}});
      branches = data.branches.map(b => typeof b === 'string' ? b : b.branch);
      $('branch-picker').hidden = false;
      $('branch-options').innerHTML = branches.map(b => `<option value="${h(b)}"></option>`).join('');
      $('branch-feedback').textContent = `已读取 ${branches.length} 个分支；请勾选学生作品。`; renderBranches();
    } catch (e) { $('branch-feedback').textContent = e.message; }
    finally { $('load-branches').disabled = false; }
  }
  function prefill(value) {
    if (value.demo) { bridge.startBatchDemo(); return; }
    setMode('batch'); $('batch-url').value = value.sources.repository.url;
    $('manual-branches').value = value.sources.branches.map(i => i.branch + '\t' + i.label).join('\n');
    selected = new Set(value.sources.branches.map(i => i.branch)); branches = [];
    $('branch-picker').hidden = true; $('branch-feedback').textContent = '';
    $('batch-baseline').value = value.sources.baseline; $('review-threshold').value = value.sources.review_threshold;
    $('batch-token').value = ''; $('batch-username').value = '';
    bridge.fillOptions(value); counts(); bridge.switchView('new');
  }
  function reset() {
    job = null; selected.clear(); branches = []; query = ''; page = 0; scope = 'adjusted'; onlyReview = true;
    $('branch-picker').hidden = true; $('branch-feedback').textContent = ''; counts(); setMode('batch');
  }
  function header(value, ready) {
    return `<button data-view="history" class="back-link">← 返回分析记录</button><div class="page-heading"><div><div class="eyebrow">毕业实习作品批次 ${value.demo ? ' / 模拟数据 · 真实引擎' : ''}</div><h1>${h(value.name)}</h1><p>${h(value.sources.repository.url)} · ${value.sources.branches.length} 个学生分支</p></div><div class="heading-actions"><span class="status ${h(value.status)}">${h(labels[value.status])}</span>${ready ? `<button class="button primary" data-export-package="${h(value.id)}">导出带证据报告包</button><a class="button secondary" href="/api/jobs/${h(value.id)}/report" download>导出批次 HTML</a>` : ''}<button class="button secondary" data-batch-retry>重新分析</button></div></div>`;
  }
  async function exportPackage(button) {
    if (button.disabled) return;
    button.disabled = true; button.textContent = '正在生成全部证据…';
    try {
      const response = await fetch(`/api/jobs/${button.dataset.exportPackage}/report-package`, {credentials:'same-origin'});
      if (!response.ok) throw new Error((await response.json()).error || '报告包导出失败。');
      const blob = await response.blob();
      if ($('app-screen').hidden) return;
      const url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = `codelens-${button.dataset.exportPackage}-reports.zip`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      bridge.toast('报告包已下载。请完整解压后打开 index.html，矩阵可链接到代码证据。');
    } catch (e) { bridge.toast(e.message); }
    finally { button.disabled = false; button.textContent = '导出带证据报告包'; }
  }
  function render(value) {
    if (job?.id !== value.id) { scope = 'adjusted'; query = ''; onlyReview = true; page = 0; }
    job = value;
    const ready = ['completed','partial'].includes(value.status), r = value.result;
    const root = $('result-content');
    if (!ready) {
      root.innerHTML = header(value, false) + `<div class="stage-card">${value.status === 'failed' ? '' : '<span class="spinner"></span>'}<h2>${h(labels[value.status])}</h2><p>${h(value.message)}</p></div>`; return;
    }
    const s = r.summary;
    const pairMap = new Map(r.pairs.map(p => [p.id,p]));
    root.innerHTML = header(value, true) + `
      ${value.status === 'partial' ? '<div class="callout warning"><p>结果部分完成。失败与不可判定的作品对显示 N/A，请先检查作品状态。</p></div>' : ''}
      <div class="metric-grid">${[['学生作品',s.students,'可分析 '+s.ready+' · 无独立代码 '+s.empty+' · 失败 '+s.failed_students],['作品对',s.pairs,'已完成 '+s.completed_pairs+' · N/A '+s.unavailable_pairs+' · 失败 '+s.failed_pairs],['优先复核',s.review_pairs,'按扣模板口径 · 任一侧 ≥ '+r.review_threshold+'%'],['教师模板',r.baseline ? '已扣除' : '未选择',r.baseline?.branch || '统一脚手架可能抬高相似度']].map(([title,n,note]) => `<section class="metric-card"><span class="metric-label">${h(title)}</span><div class="metric-value"><strong>${h(n)}</strong></div><span class="metric-foot">${h(note)}</span></section>`).join('')}</div>
      <div class="callout"><p>相似性是人工复核线索，不是抄袭裁定。矩阵表示整体覆盖；名单按两侧较高覆盖排序，以发现小作品被大作品包含的情况。独立 HTML 是总览；“导出带证据报告包”包含全部已完成作品对的两种口径证据，可离线点击。</p></div>
      ${r.warnings.map(w => `<div class="callout warning"><p>${h(w)}</p></div>`).join('')}
      <section class="detail-section"><div class="detail-title"><div><h2>作品相似度矩阵</h2><p>横纵滚动查看全班；点击数值查看证据。编号对应下方作品表。</p></div><div class="batch-toolbar"><button class="button ${scope === 'adjusted' ? 'primary' : 'secondary'}" data-batch-scope="adjusted">${r.baseline ? '扣模板后' : '有效代码'}</button><button class="button ${scope === 'raw' ? 'primary' : 'secondary'}" data-batch-scope="raw">原始口径</button></div></div>
      <div class="batch-legend" aria-label="矩阵颜色图例"><strong>整体相似覆盖率</strong><span class="batch-legend-scale" aria-hidden="true"></span><span>0% → 100% · 颜色越深覆盖越高</span><span>N/A 不可判定 / 失败 · — 同一作品</span></div>
      <div class="batch-matrix"><table><thead><tr><th>作品编号 / 标签</th>${r.students.map((st,i) => `<th title="${h(st.label)}">${i+1}</th>`).join('')}</tr></thead><tbody>${r.students.map((st,i) => `<tr><th title="${h(st.branch)}">${i+1} · ${h(st.label)}</th>${r.students.map((other,j) => {
        if (i === j) return '<td class="diagonal">—</td>';
        const p = pairMap.get(`${Math.min(i,j)}-${Math.max(i,j)}`), m = p?.[scope];
        if (!m) return `<td title="${h(p?.reason)}" class="unavailable">N/A</td>`;
        const desc = `A ${p.a_label} ↔ B ${p.b_label} · 整体 ${pct(m.coverage)}% · A ${pct(m.a_coverage)}% / B ${pct(m.b_coverage)}%`;
        return `<td><button data-batch-pair="${p.id}" title="${h(desc)}" aria-label="${h(desc)}" style="background:color-mix(in srgb,var(--accent) ${m.coverage * .4}%,var(--surface))">${pct(m.coverage)}</button></td>`;
      }).join('')}</tr>`).join('')}</tbody></table></div></section>
      <section class="detail-section"><div class="detail-title"><div><h2>作品对与优先复核名单</h2><p>优先复核筛选使用当前口径；顶部统计固定使用扣模板口径。</p></div></div><div class="table-card"><div class="filter-row"><input id="batch-pair-search" placeholder="按作品名或分支搜索…" value="${h(query)}"><label class="batch-check"><input id="batch-only-review" type="checkbox" ${onlyReview ? 'checked' : ''}>仅优先复核</label></div><div id="batch-pair-list"></div></div></section>
      <section class="detail-section"><div class="detail-title"><h2>作品状态与固定版本</h2></div><div class="table-card table-wrap"><table><thead><tr><th>作品 / 分支 / SHA</th><th>状态</th><th>原始 / 模板 / 独立 token</th><th>跳过 / 原因</th></tr></thead><tbody>${r.students.map((st,i) => `<tr><td><strong>${i+1} · ${h(st.label)}</strong><span class="row-subtitle">${h(st.branch)}</span><span class="sha">${h(st.commit || '未获取')}</span></td><td>${h(labels[st.status])}</td><td>${st.tokens ?? '—'} / ${st.template_tokens ?? '—'} / ${st.independent_tokens ?? '—'}</td><td>${st.skipped ?? '—'}<p>${h(st.error)}</p>${st.skipped_files?.length ? `<details><summary>跳过路径与原因</summary>${st.skipped_files.map(f => `<p>${h(f.path)} · ${h(f.reason)}</p>`).join('')}</details>` : ''}</td></tr>`).join('')}</tbody></table></div></section>
      <details class="method-details"><summary>检测设置、公式与局限</summary><div class="method-body"><p>最小连续片段 ${r.options.min_match} token；标识符归一化 ${r.options.normalize_identifiers ? '开启' : '关闭'}；字面量归一化 ${r.options.normalize_literals ? '开启' : '关闭'}。排除：${h(r.options.excludes.join('；') || '默认规则')}。</p><p>每侧覆盖 = 确认匹配 token 并集 / 相应口径有效 token；整体按两侧有效 token 数加权。模板只扣匹配区间；开启改名归一化后，名称不同但结构相同的模板也可能被扣除。仅分析同语言连续片段，不能可靠识别任意语句换序或跨语言语义。</p>${r.baseline ? `<p>教师模板：${h(r.baseline.branch)} · ${h(r.baseline.commit)}</p>` : ''}<p>算法 ${h(r.algorithm)}。按需证据来自本批次固定快照。</p></div></details>`;
    $('batch-pair-search').addEventListener('input', e => { query = e.target.value; page = 0; renderList(); });
    $('batch-only-review').addEventListener('change', e => { onlyReview = e.target.checked; page = 0; renderList(); });
    renderList();
  }
  function renderList() {
    const r = job.result, q = query.toLowerCase();
    const rows = r.pairs.filter(p => {
      const m = p[scope], names = [p.a_label,p.b_label,r.students[p.a].branch,r.students[p.b].branch].join(' ').toLowerCase();
      return names.includes(q) && (!onlyReview || (m?.fragments && Math.max(m.a_coverage,m.b_coverage) >= r.review_threshold));
    }).sort((a,b) => Math.max(b[scope]?.a_coverage || 0,b[scope]?.b_coverage || 0) - Math.max(a[scope]?.a_coverage || 0,a[scope]?.b_coverage || 0) || (b[scope]?.coverage || 0) - (a[scope]?.coverage || 0));
    const pages = Math.max(1,Math.ceil(rows.length/30)); page = Math.min(Math.max(0,page),pages-1);
    $('batch-pair-list').innerHTML = `<div class="table-wrap"><table><thead><tr><th>作品对</th><th>当前整体覆盖</th><th>A / B 覆盖</th><th>原始 → 扣模板</th><th></th></tr></thead><tbody>${rows.slice(page*30,(page+1)*30).map(p => {
      const m = p[scope]; return `<tr><td>${h(p.a_label)} ↔ ${h(p.b_label)}${p.same_commit ? '<span class="row-subtitle">相同提交</span>' : ''}</td><td>${m ? pct(m.coverage)+'%' : 'N/A'}</td><td>${m ? pct(m.a_coverage)+'% / '+pct(m.b_coverage)+'%' : h(p.reason)}</td><td>${p.raw ? pct(p.raw.coverage)+'% → '+pct(p.adjusted.coverage)+'%' : 'N/A'}</td><td>${m ? `<button data-batch-pair="${p.id}" class="button secondary small">查看证据 →</button>` : h(labels[p.status] || '不可判定')}</td></tr>`;
    }).join('') || '<tr><td colspan="5">没有符合条件的作品对。可取消“仅优先复核”查看全部结果。</td></tr>'}</tbody></table></div><div class="batch-toolbar batch-pagination"><span>${rows.length} 个作品对 · 第 ${page+1}/${pages} 页</span><button class="button secondary small" data-batch-page="-1" ${page === 0 ? 'disabled' : ''}>上一页</button><button class="button secondary small" data-batch-page="1" ${page+1 === pages ? 'disabled' : ''}>下一页</button></div>`;
  }
  async function openPair(id, selectedScope = scope, parent = job?.id) {
    try {
      bridge.toast('正在读取固定快照的代码证据…');
      const value = await bridge.api(`/api/jobs/${parent}/pairs/${id}?scope=${selectedScope}`);
      bridge.showPair(value);
    } catch (e) { bridge.toast(e.message); }
  }
  function pairBanner(value) {
    const c = value.result.batch_context;
    const rows = ['a','b'].map(side => {
      const raw = c.raw[side+'_tokens'], after = c.adjusted[side+'_tokens'], matched = c.adjusted[side+'_matched'];
      return `<tr><td>${side.toUpperCase()} · ${h(value.sources[side].label || value.sources[side].branch)}</td><td>${raw}</td><td>${raw-after}</td><td>${after}</td><td>${matched} / ${after} = ${pct(c.adjusted[side+'_coverage'])}%</td></tr>`;
    }).join('');
    return `<div class="callout"><p><button class="text-button" data-batch-back="${h(value.parent_batch_id)}">← 返回批次矩阵</button> · 原始 ${pct(c.raw.coverage)}% → 扣模板后 ${pct(c.adjusted.coverage)}% · 当前：${value.scope === 'raw' ? '原始' : '扣模板后'}口径</p><div class="batch-toolbar">${['adjusted','raw'].map(s => `<button class="button secondary small" data-pair-scope="${h(value.batch_pair_id)}" data-parent="${h(value.parent_batch_id)}" data-scope="${s}">${s === 'raw' ? '原始证据' : '扣模板后证据'}</button>`).join('')}</div></div><section class="template-accounting"><h3>教师模板扣除明细</h3><div class="table-wrap"><table><thead><tr><th>作品</th><th>原始有效 token</th><th>扣除模板 token</th><th>扣后有效 token</th><th>扣后匹配量 / 覆盖率</th></tr></thead><tbody>${rows}</tbody></table></div><p>模板同时影响匹配量与分母，百分比不一定下降；剩余代码全部匹配时仍为 100%。只扣确认模板区间，原始行号不变。</p></section>`;
  }
  window.BatchUI = {init,render,payload,prefill,reset,setMode,pairBanner,getMode:() => mode};
})();
