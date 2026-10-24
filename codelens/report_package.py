"""Bounded, portable ZIP bundles with links to both immutable evidence scopes."""
import json
import time
import zipfile

from .batch_report import render_batch_report
from .engine import AnalysisError
from .report import render_report

MAX_PACKAGE_RAW_BYTES = 256 * 1024 * 1024
MAX_PACKAGE_ZIP_BYTES = 128 * 1024 * 1024
MAX_PACKAGE_SECONDS = 120


def write_package(job, load_pair, output):
    if job["kind"] != "batch" or job["status"] not in ("completed", "partial"):
        raise AnalysisError("只有已完成或部分完成的批次可以导出报告包。")
    started, size = time.monotonic(), 0
    links, records = {}, []
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        def write(name, content):
            nonlocal size
            data = content.encode("utf-8")
            size += len(data)
            if size > MAX_PACKAGE_RAW_BYTES:
                raise AnalysisError("报告包未压缩内容超过 256 MB，请缩小批次或排除无关代码。")
            if time.monotonic() - started > MAX_PACKAGE_SECONDS:
                raise AnalysisError("报告包生成超过 120 秒，请缩小批次后重试。")
            archive.writestr(name, data)
            if output.tell() > MAX_PACKAGE_ZIP_BYTES:
                raise AnalysisError("压缩报告包超过 128 MB，请缩小分析范围。")

        for pair in job["result"]["pairs"]:
            record = {"pair_id": pair["id"], "a_label": pair["a_label"], "b_label": pair["b_label"],
                      "status": pair["status"], "reason": pair.get("reason", ""), "reports": {}}
            if pair["status"] == "completed":
                links[pair["id"]] = {}
                for scope in ("adjusted", "raw"):
                    name = f'pairs/{pair["id"]}-{scope}.html'
                    alternate = "raw" if scope == "adjusted" else "adjusted"
                    try:
                        detail = load_pair(pair["id"], scope)
                        detail["package_nav"] = {
                            "overview": f'../index.html#{"raw" if scope == "raw" else "adjusted"}-matrix',
                            "alternate": f'{pair["id"]}-{alternate}.html'}
                        page = render_report(detail)
                    except AnalysisError as error:
                        raise AnalysisError(f'作品对 {pair["id"]} 的{scope}证据未能完整导出：{error}')
                    write(name, page)
                    links[pair["id"]][scope] = name
                    record["reports"][scope] = name
            records.append(record)
        write("index.html", render_batch_report(job, pair_links=links))
        manifest = {"format":"codelens-linked-report-1", "job_id":job["id"], "name":job["name"],
                    "created_at":job["created_at"], "algorithm":job["result"]["algorithm"],
                    "sources":job["sources"], "options":job["options"], "summary":job["result"]["summary"],
                    "baseline":job["result"]["baseline"],
                    "students":job["result"]["students"], "pairs":records,
                    "evidence_pages":sum(len(r["reports"]) for r in records)}
        write("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        write("使用说明.txt", "CodeLens 离线证据报告包\n\n"
              "1. 解压整个 ZIP，保留 index.html、pairs 目录和 manifest.json 的相对位置。\n"
              "2. 用浏览器打开 index.html；矩阵数值和作品对名单链接到对应原始/扣模板后证据。\n"
              "3. 证据页可返回矩阵、切换口径、自动换行，并同步双栏水平与垂直滚动比例。\n"
              "4. N/A 或比较失败的作品对没有可判定证据，不按 0% 处理。\n"
              "5. 无匹配的已完成作品对仍有报告页，明确说明未发现达到阈值的片段。\n"
              "6. 所有页面离线可用。报告含学生标签和源码，请按作品权限分享。\n"
              "相似度不能直接认定抄袭；教师模板只扣确认匹配的 token 区间。\n")
    if output.tell() > MAX_PACKAGE_ZIP_BYTES:
        raise AnalysisError("压缩报告包超过 128 MB，请缩小分析范围。")
    output.seek(0)
    return manifest
