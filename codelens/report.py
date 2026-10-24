"""Standalone, escaped HTML reports with no external runtime dependencies."""
import html
import base64
import hashlib
from datetime import datetime
from pathlib import Path

EVIDENCE_SCRIPT = (Path(__file__).resolve().parents[1] / "web" / "evidence.js").read_text()
EVIDENCE_HASH = base64.b64encode(hashlib.sha256(EVIDENCE_SCRIPT.encode()).digest()).decode()


def evidence_controls():
    return ('<div class="evidence-tools" role="group" aria-label="代码阅读设置">'
            '<label><input type="checkbox" data-evidence-wrap checked>自动换行</label>'
            '<label><input type="checkbox" data-evidence-sync checked>双栏同步滚动</label>'
            '<span>同步同一片段的水平与垂直滚动比例；浅绿色为确认匹配。</span></div>')


def template_accounting(context, a_label="A", b_label="B"):
    raw, adjusted = context["raw"], context["adjusted"]
    rows = []
    for side, label in (("a", a_label), ("b", b_label)):
        before, after = raw[side + "_tokens"], adjusted[side + "_tokens"]
        matched = adjusted[side + "_matched"]
        rows.append([label, before, before - after, after, matched,
                     f'{matched} / {after} = {number(adjusted[side + "_coverage"])}%'])
    return ('<section class="template-accounting"><h2>教师模板扣除明细</h2>' +
            table(["作品", "原始有效 token", "扣除模板 token", "扣后有效 token", "扣后匹配 token", "扣后覆盖率"], rows) +
            '<p class="muted">扣模板同时改变匹配量和分母，覆盖率不一定降低。例如 111 token 扣去 68 个模板 token，'
            '剩余 43 个若全部匹配，仍显示 100%。只扣确认的模板区间，原始行号不变。</p></section>')


def esc(value):
    return html.escape(str(value), quote=True)


def number(value):
    return f"{float(value):.1f}"


def code_block(snippet):
    rows = []
    for index, text in enumerate(snippet["code"].splitlines(), snippet["context_start"]):
        active = snippet["start_line"] <= index <= snippet["end_line"]
        rows.append(f'<div class="line{" matched" if active else ""}"><span class="num">{index}</span><code>{esc(text) or " "}</code></div>')
    return '<div class="code">' + "".join(rows) + "</div>"


def table(headers, rows):
    return '<div class="table-wrap"><table><thead><tr>' + "".join(
        f"<th>{esc(h)}</th>" for h in headers
    ) + "</tr></thead><tbody>" + "".join(
        "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in rows
    ) + "</tbody></table></div>"


def render_report(job):
    if job.get("kind") == "batch":
        from .batch_report import render_batch_report
        return render_batch_report(job)
    result, summary = job["result"], job["result"]["summary"]
    origin = []
    for label in ("a", "b"):
        src = job["sources"][label]
        origin.append(f'<section class="origin"><span class="eyebrow">来源 {label.upper()}</span><h3>{esc(src["url"])}</h3><p>请求分支：{esc(src["branch"] or "默认分支")} · 解析分支：{esc(src.get("resolved_branch", "演示"))}</p><code class="sha">{esc(src.get("commit", ""))}</code></section>')
    metrics = "".join(
        f'<div class="metric"><span>{esc(label)}</span><strong>{number(value)}%</strong><meter min="0" max="100" value="{number(value)}">{number(value)}%</meter></div>'
        for label, value in [("整体相似覆盖率", summary["coverage"]), ("A → B 覆盖率", summary["a_coverage"]), ("B → A 覆盖率", summary["b_coverage"])]
    )
    pairs = []
    for index, pair in enumerate(result["pairs"], 1):
        fragments = []
        for i, e in enumerate(pair["evidence"], 1):
            fragments.append(f'<details class="fragment" open><summary>片段 {i} · {esc(e["kind"])} · {e["tokens"]} token · A L{e["a"]["start_line"]}–{e["a"]["end_line"]} ↔ B L{e["b"]["start_line"]}–{e["b"]["end_line"]}</summary><div class="columns"><section><p class="code-label">A · {esc(pair["a_path"])}</p>{code_block(e["a"])}</section><section><p class="code-label">B · {esc(pair["b_path"])}</p>{code_block(e["b"])}</section></div></details>')
        tags = (" · 片段整体换序" if pair["reordered"] else "") + (" · 跨文件路径" if pair["relocated"] else "")
        pairs.append(f'<section class="pair"><h3>{index:02d} · {esc(pair["language"])} · {number(pair["coverage"])}% 相似覆盖{tags}</h3><p>{esc(pair["a_path"])} ↔ {esc(pair["b_path"])}</p><p class="muted">A→B {number(pair["a_coverage"])}% · B→A {number(pair["b_coverage"])}% · {len(pair["evidence"])} 个片段</p>{"".join(fragments)}</section>')
    if not pairs:
        pairs = ['<p class="notice">未发现达到当前最小片段阈值的匹配。请结合参与分析语言和跳过项解读，此结果不能证明没有抄袭。</p>']
    language_table = table(
        ["语言", "A 文件", "B 文件", "A 有效 token", "B 有效 token", "A 匹配 token", "B 匹配 token"],
        [[s[key] for key in ("language", "a_files", "b_files", "a_tokens", "b_tokens", "a_matched", "b_matched")] for s in result["languages"]],
    )
    skip_rows = [[label.upper(), item["path"], item["reason"]]
                 for label in ("a", "b") for item in result["skipped"][label]]
    skip_table = table(["来源", "路径", "跳过原因"], skip_rows) if skip_rows else "<p>无跳过文件。</p>"
    options = result["options"]
    warnings = "".join(f"<p class=\"notice\">{esc(w)}</p>" for w in result["warnings"])
    context = result.get("batch_context")
    accounting = ""
    if context:
        template = context["baseline"]
        template_text = f'{template["branch"]} @ {template["commit"]}' if template else "无"
        warnings += (f'<p class="notice">批次：{esc(context["name"])}；教师模板：{esc(template_text)}；'
                     f'当前口径：{"原始" if context["scope"] == "raw" else "扣模板后"}。'
                     f'原始整体 {number(context["raw"]["coverage"])}%，扣模板后整体 '
                     f'{number(context["adjusted"]["coverage"])}%。模板只扣除确认的 token 区间；'
                     '归一化设置会影响模板扣除，原文行号保持不变。</p>')
        accounting = template_accounting(context, job["sources"]["a"].get("label", "A"),
                                         job["sources"]["b"].get("label", "B"))
    demo = '<span class="badge">离线示例 · 真实引擎计算</span>' if job.get("demo") else ""
    html_page = """<!-- Generated by Trae Work -->
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; script-src 'sha256-__SCRIPTHASH__'"><title>__TITLE__ · CodeLens 报告</title><style>
:root{--bg:#f6f8fc;--surface:#ffffff;--ink:#18263b;--muted:#536579;--rule:#dce4ed;--accent:#365ad8;--wash:#edf2ff;--highlight:#e7f5ef}
*{box-sizing:border-box}body{margin:0;padding:45px 24px;background:var(--bg);color:var(--ink);font:15px/1.8 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}main{max-width:1200px;margin:auto}.eyebrow{font-size:12px;font-weight:700;letter-spacing:.08em;color:var(--accent)}header{margin-bottom:32px}h1{font-size:36px;line-height:1.35;margin:14px 0}h2{font-size:23px;margin:40px 0 18px}h3{font-size:18px;margin:10px 0;overflow-wrap:anywhere}p{margin:8px 0}.muted,.code-label{color:var(--muted)}.badge{background:var(--wash);color:var(--accent);font-size:12px;padding:5px 10px;border-radius:5px}.columns{display:grid;grid-template-columns:1fr 1fr;gap:18px}.columns>section{min-width:0}.origin,.metric,.pair{background:var(--surface);border:1px solid var(--rule);border-radius:12px;padding:24px}.sha{font-size:12px;overflow-wrap:anywhere;color:var(--muted)}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin:22px 0}.metric span{display:block;color:var(--muted)}.metric strong{display:block;font-size:40px;color:var(--accent);line-height:1.5}meter{width:100%;height:12px;accent-color:var(--accent)}.notice{background:var(--wash);border-left:3px solid var(--accent);padding:15px 18px;color:var(--muted)}.table-wrap{overflow-x:auto;overflow-y:auto;max-height:600px;border:1px solid var(--rule);border-radius:10px}table{width:100%;border-collapse:collapse;background:var(--surface);font-size:13px}th,td{text-align:left;padding:12px;border-bottom:1px solid var(--rule);overflow-wrap:anywhere}th{background:var(--wash)}.pair{margin:18px 0}.fragment{margin-top:18px;border-top:1px solid var(--rule);padding-top:14px}summary{cursor:pointer;font-size:13px;font-weight:600}.code{background:var(--bg);border:1px solid var(--rule);border-radius:7px;max-height:480px;overflow:auto;padding:8px 0}.line{display:flex;min-width:max-content;font:12px/1.9 ui-monospace,SFMono-Regular,Consolas,monospace}.line.matched{background:var(--highlight)}.num{width:48px;flex:none;text-align:right;padding:0 12px;color:var(--muted);user-select:none}code{white-space:pre}.code code{padding-right:15px}footer{border-top:1px solid var(--rule);margin-top:40px;padding-top:20px;color:var(--muted);font-size:12px}
@media(max-width:760px){body{padding:24px 14px}h1{font-size:28px}.columns,.metrics{grid-template-columns:1fr}.pair,.origin,.metric{padding:18px}table{min-width:650px}.metric strong{font-size:32px}}@media print{body{padding:0;background:var(--surface)}.pair{break-inside:auto}.table-wrap,.code{max-height:none;overflow:visible}.line{min-width:0}code{white-space:pre-wrap;overflow-wrap:anywhere}.columns{grid-template-columns:1fr}summary{list-style:none}details::details-content{content-visibility:visible!important}details>.columns{display:grid!important}}
</style></head><body><main><header><div class="eyebrow">CODELENS / 代码相似性报告</div><h1>__TITLE__</h1>__DEMO__<p class="muted">创建：__CREATED__ · 完成：__COMPLETED__ · 检测版本 __ALGORITHM__</p></header><div class="columns">__ORIGINS__</div><div class="metrics">__METRICS__</div><p class="notice">相似覆盖比例不等于抄袭概率。分支继承、开源复用和常见模板均可能造成匹配，请根据下面的原始片段与来源关系人工复核。报告包含匹配源码，请按仓库权限分享。</p>__WARNINGS__<p>参与分析：A __AF__ 个文件 / __AT__ token；B __BF__ 个文件 / __BT__ token。确认 __PAIRS__ 个文件对、__FRAGMENTS__ 个片段；跳过 __SKIPPED__ 个文件。</p><h2>检测规则与口径</h2><p>忽略空白和注释；标识符归一化：__IDENTIFIERS__；字面量归一化：__LITERALS__；最小连续片段：__MIN__ 个有效 token。</p><p>用户排除：__EXCLUDES__。默认排除依赖、构建目录、锁文件、压缩和映射文件。跳过文件不进入分母。</p><p class="muted">A→B = A 中去重匹配 token / A 有效 token；B→A 对称计算；整体 = 两侧去重匹配 token 之和 / 两侧有效 token 之和。文件对按对应两文件口径计算。同一位置多次匹配只计一次。</p><h2>语言分布</h2>__LANGUAGES__<h2>匹配证据</h2>__EVIDENCE__<h2>跳过文件与原因</h2>__SKIPTABLE__<h2>检测局限</h2><p>这是同语言的词法连续片段检测，可发现变量改名、注释/格式变化、函数或连续片段整体换序与跨文件迁移。不能可靠判断任意语句重排、数据流、算法等价或跨语言语义。标识符归一化会使常见写法更容易匹配；小于阈值或被高频筛选忽略的片段可能漏检。无匹配结果不等于没有抄袭。高/中/低仅为界面分级（70% / 30%），不构成抄袭裁定。</p><footer>CodeLens · 本报告可离线阅读 · 使用浏览器查找定位路径，点击片段标题折叠或展开 · 打印时请展开片段</footer></main></body></html>"""
    extra_css = """
.evidence-tools{display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:14px 18px;border:1px solid var(--rule);border-radius:8px;background:var(--surface);margin:12px 0}.evidence-tools label{display:flex;align-items:center;gap:7px;font-weight:600;font-size:14px}.evidence-tools input{accent-color:var(--accent);width:18px;height:18px}.evidence-tools span{color:var(--muted);font-size:12px}.wrap-lines .line{min-width:0;align-items:flex-start}.wrap-lines .code code{white-space:pre-wrap;overflow-wrap:anywhere;min-width:0;flex:1}.code .num{position:sticky;left:0;background:var(--bg)}.line.matched .num{background:var(--highlight)}.package-nav{display:flex;gap:18px;flex-wrap:wrap;margin:12px 0}.package-nav a{color:var(--accent);font-weight:600}@media print{.evidence-tools,.package-nav{display:none}}
"""
    html_page = html_page.replace("</style>", extra_css + "</style>")
    html_page = html_page.replace("</style>", "h1{overflow-wrap:anywhere}</style>")
    html_page = html_page.replace("__WARNINGS__", "__WARNINGS____ACCOUNTING__")
    html_page = html_page.replace("<h2>匹配证据</h2>__EVIDENCE__",
                                  '<h2>匹配证据</h2><section class="evidence-view wrap-lines">__CONTROLS____EVIDENCE__</section>')
    html_page = html_page.replace("__DEMO__", "__DEMO____NAV__")
    html_page = html_page.replace("</body>", '<script data-codelens-controls>__SCRIPT__</script></body>')
    nav = job.get("package_nav", {})
    navigation = (f'<nav class="package-nav" aria-label="报告包导航"><a href="{esc(nav["overview"])}">← 返回批次矩阵</a>'
                  f'<a href="{esc(nav["alternate"])}">查看{"原始" if context["scope"] == "adjusted" else "扣模板后"}证据</a></nav>') if nav else ""
    substitutions = {
        "TITLE": esc(job["name"]), "CREATED": esc(job["created_at"]),
        "COMPLETED": esc(job.get("completed_at", "")), "ALGORITHM": esc(result["algorithm"]),
        "DEMO": demo, "ORIGINS": "".join(origin), "METRICS": metrics, "WARNINGS": warnings,
        "AF": summary["a_files"], "BF": summary["b_files"], "AT": summary["a_tokens"],
        "BT": summary["b_tokens"], "PAIRS": summary["pairs"], "FRAGMENTS": summary["fragments"],
        "SKIPPED": summary["skipped"], "IDENTIFIERS": "开启" if options["normalize_identifiers"] else "关闭",
        "LITERALS": "开启" if options["normalize_literals"] else "关闭", "MIN": options["min_match"],
        "EXCLUDES": esc("；".join(options["excludes"]) or "无"), "LANGUAGES": language_table,
        "EVIDENCE": "".join(pairs), "SKIPTABLE": skip_table,
        "SCRIPTHASH": EVIDENCE_HASH, "SCRIPT": EVIDENCE_SCRIPT, "CONTROLS": evidence_controls(),
        "ACCOUNTING": accounting, "NAV": navigation,
    }
    # One regex substitution avoids interpreting placeholders embedded in untrusted code.
    import re
    return re.sub(r"__([A-Z]+)__", lambda m: str(substitutions[m.group(1)]), html_page)
