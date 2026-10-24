(() => {
  const $ = id => document.getElementById(id);
  const stages = ['排队中 · 等待资源', '获取代码中 · 已处理 50/50 个作品', '分析中 · 已比较 800/1,225 个作品对', '已完成 · 1,225 个作品对均有明确状态'];
  let stage = 0, adjusted = false;
  document.addEventListener('click', event => {
    const action = event.target.dataset.demo;
    if (action === 'login') $('login-feedback').textContent = /^[a-zA-Z0-9_.-]{3,40}$/.test($('demo-user').value) && $('demo-pass').value.length >= 8 ? '登录示意成功 · 进入本人工作台' : '请检查用户名并输入至少 8 位密码。';
    if (action === 'submit') {
      const valid = ['demo-a', 'demo-b'].every(id => {
        try { const u = new URL($(id).value); return u.protocol === 'https:' && !u.username && !u.password && !u.search && !u.hash && u.hostname !== 'localhost'; } catch { return false; }
      });
      $('submit-feedback').textContent = valid ? '参数已接受 · 任务进入排队中（示意）' : '请填写合法的公开 HTTPS Git 地址，访问凭据应填在独立字段。';
    }
    if (action === 'stage') { stage = Math.min(stage + 1, 3); $('stage-feedback').textContent = stages[stage]; }
    if (action === 'fail') $('stage-feedback').textContent = '部分完成 · 一个学生分支不存在；其他作品结果保留，涉及失败作品的单元格显示 N/A。';
    if (action === 'retry') { stage = 0; $('stage-feedback').textContent = stages[0]; }
    if (action === 'evidence') $('demo-code').hidden = !$('demo-code').hidden;
    if (action === 'scope') { adjusted = !adjusted; $('scope-feedback').textContent = adjusted ? '当前：扣模板后 20%（示意）' : '当前：原始 80%（示意）'; }
    if (action === 'print') window.print();
    if (action === 'history') $('history-feedback').textContent = '已打开历史示意任务 · 实际报告将固定来源提交并保留检测设置。';
  });
  $('demo-count').addEventListener('input', e => {
    const n = Number(e.target.value);
    $('count-feedback').textContent = `${n} 个作品 → ${(n * (n-1) / 2).toLocaleString()} 个作品对，每个无序对只比较一次。`;
  });
  $('demo-coverage').addEventListener('input', e => {
    const n = Number(e.target.value);
    $('coverage-feedback').textContent = `A→B ${n}% · B→A ${n / 2}% · 整体 ${(2 * n / 3).toFixed(1)}%（B 总量 200）`;
  });
  $('demo-filter').addEventListener('input', e => {
    const visible = 'utils.js helpers.js'.includes(e.target.value.toLowerCase());
    $('demo-pair').hidden = !visible;
    $('demo-code').hidden = true;
    $('filter-feedback').textContent = visible ? '' : '没有符合筛选条件的文件对，请清空筛选。';
  });
})();
