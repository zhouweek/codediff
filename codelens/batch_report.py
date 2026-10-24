"""Offline batch overviews; full source evidence is exported for individual pairs."""
from .report import esc, number, table


def color_legend():
    return ('<div class="color-legend" aria-label="矩阵颜色图例">'
            '<strong>整体相似覆盖率</strong><span class="legend-scale" aria-hidden="true"></span>'
            '<span>0% → 100% · 颜色越深，整体覆盖越高</span>'
            '<span class="legend-na">N/A</span><span>不可判定 / 失败</span>'
            '<span>— 同一作品</span></div>')


def render_batch_report(job, pair_links=None):
    is_package = pair_links is not None
    pair_links = pair_links or {}
    result = job["result"]
    students, pairs, s = result["students"], result["pairs"], result["summary"]
    states = {"ready": "可分析", "empty": "无独立代码", "failed": "处理失败",
              "completed": "已完成", "unavailable": "不可判定"}
    lookup = {(p["a"], p["b"]): p for p in pairs}
    def matrix(scope):
        body = []
        for i, student in enumerate(students):
            cells = [f'<th scope="row" title="{esc(student["branch"])}"><span>{i+1:02d}</span> · {esc(student["label"])}</th>']
            for j in range(len(students)):
                pair = lookup.get((min(i, j), max(i, j)))
                if i == j:
                    cells.append('<td class="diagonal">—</td>')
                elif not pair or pair["status"] != "completed":
                    cells.append('<td class="na">N/A</td>')
                else:
                    value = pair[scope]["coverage"]
                    metrics = pair[scope]
                    title = (f'A {pair["a_label"]} ↔ B {pair["b_label"]}: 整体 {number(value)}%; '
                             f'A {number(metrics["a_coverage"])}% / B {number(metrics["b_coverage"])}%')
                    href = pair_links.get(pair["id"], {}).get(scope)
                    content = f'<a href="{esc(href)}" aria-label="{esc(title)}">{number(value)}</a>' if href else number(value)
                    cells.append(f'<td title="{esc(title)}" style="background:color-mix(in srgb,var(--accent) {value * .4:.1f}%,var(--surface))">{content}</td>')
            body.append("<tr>" + "".join(cells) + "</tr>")
        return '<div class="table-wrap matrix"><table><thead><tr><th>作品编号 / 标签</th>' + "".join(
            f'<th title="{esc(st["label"])}">{i+1:02d}</th>' for i, st in enumerate(students)) + '</tr></thead><tbody>' + "".join(body) + '</tbody></table></div>'
    sorted_pairs = sorted(pairs, key=lambda p: (
        -max(p["adjusted"]["a_coverage"], p["adjusted"]["b_coverage"]) if p["adjusted"] else 1,
        -p["adjusted"]["coverage"] if p["adjusted"] else 1, p["a"], p["b"]))
    rows = []
    for p in sorted_pairs:
        raw, adjusted = p["raw"], p["adjusted"]
        review = bool(adjusted and adjusted["fragments"] and
                      max(adjusted["a_coverage"], adjusted["b_coverage"]) >= result["review_threshold"])
        rows.append([p["a_label"], p["b_label"], "优先复核" if review else states[p["status"]],
                     number(raw["coverage"]) + "%" if raw else "N/A",
                     number(adjusted["coverage"]) + "%" if adjusted else "N/A",
                     number(adjusted["a_coverage"]) + "%" if adjusted else "N/A",
                     number(adjusted["b_coverage"]) + "%" if adjusted else "N/A",
                     adjusted["fragments"] if adjusted else "N/A", p.get("reason", "")])
    baseline = result["baseline"]
    baseline_text = (f'{baseline["branch"]} · {baseline["commit"]}' if baseline else "未使用教师模板")
    student_table = table(["编号", "作品标签", "学生分支", "固定 SHA", "状态", "原始 token", "模板 token", "独立 token", "跳过文件", "原因"], [
        [i+1, st["label"], st["branch"], st.get("commit", ""), states[st["status"]], st.get("tokens", "N/A"),
         st.get("template_tokens", "N/A"), st.get("independent_tokens", "N/A"), st.get("skipped", "N/A"),
         st.get("error", "")] for i, st in enumerate(students)])
    headers = ["作品 A", "作品 B", "复核/状态", "原始整体", "扣模板整体", "A→B", "B→A", "片段数", "原因", "代码证据"]
    pair_rows = []
    for row, pair in zip(rows, sorted_pairs):
        links = pair_links.get(pair["id"], {})
        evidence = " · ".join(f'<a href="{esc(links[scope])}">{"扣模板后" if scope == "adjusted" else "原始"}证据</a>'
                              for scope in ("adjusted", "raw") if scope in links)
        if not evidence:
            evidence = "不可判定" if pair["status"] != "completed" else "请从平台下载报告包"
        pair_rows.append(f'<tr id="pair-{esc(pair["id"])}">' +
                         "".join(f'<td>{esc(c)}</td>' for c in row) + f'<td>{evidence}</td></tr>')
    pairs_table = ('<div class="table-wrap"><table><thead><tr>' +
                   "".join(f'<th>{esc(h)}</th>' for h in headers) +
                   '</tr></thead><tbody>' + "".join(pair_rows) + '</tbody></table></div>')
    skipped_table = table(["作品", "路径", "跳过原因"], [
        [st["label"], item["path"], item["reason"]]
        for st in students for item in st.get("skipped_files", [])])
    options = result["options"]
    note = "部分完成：尚有作品或作品对无法判断。" if job["status"] == "partial" else "所有选中作品均已处理。"
    evidence_note = ("本报告包包含全部已完成作品对的原始及扣模板后证据。点击矩阵数值或名单中的证据链接，"
                     "无需联网；证据页可返回本矩阵。请先解压整个报告包，并保持目录结构。") if is_package else (
                         "此为独立总览，不含全部代码证据。请在平台点击“导出带证据报告包”，获得可离线点击的矩阵和证据页面。")
    body = f"""<header><p class="eyebrow">CODELENS / 毕业实习作品批量查重</p><h1>{esc(job["name"])}</h1><p>{'离线示例 · 模拟分支与作品标签' if job.get("demo") else '固定 Git 提交分析'} · {esc(job["created_at"])}</p></header>
<p class="notice">{esc(note)} {esc(evidence_note)} 相似覆盖不是抄袭裁定。</p>
<div class="metrics"><section><b>{s["students"]}</b><span>学生作品</span></section><section><b>{s["completed_pairs"]}/{s["pairs"]}</b><span>已完成作品对</span></section><section><b>{s["review_pairs"]}</b><span>优先复核作品对</span></section></div>
<p>仓库：{esc(job["sources"]["repository"]["url"])}<br>教师模板：{esc(baseline_text)}<br>复核阈值：任一侧覆盖达到 {number(result["review_threshold"])}% 且存在匹配片段。</p>
<p>可分析 {s["ready"]} 个 · 无独立代码 {s["empty"]} 个 · 作品失败 {s["failed_students"]} 个 · 不可判定作品对 {s["unavailable_pairs"]} 个 · 比较失败 {s["failed_pairs"]} 个。</p>
<h2>作品与固定提交</h2>{student_table}<h2 id="adjusted-matrix">扣模板后的整体相似矩阵（%）</h2><p>行轴显示作品编号与标签；列编号对应同一作品顺序。对角线为 —，N/A 不等于 0%。{"点击数值可查看对应口径的代码证据。" if pair_links else ""}</p>{color_legend()}{matrix("adjusted")}
<h2 id="raw-matrix">原始整体相似矩阵（%）</h2>{color_legend()}{matrix("raw")}<h2 id="pairs">全部作品对与复核排序</h2>{pairs_table}
<h2>跳过文件与原因</h2>{skipped_table}
<h2>检测设置与解释</h2><p>算法 {esc(result["algorithm"])} · 标识符归一化 {'开启' if options["normalize_identifiers"] else '关闭'} · 字面量归一化 {'开启' if options["normalize_literals"] else '关闭'} · 最小连续片段 {options["min_match"]} token。用户排除：{esc("；".join(options["excludes"]) or "无")}。</p>
<p>先确认学生与模板之间达到阈值的同语言连续片段，从学生代码中扣除区间并集；只保留两侧均非模板且达到最小长度的证据。相同路径不代表整个文件都被排除。每侧覆盖 = 去重匹配 token / 该侧对应口径的有效 token。整体 = 两侧匹配 token 之和 / 两侧对应分母之和。</p>
<p><strong>模板扣除不保证覆盖率下降：</strong>分子和分母都可能减少。作品表的“原始 / 模板 / 独立 token”展示实际扣除量；证据页还会列出扣后匹配量和计算式。例如 111 个有效 token 扣去 68 个模板 token，剩余 43 个全部匹配时，扣后覆盖率仍为 100%。图例显示整体覆盖；优先复核按任一侧覆盖达到阈值且有证据判定，两者不能混用。</p>
<p>学生可能共享课堂示例、框架和允许复用的代码。词法检测不能可靠判断任意语句重排、算法等价或跨语言语义。没有匹配不证明没有抄袭。常见写法仍可能匹配，请人工复核。</p>
{"".join(f'<p class="notice">{esc(w)}</p>' for w in result["warnings"])}
<footer>CodeLens v2 · 可离线阅读 · 报告可能包含学生作品标签，请按学校要求分享。</footer>"""
    return """<!-- Generated by Trae Work -->
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>""" + esc(job["name"]) + """</title><style>
:root{--bg:#f6f8fc;--surface:#fff;--ink:#18263b;--muted:#536579;--rule:#dce4ed;--accent:#365ad8;--wash:#edf2ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.9 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;padding:40px 24px}main{max-width:1240px;margin:auto}h1{font-size:32px}h2{font-size:22px;margin-top:40px}.eyebrow{color:var(--accent);font-size:12px;letter-spacing:.1em}.notice{background:var(--wash);border-left:3px solid var(--accent);padding:15px 20px;color:var(--muted)}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}.metrics section{background:var(--surface);border:1px solid var(--rule);padding:20px;border-radius:10px}.metrics b{font-size:34px;display:block;color:var(--accent)}.metrics span{color:var(--muted);font-size:13px}.table-wrap{overflow:auto;max-height:600px;border:1px solid var(--rule);border-radius:8px;margin:20px 0}table{width:100%;border-collapse:separate;border-spacing:0;background:var(--surface);font-size:12px}th,td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--rule);overflow-wrap:anywhere}thead th{position:sticky;top:0;background:var(--wash);z-index:2}.matrix th,.matrix td{white-space:nowrap;text-align:center;min-width:52px;padding:8px;font-size:11px}.matrix tbody th{position:sticky;left:0;background:var(--wash);z-index:1}.matrix thead th:first-child{left:0;z-index:3}.na,.diagonal{color:var(--muted);background:var(--bg)}footer{margin-top:40px;padding-top:20px;border-top:1px solid var(--rule);color:var(--muted);font-size:12px}@media(max-width:760px){body{padding:20px 14px}.metrics{grid-template-columns:1fr}h1{font-size:26px}}@media print{body{padding:0}.table-wrap{overflow:visible;max-height:none}.matrix{overflow:auto}thead th{position:static}}
</style><style>
h1{overflow-wrap:anywhere}
a{color:var(--accent);text-underline-offset:3px}.matrix tbody th{width:220px;min-width:220px;max-width:220px;text-align:left;white-space:normal;overflow-wrap:anywhere}.matrix thead th:first-child{min-width:220px;text-align:left}.matrix td a{display:block;min-width:36px;color:var(--ink);text-decoration:none}.matrix td a:hover,.matrix td a:focus-visible{outline:2px solid var(--accent);outline-offset:3px}.color-legend{display:flex;align-items:center;gap:12px;flex-wrap:wrap;font-size:13px;color:var(--muted);margin:14px 0}.legend-scale{width:110px;height:16px;border:1px solid var(--rule);border-radius:4px;background:linear-gradient(90deg,var(--surface),color-mix(in srgb,var(--accent) 40%,var(--surface)))}.legend-na{padding:1px 8px;border:1px solid var(--rule);border-radius:4px;background:var(--bg)}html{scroll-padding-top:20px}
</style></head><body><main>""" + body + "</main></body></html>"
