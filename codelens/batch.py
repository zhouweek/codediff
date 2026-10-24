"""Batch preparation, template subtraction, summary caching and immutable evidence."""
import gzip
import hashlib
import json
from dataclasses import asdict
from itertools import combinations

from .engine import (AnalysisError, CodeFile, compare_prepared, covered, file_index,
                     merge_intervals, read_files, validate_options)

MAX_BRANCHES = 100
MAX_BATCH_TOKENS = 4000000
MAX_BRANCH_TOKENS = 200000
MAX_CACHE_BYTES = 128 * 1024 * 1024
BATCH_ALGORITHM = "batch-template-winnowing-2.0"


def validate_batch(body, validate_source):
    if not isinstance(body, dict):
        raise AnalysisError("批次请求格式不正确。")
    source = validate_source(body.get("repository", {}), check_dns=False)
    items = body.get("branches")
    if not isinstance(items, list) or not 2 <= len(items) <= MAX_BRANCHES:
        raise AnalysisError("请选择 2–100 个学生分支。")
    result, seen = [], set()
    for item in items:
        item = {"branch": item} if isinstance(item, str) else item
        if not isinstance(item, dict) or not isinstance(item.get("branch"), str) or not item["branch"].strip():
            raise AnalysisError("学生分支名不能为空。")
        branch = validate_source({**source, "branch": item["branch"]}, check_dns=False)["branch"]
        label = item.get("label", branch)
        if not isinstance(label, str) or len(label) > 80:
            raise AnalysisError("作品标签最多 80 字。")
        if branch in seen:
            raise AnalysisError("学生分支不能重复：" + branch)
        seen.add(branch)
        result.append({"branch": branch, "label": label.strip() or branch})
    baseline = body.get("baseline", "")
    if not isinstance(baseline, str):
        raise AnalysisError("教师模板分支格式不正确。")
    if baseline.strip():
        baseline = validate_source({**source, "branch": baseline}, check_dns=False)["branch"]
        if baseline in seen:
            raise AnalysisError("教师模板不能同时作为学生作品。")
    threshold = body.get("review_threshold", 70)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 100:
        raise AnalysisError("复核阈值必须为 0–100。")
    return source, result, baseline.strip(), float(threshold), validate_options(body.get("options", {}))


def encode_prepared(prepared):
    obj = {"files": [asdict(f) for f in prepared["files"]], "skipped": prepared["skipped"],
           "masks": prepared["masks"], "meta": prepared["meta"]}
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    return gzip.compress(raw), len(raw)


def decode_prepared(blob):
    with gzip.GzipFile(fileobj=__import__("io").BytesIO(blob)) as stream:
        raw = stream.read(MAX_CACHE_BYTES + 1)
    if len(raw) > MAX_CACHE_BYTES:
        raise AnalysisError("快照缓存超过处理范围。")
    obj = json.loads(raw)
    files = []
    for item in obj["files"]:
        item["fingerprints"] = {int(k): v for k, v in item["fingerprints"].items()}
        files.append(CodeFile(**item))
    return {"files": files, "skipped": obj["skipped"], "masks": {int(k): v for k, v in obj["masks"].items()},
            "meta": obj["meta"], "index": file_index(files)}


def compare(a, b, options, adjusted=True, evidence=False):
    return compare_prepared(a["files"], b["files"], options, a["skipped"], b["skipped"],
                            index_a=a["index"], index_b=b["index"],
                            masks_a=a["masks"] if adjusted else {},
                            masks_b=b["masks"] if adjusted else {}, evidence=evidence)


def prepare(entries, meta, options, cache, baseline=None):
    files, skipped = read_files(entries, options, cache=cache)
    total = sum(len(f.values) for f in files)
    if total > MAX_BRANCH_TOKENS:
        raise AnalysisError("作品超过 200,000 有效 token，请增加排除路径。")
    prepared = {"files": files, "skipped": skipped, "masks": {}, "meta": meta, "index": file_index(files)}
    if baseline and files:
        match = compare_prepared(files, baseline["files"], options, evidence=False, trace=True,
                                 index_a=prepared["index"], index_b=baseline["index"])
        prepared["masks"] = {i: merge_intervals(v) for i, v in match["_intervals"]["a"].items()}
    removed = sum(covered(v) for v in prepared["masks"].values())
    prepared["counts"] = {"files": len(files), "tokens": total, "template_tokens": removed,
                          "independent_tokens": total - removed, "skipped": len(skipped)}
    prepared["usable"] = any(
        end - start >= options["min_match"]
        for i, f in enumerate(files)
        for start, end in independent_intervals(len(f.values), prepared["masks"].get(i, [])))
    signature = [(f.path, f.language, f.values, prepared["masks"].get(i, [])) for i, f in enumerate(files)]
    prepared["signature"] = hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()
    return prepared


def independent_intervals(length, masks):
    cursor = 0
    for start, end in merge_intervals(masks) + [(length, length)]:
        if cursor < start:
            yield cursor, start
        cursor = end


def analyze_batch(snapshots, items, options, baseline_branch="", review_threshold=70,
                  progress=lambda _: None, save=lambda branch, blob: None):
    """Snapshots yield (branch, entries or None, metadata, error), template first."""
    options = validate_options(options)
    cache, prepared, branch_rows, errors, baseline = {}, {}, {}, {}, None
    sizes = 0
    for branch, entries, meta, error in snapshots:
        is_baseline = branch == baseline_branch
        if error:
            if is_baseline:
                raise AnalysisError("教师模板不可获取：" + error)
            errors[branch] = error
            branch_rows[branch] = {**meta, "status": "failed", "error": error}
        else:
            try:
                obj = prepare(entries, meta, options, cache, baseline if not is_baseline else None)
                blob, size = encode_prepared(obj)
                if is_baseline and not obj["files"]:
                    raise AnalysisError("教师模板没有达到最小长度的有效代码。")
                sizes += size
                if sizes > MAX_CACHE_BYTES or sum(len(f.values) for f in cache.values()) > MAX_BATCH_TOKENS:
                    raise AnalysisError("批次缓存或有效 token 总量超限，请缩小范围。")
                save(branch, blob)
                if is_baseline:
                    baseline = obj
                else:
                    prepared[branch] = obj
                    branch_rows[branch] = {**meta, **obj["counts"], "skipped_files": obj["skipped"],
                                           "status": "ready" if obj["usable"] else "empty", "error": ""}
            except AnalysisError as error:
                if is_baseline or "批次缓存" in str(error):
                    raise
                errors[branch] = str(error)
                branch_rows[branch] = {**meta, "status": "failed", "error": str(error)}
        if not is_baseline:
            progress(f"已处理 {len(branch_rows)}/{len(items)} 个学生作品")
    if baseline_branch and baseline is None:
        raise AnalysisError("教师模板未成功处理。")
    ready = [item for item in items if branch_rows.get(item["branch"], {}).get("status") == "ready"]
    if len(ready) < 2:
        raise AnalysisError("不足两个具有独立有效代码的学生作品；请检查模板与排除设置。")
    students = [{**item, **branch_rows.get(item["branch"], {"status": "failed", "error": "快照缺失"}),
                 "id": str(i)} for i, item in enumerate(items)]
    pairs, comparison_cache = [], {}
    total = len(items) * (len(items) - 1) // 2
    computed = 0
    for i, j in combinations(range(len(students)), 2):
        a_row, b_row = students[i], students[j]
        row = {"id": f"{i}-{j}", "a": i, "b": j, "a_label": a_row["label"], "b_label": b_row["label"]}
        if a_row["status"] != "ready" or b_row["status"] != "ready":
            row.update(status="unavailable", reason="至少一侧作品无独立代码或处理失败。", raw=None, adjusted=None)
        else:
            a, b = prepared[a_row["branch"]], prepared[b_row["branch"]]
            key = (a["signature"], b["signature"])
            if key not in comparison_cache:
                computed += 1
                try:
                    raw = compare(a, b, options, adjusted=False)
                    # Raw must retain template content. Adjusted uses the same cached tokens.
                    if baseline_branch:
                        adjusted = compare(a, b, options, adjusted=True)
                    else:
                        adjusted = raw
                    comparison_cache[key] = {"raw": raw["summary"], "adjusted": adjusted["summary"],
                                             "warnings": sorted(set(raw["warnings"] + adjusted["warnings"]))}
                except AnalysisError as error:
                    comparison_cache[key] = {"error": str(error)}
            cached = comparison_cache[key]
            if "error" in cached:
                row.update(status="failed", reason=cached["error"], raw=None, adjusted=None)
            else:
                row.update(status="completed", reason="", **cached)
                row["same_commit"] = a_row.get("commit") == b_row.get("commit")
        pairs.append(row)
        if len(pairs) % 25 == 0 or len(pairs) == total:
            progress(f"已比较 {len(pairs)}/{total} 个作品对")
    completed = [p for p in pairs if p["status"] == "completed"]
    review = [p for p in completed if p["adjusted"]["fragments"] and
              max(p["adjusted"]["a_coverage"], p["adjusted"]["b_coverage"]) >= review_threshold]
    warnings = ["同仓库学生分支可能共享课堂示例或历史，需结合模板与授权范围人工复核。"]
    if not baseline_branch:
        warnings.append("未扣除教师模板，统一脚手架可能抬高相似度。")
    failed_pairs = sum(p["status"] == "failed" for p in pairs)
    return {"kind": "batch", "algorithm": BATCH_ALGORITHM, "options": options,
            "baseline": baseline["meta"] if baseline else None, "review_threshold": review_threshold,
            "students": students, "pairs": pairs, "warnings": warnings,
            "summary": {"students": len(students), "ready": len(ready),
                        "empty": sum(s["status"] == "empty" for s in students),
                        "failed_students": len(errors), "pairs": total, "completed_pairs": len(completed),
                        "unavailable_pairs": sum(p["status"] == "unavailable" for p in pairs),
                        "failed_pairs": failed_pairs, "review_pairs": len(review)},
            "partial": bool(errors or failed_pairs),
            "work": {"unique_files": len(cache), "computed_pairs": computed, "cache_bytes": sizes}}


def demo_snapshots(count=50):
    """Shared scaffold + independent, renamed implementation groups; never fake percentages."""
    from .demo import SAMPLES
    scaffold = SAMPLES["src/cart.js"]
    baseline = ("teacher-template", [("src/work.js", scaffold.encode(), "")],
                {"url": "离线示例 / graduation", "branch": "teacher-template", "commit": "demo-template-v2"}, "")
    yield baseline
    for i in range(count):
        group = i // 5
        additions = [
            f'\nexport async function work{i}(rows) {{ const result = await Promise.all(rows.map(async value => Math.sqrt(value) + {group + 11})); return JSON.stringify(result); }}\n',
            f'\nexport class Work{i} {{ constructor(value) {{ this.value = value; }} encode() {{ try {{ return JSON.stringify(this.value).toUpperCase(); }} catch(error) {{ throw new Error("group-{group}"); }} }} }}\n',
            f'\nexport function work{i}(values) {{ const table = new Map(); for (const entry of values) {{ table.set(entry.key, entry.value * {group + 7}); }} return Array.from(table.entries()).sort(); }}\n',
        ]
        source = scaffold if i == count - 1 else scaffold + additions[group % len(additions)]
        branch = f"student-{i+1:03d}"
        yield branch, [("src/work.js", source.encode(), "")], {
            "url": "离线示例 / graduation", "branch": branch, "commit": f"demo-student-{i+1:03d}-v2"}, ""
