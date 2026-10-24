(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const h = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const percent = n => Number(n || 0).toFixed(1);
  const safeWidth = n => Math.max(0, Math.min(100, Number(n) || 0));
  const date = value => value ? new Date(value).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
  const states = {queued:'排队中',fetching:'获取代码中',analyzing:'分析中',completed:'已完成',partial:'部分完成',failed:'失败'};
  let session = null, register = false, view = 'new', jobs = [], current = null, currentId = null, pairId = null, submitting = false, refreshing = false;
  let filter = {path:'', language:'', minimum:0};
  let toastTimer;

  async function api(path, {method='GET', body, quiet=false} = {}) {
    const response = await fetch(path, {
      method, credentials:'same-origin', cache:'no-store',
      headers: body !== undefined ? {'Content-Type':'application/json','X-CSRF-Token':session?.csrf || ''} : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    let data;
    try { data = await response.json(); } catch { throw new Error('服务返回内容异常，请确认本地服务仍在运行。'); }
    if (!response.ok) {
      if (response.status === 401 && session && !['/api/login','/api/register'].includes(path)) {
        showAuth(); if (!quiet) toast('会话已失效，请重新登录。');
      }
      throw new Error(data.error || `请求失败（${response.status}）`);
    }
    return data;
  }

  function toast(text) {
    $('toast').textContent = text; $('toast').hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 4500);
  }
  function showAuth() {
    session = null; current = null; currentId = null; jobs = [];
    $('app-screen').hidden = true; $('auth-screen').hidden = false; $('loading-screen').hidden = true;
    $('password').value = ''; $('auth-error').textContent = '';
    $('result-content').innerHTML = ''; $('history-content').innerHTML = '';
    $('history-count').textContent = '0'; $('tab-count').textContent = '0';
    $('analysis-form').reset();
    window.BatchUI.reset();
    ['a','b'].forEach(label => $(label+'-token').value = '');
    $('form-error').textContent = '';
  }
  async function showApp(data) {
    session = data; $('auth-screen').hidden = true; $('app-screen').hidden = false; $('loading-screen').hidden = true;
    $('user-label').textContent = data.username; $('avatar').textContent = data.username.slice(0,1).toUpperCase();
    switchView('new'); await refresh();
  }
  function switchView(next) {
    view = next;
    ['new','history','result'].forEach(key => $(key + '-view').hidden = next !== key);
    document.querySelectorAll('.sidebar [data-view]').forEach(button => button.classList.toggle('active', button.dataset.view === next || (next === 'result' && button.dataset.view === 'history')));
    $('page-label').textContent = {new:'分析工作台', history:'分析记录', result:'分析详情'}[next];
    if (next === 'history') renderHistory();
    window.scrollTo({top:0,behavior:'instant'});
  }
  function status(job) {
    const symbol = job.status === 'completed' ? icon('check') : (['failed','partial'].includes(job.status) ? icon('info') : '<span class="spinner"></span>');
    return `<span class="status ${h(job.status)}">${symbol}${h(states[job.status] || job.status)}</span>`;
  }
  function summaryText(job) { return job.summary ? (job.kind === 'batch' ? `${job.summary.review_pairs} 对优先复核` : `${percent(job.summary.coverage)}%`) : '—'; }
  function historySource(job) {
    return job.kind === 'batch' ? h(job.sources.repository.url) + ' · ' + job.sources.branches.length + ' 个学生分支' : h(sourceShort(job.sources.a)) + '<br>' + h(sourceShort(job.sources.b));
  }
  function sourceShort(source) {
    return source.url.replace(/^https:\/\//, '').replace(/\.git$/, '') + ' · ' + (source.branch || '默认分支');
  }
  function renderHistory() {
    if (!jobs.length) {
      $('history-content').innerHTML = `<div class="empty-state">${icon('history')}<h3>还没有分析记录</h3><p>选择两个代码版本，或用示例体验完整的分析流程。</p><button data-view="new" class="button primary">新建分析 ${icon('arrow')}</button></div>`;
      return;
    }
    $('history-content').innerHTML = `<div class="table-card"><div class="table-toolbar"><div><h2>我的分析任务 <span class="tag neutral">${jobs.length}</span></h2><p>仅显示当前账号的任务；运行状态自动更新。</p></div><button data-refresh class="button secondary small">刷新记录</button></div><div class="table-wrap"><table><thead><tr><th>任务 / 代码来源</th><th>状态</th><th>覆盖 / 复核</th><th>创建时间</th><th></th></tr></thead><tbody>${jobs.map(job => `<tr><td><button class="row-button" data-job="${h(job.id)}"><span class="row-title">${h(job.name)} <span class="tag neutral">${job.kind === 'batch' ? '批次' : '双版本'}</span> ${job.demo ? '<span class="tag neutral">示例</span>' : ''}</span><span class="row-subtitle">${historySource(job)}</span></button></td><td>${status(job)}</td><td><span class="coverage-value">${summaryText(job)}</span><span class="row-subtitle">${job.summary ? `${job.summary.pairs} 个${job.kind === 'batch' ? '作品' : '文件'}对` : h(job.message)}</span></td><td class="small-meta">${h(date(job.created_at))}</td><td><button class="button secondary small" data-job="${h(job.id)}">查看 ${icon('arrow')}</button></td></tr>`).join('')}</tbody></table></div></div>`;
  }
  async function refresh() {
    if (!session || refreshing) return;
    refreshing = true;
    try {
      const data = await api('/api/jobs', {quiet:true});
      jobs = data.jobs; $('history-count').textContent = jobs.length; $('tab-count').textContent = jobs.length;
      if (view === 'history') renderHistory();
      if (currentId && view === 'result' && !['completed','partial','failed'].includes(current?.status)) {
        current = await api('/api/jobs/' + currentId, {quiet:true});
        renderCurrent();
      }
    } catch (error) { if (session) toast(error.message); }
    finally { refreshing = false; }
  }
  async function openJob(id) {
    currentId = id; pairId = null; filter = {path:'',language:'',minimum:0};
    try {
      current = await api('/api/jobs/' + id);
      if (!session) { current = null; currentId = null; return; }
      switchView('result'); renderCurrent();
    } catch (error) { toast(error.message); }
  }
  function origin(source, label) {
    return `<div class="origin"><span class="source-mark">${label}</span><div class="origin-info"><strong>${h(source.url)}</strong><p>${icon('branch')} 请求：${h(source.branch || '默认分支')} ${source.resolved_branch ? `· 解析：${h(source.resolved_branch)}` : ''}</p>${source.commit ? `<span class="sha">提交 ${h(source.commit)}</span>` : '<span class="sha">完成获取后记录实际提交</span>'}</div></div>`;
  }
  function baseHeading(job, finished) {
    return `<button data-view="history" class="back-link">← 返回分析记录</button><div class="page-heading result-heading"><div><div class="eyebrow">ANALYSIS / ${job.demo ? '离线示例 · 真实引擎计算' : '代码相似性分析'}</div><h1>${h(job.name)}</h1><p>${h(date(job.created_at))} 创建 · ${finished ? `检测版本 ${h(job.result.algorithm)}` : '状态自动更新，可稍后从分析记录返回'}</p></div><div class="heading-actions">${status(job)}${finished ? `<a class="button primary" href="${h(job.report_url || '/api/jobs/'+job.id+'/report')}" download>${icon('download')}导出 HTML</a>` : ''}${job.parent_batch_id ? '' : '<button data-retry class="button secondary">重新分析</button>'}</div></div>${job.parent_batch_id ? window.BatchUI.pairBanner(job) : ''}<div class="result-origins">${origin(job.sources.a,'A')}${origin(job.sources.b,'B')}</div>`;
  }
  function renderCurrent() {
    if (!current) return;
    if (current.kind === 'batch') { window.BatchUI.render(current); return; }
    const finished = current.status === 'completed';
    if (!finished) {
      const step = {queued:0,fetching:1,analyzing:2}[current.status];
      $('result-content').innerHTML = baseHeading(current, false) +
        (current.status === 'failed' ? `<div class="stage-card">${icon('info')}<h2>这次分析未能完成</h2><p>${h(current.message)}</p><p>可以重新使用这些参数；访问令牌需要再次输入。</p><button data-retry class="button primary">修改参数并重试 ${icon('arrow')}</button></div>` :
          `<div class="stage-card"><span class="spinner"></span><h2>${h(states[current.status])}</h2><p>${h(current.message)}</p><div class="stages">${['排队中','获取代码','分析片段','生成结果'].map((name,i) => `<div class="stage ${i <= step ? 'active' : ''}"><span></span><b>0${i+1} ${name}</b></div>`).join('')}</div><p>这里只展示实际执行阶段，完成后将显示匹配证据。</p></div>`);
      return;
    }
    const result = current.result, s = result.summary;
    const level = s.coverage >= 70 ? ['高相似','gold'] : (s.coverage >= 30 ? ['中相似',''] : ['低相似','teal']);
    $('result-content').innerHTML = baseHeading(current, true) + `
      <div class="metric-grid">
        <section class="metric-card highlight"><span class="metric-label">整体相似覆盖率 <span class="tag ${level[1]}">${level[0]}</span></span><div class="metric-value"><strong>${percent(s.coverage)}</strong><span>%</span></div><div class="progress-bar"><span style="width:${safeWidth(s.coverage)}%"></span></div><span class="metric-foot">两侧匹配 token 的加权覆盖</span></section>
        <section class="metric-card"><span class="metric-label">A → B 覆盖率 ${icon('arrow')}</span><div class="metric-value"><strong>${percent(s.a_coverage)}</strong><span>%</span></div><div class="progress-bar"><span style="width:${safeWidth(s.a_coverage)}%"></span></div><span class="metric-foot">${s.a_matched.toLocaleString()} / ${s.a_tokens.toLocaleString()} token · ${s.a_files} 个文件</span></section>
        <section class="metric-card teal"><span class="metric-label">B → A 覆盖率 ${icon('arrow')}</span><div class="metric-value"><strong>${percent(s.b_coverage)}</strong><span>%</span></div><div class="progress-bar"><span style="width:${safeWidth(s.b_coverage)}%"></span></div><span class="metric-foot">${s.b_matched.toLocaleString()} / ${s.b_tokens.toLocaleString()} token · ${s.b_files} 个文件</span></section>
        <section class="metric-card"><span class="metric-label">确认匹配文件对 ${icon('file')}</span><div class="metric-value"><strong>${s.pairs}</strong><span>对</span></div><p class="metric-foot">${s.fragments} 个确认片段</p><span class="metric-foot">${s.skipped} 个跳过文件 · 可查看原因</span></section>
      </div>
      <div class="callout">${icon('info')}<p>相似覆盖比例不等于抄袭概率。分支继承、合法开源复用与模板均可能造成匹配，请结合下面的代码证据人工复核。<br>导出报告包含匹配源码，请按仓库权限分享。</p></div>
      ${result.warnings.map(w => `<div class="callout warning">${icon('info')}<p>${h(w)}</p></div>`).join('')}
      <section class="detail-section"><div class="detail-title"><div><h2>匹配文件对</h2><p>按覆盖率从高到低，选择文件对查看原始行号与片段。</p></div><span class="tag neutral">${s.pairs} 对 / ${s.fragments} 段</span></div><div class="table-card"><div class="filter-row"><div class="input-icon">${icon('search')}<input id="filter-path" placeholder="搜索文件路径…" value="${h(filter.path)}" aria-label="搜索文件路径"></div><select id="filter-language" aria-label="筛选语言"><option value="">全部语言</option>${[...new Set(result.pairs.map(p => p.language))].sort().map(l => `<option ${filter.language === l ? 'selected' : ''} value="${h(l)}">${h(l)}</option>`).join('')}</select><select id="filter-minimum" aria-label="最低覆盖率">${[[0,'全部覆盖率'],[30,'至少 30%'],[70,'至少 70%']].map(([n,label]) => `<option value="${n}" ${filter.minimum === n ? 'selected' : ''}>${label}</option>`).join('')}</select><button data-clear-filter class="text-button">重置</button></div><div id="pair-table"></div></div></section>
      <div id="evidence-container"></div>
      <section class="detail-section"><div class="detail-title"><div><h2>分析范围</h2><p>参与分析的有效代码才计入分母。</p></div><span class="tag neutral">${result.languages.length} 种语言</span></div><div class="table-card"><div class="table-wrap language-table"><table><thead><tr><th>语言</th><th>A 文件 / token</th><th>B 文件 / token</th><th>A 匹配 token</th><th>B 匹配 token</th></tr></thead><tbody>${result.languages.map(l => `<tr><td>${h(l.language)}</td><td>${l.a_files} / ${l.a_tokens.toLocaleString()}</td><td>${l.b_files} / ${l.b_tokens.toLocaleString()}</td><td>${l.a_matched.toLocaleString()}</td><td>${l.b_matched.toLocaleString()}</td></tr>`).join('')}</tbody></table></div></div></section>
      <details class="method-details"><summary>检测设置、计算口径与局限</summary><div class="method-body"><p><strong>本次设置：</strong>忽略空白与注释 · 标识符归一化 ${result.options.normalize_identifiers ? '开启' : '关闭'} · 字面量归一化 ${result.options.normalize_literals ? '开启' : '关闭'} · 最小片段 ${result.options.min_match} token。</p><p><strong>用户排除：</strong>${h(result.options.excludes.join('；') || '无')}；默认跳过依赖、构建目录、锁文件、压缩文件。</p><p><strong>双向覆盖率：</strong>该侧与另一侧匹配的有效 token 并集 / 该侧参与分析的有效 token；整体按两侧 token 数加权。文件对只计算对应两文件；重复覆盖不重复计数。高/中/低分界为 70% / 30%，只是界面分级。</p><p><strong>能力边界：</strong>可检出同语言连续片段、标识符修改及函数/片段整体换序；不能可靠检出任意语句重排、数据流和跨语言算法等价。HTML 按模板整体分析。未发现匹配不代表没有抄袭，常见写法匹配不代表未经授权复制。</p></div></details>
      <details class="skip-details"><summary>查看跳过的文件与原因 · ${s.skipped} 个</summary><div class="table-wrap"><table><thead><tr><th>来源</th><th>路径</th><th>原因</th></tr></thead><tbody>${['a','b'].flatMap(label => result.skipped[label].map(item => `<tr><td>${label.toUpperCase()}</td><td>${h(item.path)}</td><td>${h(item.reason)}</td></tr>`)).join('') || '<tr><td colspan="3">没有跳过文件。</td></tr>'}</tbody></table></div></details>`;
    $('filter-path').addEventListener('input', event => { filter.path = event.target.value; renderPairs(); });
    $('filter-language').addEventListener('change', event => { filter.language = event.target.value; renderPairs(); });
    $('filter-minimum').addEventListener('change', event => { filter.minimum = Number(event.target.value); renderPairs(); });
    renderPairs();
    if (pairId) renderEvidence();
  }
  function renderPairs() {
    if (!current?.result) return;
    const pairs = current.result.pairs.filter(p =>
      (!filter.language || p.language === filter.language) &&
      p.coverage >= filter.minimum &&
      (p.a_path + ' ' + p.b_path).toLowerCase().includes(filter.path.toLowerCase())
    );
    if (!pairs.length) {
      const noMatches = !current.result.pairs.length;
      $('pair-table').innerHTML = `<div class="empty-state table-empty">${icon(noMatches ? 'check' : 'search')}<h3>${noMatches ? '未发现达到阈值的匹配' : '没有符合筛选条件的文件对'}</h3><p>${noMatches ? '请结合最小片段长度、共同语言与跳过项解读，结果不能证明没有抄袭。' : '试试其他路径、语言，或重置筛选条件。'}</p>${noMatches ? '' : '<button data-clear-filter class="button secondary small">重置筛选</button>'}</div>`;
    } else {
      $('pair-table').innerHTML = `<div class="table-wrap"><table><thead><tr><th>文件对</th><th>语言 / 特征</th><th>相似覆盖率</th><th>匹配片段</th><th></th></tr></thead><tbody>${pairs.map(p => `<tr><td><button class="row-button" data-pair="${h(p.id)}"><span class="pair-main">A · ${h(p.a_path)}</span><span class="pair-arrow">↕</span><span class="pair-main">B · ${h(p.b_path)}</span></button></td><td><span class="tag neutral">${h(p.language)}</span>${p.reordered ? '<span class="row-subtitle">↕ 片段整体换序</span>' : ''}${p.relocated ? '<span class="row-subtitle">↗ 文件路径不同</span>' : ''}</td><td class="coverage-cell"><span class="coverage-value">${percent(p.coverage)}%</span><div class="small-progress"><span style="width:${safeWidth(p.coverage)}%"></span></div><span class="small-meta">A ${percent(p.a_coverage)}% / B ${percent(p.b_coverage)}%</span></td><td>${p.evidence.length} 段</td><td><button class="button secondary small" data-pair="${h(p.id)}">查看证据 ${icon('arrow')}</button></td></tr>`).join('')}</tbody></table></div>`;
    }
    if (pairId && !pairs.some(p => p.id === pairId)) { pairId = null; $('evidence-container').innerHTML = ''; }
  }
  function sourceCode(snippet) {
    return snippet.code.split('\n').map((line, index) => {
      const n = snippet.context_start + index;
      return `<div class="code-line ${n >= snippet.start_line && n <= snippet.end_line ? 'matched' : ''}"><span class="line-number">${n}</span><code>${h(line) || ' '}</code></div>`;
    }).join('');
  }
  function renderEvidence() {
    const pair = current.result.pairs.find(p => p.id === pairId);
    if (!pair) return;
    $('evidence-container').innerHTML = `<section class="evidence-panel evidence-view wrap-lines"><div class="evidence-head"><div><h3>原始代码证据 <span class="tag neutral">${h(pair.language)}</span></h3><p>A · ${h(pair.a_path)}<br>B · ${h(pair.b_path)}</p></div><button data-close-evidence class="icon-button" aria-label="关闭证据">✕</button></div><div class="evidence-body"><div class="evidence-tools" role="group" aria-label="代码阅读设置"><label><input type="checkbox" data-evidence-wrap checked>自动换行</label><label><input type="checkbox" data-evidence-sync checked>双栏同步滚动</label><span>同步同一片段的水平与垂直滚动比例。</span></div>${pair.evidence.map((e,i) => `<details class="fragment" ${i === 0 ? 'open' : ''}><summary>片段 ${i+1}<span class="tag teal">${h(e.kind)}</span><span class="fragment-meta">${e.tokens} token · A L${e.a.start_line}–${e.a.end_line} ↔ B L${e.b.start_line}–${e.b.end_line}</span></summary><div class="code-columns">${['a','b'].map(label => `<div class="code-side"><div class="code-path"><span>来源 ${label.toUpperCase()}</span><span>L${e[label].start_line}–${e[label].end_line}</span></div><div class="code-box">${sourceCode(e[label])}</div><button data-expand-code class="code-expand">展开完整片段 / 收起</button></div>`).join('')}</div></details>`).join('')}</div></section>`;
    window.CodeLensEvidence.mount($('evidence-container'));
  }
  function options() {
    return {normalize_identifiers:$('normalize-identifiers').checked, normalize_literals:$('normalize-literals').checked,
      min_match:Number($('min-match').value), excludes:$('excludes').value.split('\n').map(s => s.trim()).filter(Boolean)};
  }
  function payload() {
    return {name:$('task-name').value.trim(), options:options(),
      ...Object.fromEntries(['a','b'].map(label => [label,{url:$(label+'-url').value.trim(),branch:$(label+'-branch').value.trim(),
        username:$(label+'-username').value,token:$(label+'-token').value}]))};
  }
  function prefill(job) {
    if (job.kind === 'batch') { window.BatchUI.prefill(job); return; }
    window.BatchUI.setMode('pair');
    if (job.demo) { startDemo(); return; }
    ['a','b'].forEach(label => {
      $(label+'-url').value = job.sources[label].url; $(label+'-branch').value = job.sources[label].branch;
      $(label+'-username').value = ''; $(label+'-token').value = '';
    });
    $('task-name').value = job.name; $('normalize-identifiers').checked = job.options.normalize_identifiers;
    $('normalize-literals').checked = job.options.normalize_literals; $('min-match').value = job.options.min_match;
    $('excludes').value = job.options.excludes.join('\n'); $('form-error').textContent = '';
    switchView('new'); toast('已填入原任务参数，令牌请重新输入。');
  }
  async function startDemo() {
    if (submitting) return;
    submitting = true; $('demo-button').disabled = true;
    try {
      const created = await api('/api/demo', {method:'POST',body:{options:options()}});
      await openJob(created.id); await refresh();
    } catch (error) { toast(error.message); }
    finally { submitting = false; $('demo-button').disabled = false; }
  }
  async function startBatchDemo() {
    if (submitting) return;
    submitting = true; $('batch-demo-button').disabled = true;
    try {
      const created = await api('/api/batch-demo', {method:'POST',body:{options:options(),review_threshold:Number($('review-threshold').value)}});
      await openJob(created.id); await refresh();
    } catch (error) { toast(error.message); }
    finally { submitting = false; $('batch-demo-button').disabled = false; }
  }
  window.BatchUI.init({
    api,toast,openJob,switchView,startBatchDemo,
    fillOptions(job) {
      $('task-name').value = job.name; $('normalize-identifiers').checked = job.options.normalize_identifiers;
      $('normalize-literals').checked = job.options.normalize_literals; $('min-match').value = job.options.min_match;
      $('excludes').value = job.options.excludes.join('\n'); $('form-error').textContent = '';
    },
    showPair(job) { if (!session) return; current = job; currentId = job.id; pairId = null; filter = {path:'',language:'',minimum:0}; switchView('result'); renderCurrent(); },
  });
  $('auth-toggle').addEventListener('click', () => {
    register = !register;
    $('auth-title').textContent = register ? '创建你的工作空间' : '欢迎回来';
    $('auth-subtitle').textContent = register ? '用一个本地账号，保存你的分析与证据。' : '登录，开始一次有据可查的代码分析。';
    $('auth-submit').innerHTML = (register ? '创建账号并进入' : '登录工作台') + ' ' + icon('arrow');
    $('auth-toggle').textContent = register ? '直接登录' : '创建账号';
    $('auth-switch-copy').textContent = register ? '已有账号？' : '第一次使用？';
    $('password').autocomplete = register ? 'new-password' : 'current-password';
    $('password').minLength = register ? 8 : 1;
    $('password').placeholder = register ? '至少 8 个字符' : '请输入密码';
    $('auth-error').textContent = '';
  });
  $('auth-form').addEventListener('submit', async event => {
    event.preventDefault(); $('auth-submit').disabled = true; $('auth-error').textContent = '';
    try {
      const data = await api(register ? '/api/register' : '/api/login', {method:'POST',body:{username:$('username').value,password:$('password').value}});
      $('password').value = ''; await showApp(data);
    } catch (error) { $('auth-error').textContent = error.message; }
    finally { $('auth-submit').disabled = false; }
  });
  $('analysis-form').addEventListener('submit', async event => {
    event.preventDefault(); if (submitting) return;
    submitting = true; $('submit-analysis').disabled = true; $('form-error').textContent = '';
    try {
      const isBatch = window.BatchUI.getMode() === 'batch';
      const created = await api(isBatch ? '/api/batches' : '/api/jobs', {method:'POST',body:isBatch ? window.BatchUI.payload(options()) : payload()});
      ['a','b'].forEach(label => $(label+'-token').value = '');
      $('batch-token').value = '';
      await openJob(created.id); await refresh();
    } catch (error) { $('form-error').textContent = error.message; }
    finally { submitting = false; $('submit-analysis').disabled = false; }
  });
  $('demo-button').addEventListener('click', startDemo);
  $('batch-demo-button').addEventListener('click', startBatchDemo);
  $('logout').addEventListener('click', async () => {
    try { await api('/api/logout', {method:'POST',body:{}}); showAuth(); }
    catch (error) { toast(error.message); }
  });
  document.addEventListener('click', async event => {
    const target = event.target.closest('button, a');
    if (!target) return;
    if (target.dataset.view) switchView(target.dataset.view);
    if (target.dataset.job) await openJob(target.dataset.job);
    if (target.hasAttribute('data-refresh')) await refresh();
    if (target.hasAttribute('data-retry') && current) prefill(current);
    if (target.dataset.pair) {
      pairId = target.dataset.pair; renderEvidence();
      $('evidence-container').scrollIntoView({behavior:'smooth',block:'start'});
    }
    if (target.hasAttribute('data-close-evidence')) { pairId = null; $('evidence-container').innerHTML = ''; }
    if (target.hasAttribute('data-expand-code')) target.previousElementSibling.classList.toggle('expanded');
    if (target.hasAttribute('data-clear-filter')) {
      filter = {path:'',language:'',minimum:0};
      $('filter-path').value = ''; $('filter-language').value = ''; $('filter-minimum').value = '0'; renderPairs();
    }
  });
  setInterval(refresh, 2500);
  api('/api/session', {quiet:true}).then(showApp).catch(error => {
    showAuth();
    if (!error.message.includes('登录')) $('auth-error').textContent = error.message;
  });
})();
