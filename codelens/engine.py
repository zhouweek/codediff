"""Language-aware token clone detection, with verifiable source evidence."""
import fnmatch
import hashlib
import re
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Callable, Dict, List

from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.token import Comment, Literal, Name, Text

ALGORITHM_VERSION = "tokens-winnowing-1.0"
LANGUAGES = {
    ".java": ("Java", "java"), ".js": ("JavaScript", "javascript"),
    ".jsx": ("JavaScript", "jsx"), ".mjs": ("JavaScript", "javascript"),
    ".cjs": ("JavaScript", "javascript"), ".ts": ("TypeScript", "typescript"),
    ".tsx": ("TypeScript", "tsx"), ".html": ("HTML", "html"),
    ".htm": ("HTML", "html"), ".c": ("C", "c"), ".h": ("C", "c"),
    ".cc": ("C++", "cpp"), ".cpp": ("C++", "cpp"), ".cxx": ("C++", "cpp"),
    ".hpp": ("C++", "cpp"), ".hh": ("C++", "cpp"), ".cs": ("C#", "csharp"),
    ".php": ("PHP", "php"), ".py": ("Python", "python"),
    ".go": ("Go", "go"), ".rs": ("Rust", "rust"),
    ".kt": ("Kotlin", "kotlin"), ".kts": ("Kotlin", "kotlin"),
    ".swift": ("Swift", "swift"), ".rb": ("Ruby", "ruby"),
    ".css": ("CSS", "css"), ".sql": ("SQL", "sql"),
}
DEFAULT_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", "target", "coverage",
    ".venv", "venv", "__pycache__", ".next", ".nuxt", ".idea",
}
DEFAULT_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock"}
MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 1000
MAX_TOKENS = 400000
MAX_CANDIDATES = 20000
MAX_EVIDENCE = 5000


class AnalysisError(ValueError):
    pass


@dataclass
class CodeFile:
    path: str
    language: str
    source: str
    values: List[str]
    raw: List[str]
    offsets: List[tuple]
    lines: List[tuple]
    fingerprints: Dict[int, List[int]]


def validate_options(options):
    if not isinstance(options, dict):
        raise AnalysisError("检测设置格式不正确。")
    allowed = {"normalize_identifiers", "normalize_literals", "min_match", "excludes"}
    if set(options) - allowed:
        raise AnalysisError("检测设置包含未知字段。")
    minimum = options.get("min_match", 24)
    if isinstance(minimum, bool) or not isinstance(minimum, int) or not 12 <= minimum <= 100:
        raise AnalysisError("最小片段长度必须是 12–100 的整数。")
    excludes = options.get("excludes", [])
    if not isinstance(excludes, list) or len(excludes) > 50 or any(
        not isinstance(x, str) or len(x) > 200 for x in excludes
    ):
        raise AnalysisError("排除规则最多 50 行，每行最多 200 字。")
    result = {"min_match": minimum, "excludes": [x.strip() for x in excludes if x.strip()]}
    for key, default in [("normalize_identifiers", True), ("normalize_literals", False)]:
        value = options.get(key, default)
        if not isinstance(value, bool):
            raise AnalysisError("归一化选项必须为布尔值。")
        result[key] = value
    return result


def excluded(path, patterns):
    p = PurePosixPath(path)
    if any(part in DEFAULT_DIRS for part in p.parts) or p.name in DEFAULT_FILES:
        return "默认排除目录/文件"
    if p.name.endswith((".min.js", ".min.css", ".map")):
        return "压缩或生成文件"
    for pattern in patterns:
        # Directory patterns also match any descendants, at any depth.
        pattern = pattern[2:] if pattern.startswith("./") else pattern
        if not pattern:
            continue
        if fnmatch.fnmatchcase(path, pattern) or p.match(pattern):
            return "用户排除规则"
        directory = pattern.rstrip("/").removesuffix("/**")
        if directory and (path.startswith(directory + "/") or directory in p.parts[:-1]):
            return "用户排除规则"
    return ""


def fingerprints(values, minimum):
    """Rolling polynomial hash + rightmost minima; equal >=minimum runs share anchors."""
    k, window = minimum - 4, 5
    if len(values) < minimum:
        return {}
    mask, base = (1 << 64) - 1, 1000003
    cache = {}
    nums = []
    for value in values:
        if value not in cache:
            cache[value] = int.from_bytes(hashlib.blake2b(value.encode(), digest_size=8).digest(), "big")
        nums.append(cache[value])
    power = pow(base, k - 1, 1 << 64)
    h = 0
    for n in nums[:k]:
        h = (h * base + n) & mask
    hashes = [h]
    for i in range(k, len(nums)):
        h = ((h - nums[i - k] * power) * base + nums[i]) & mask
        hashes.append(h)
    minima, selected = deque(), defaultdict(list)
    last_index = -1
    for i, h in enumerate(hashes):
        while minima and minima[0][1] <= i - window:
            minima.popleft()
        while minima and minima[-1][0] >= h:
            minima.pop()
        minima.append((h, i))
        if i >= window - 1 and minima[0][1] != last_index:
            last_index = minima[0][1]
            selected[minima[0][0]].append(last_index)
    return dict(selected)


def tokenize(path, source, options):
    language, alias = LANGUAGES[PurePosixPath(path).suffix.lower()]
    lexer = get_lexer_by_name(alias, stripnl=False, ensurenl=False, startinline=True)
    values, raw, offsets, lines = [], [], [], []
    line, offset = 1, 0
    for token_type, value in lex(source, lexer):
        end_line = line + value.count("\n")
        # Include only meaningful tokens; unknown/error tokens are preserved.
        is_comment = token_type in Comment and token_type not in Comment.Preproc and token_type not in Comment.PreprocFile
        if value.strip() and not is_comment:
            norm = value
            if options["normalize_identifiers"] and token_type in Name and token_type not in (Name.Tag, Name.Attribute, Name.Builtin):
                norm = "<identifier>"
            elif options["normalize_literals"] and token_type in Literal:
                norm = "<number>" if token_type in Literal.Number else "<literal>"
            elif language == "HTML" and token_type in Text:
                norm = re.sub(r"\s+", " ", value).strip()
                if options["normalize_literals"]:
                    norm = "<text>"
            # Keep token type category so e.g. operator + differs from string "+".
            category = "id" if norm == "<identifier>" else str(token_type).split(".")[1]
            values.append(category + ":" + norm)
            raw.append(str(token_type) + ":" + value)
            offsets.append((offset, offset + len(value)))
            lines.append((line, end_line))
        offset += len(value)
        line = end_line
    return CodeFile(path, language, source, values, raw, offsets, lines,
                    fingerprints(values, options["min_match"]))


def read_files(entries, options, cancel=lambda: None, cache=None):
    files, skipped = [], []
    for path, data, reason in entries:
        cancel()
        reason = reason or excluded(path, options["excludes"])
        if not reason and PurePosixPath(path).suffix.lower() not in LANGUAGES:
            reason = "不支持的文件类型"
        if not reason and len(data) > MAX_FILE_BYTES:
            reason = "文件超过 512 KB"
        if not reason and (b"\0" in data or data.startswith(b"version https://git-lfs.github.com/spec/v1")):
            reason = "二进制或 Git LFS 指针"
        if reason:
            skipped.append({"path": path, "reason": reason})
            continue
        try:
            source = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            skipped.append({"path": path, "reason": "非 UTF-8 编码"})
            continue
        key = (LANGUAGES[PurePosixPath(path).suffix.lower()], hashlib.sha256(source.encode()).digest(),
               options["min_match"], options["normalize_identifiers"], options["normalize_literals"])
        if cache is not None and key in cache:
            f = replace(cache[key], path=path)
        else:
            f = tokenize(path, source, options)
            if cache is not None:
                cache[key] = f
        if len(f.values) < options["min_match"]:
            skipped.append({"path": path, "reason": "有效 token 少于最小片段长度"})
            continue
        files.append(f)
        if len(files) > MAX_FILES:
            raise AnalysisError("单侧可分析文件超过 1,000 个，请增加排除路径。")
    return files, skipped


def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def covered(intervals):
    return sum(end - start for start, end in merge_intervals(intervals))


def pct(n, denominator):
    return round(100 * n / denominator, 4) if denominator else 0.0


def blocks(a, b, anchors, minimum, mask_a=(), mask_b=()):
    # Anchors along the same diagonal extend to the same maximal block.
    spans, found = defaultdict(list), []
    k = minimum - 4
    for x, y in sorted(anchors):
        diagonal = x - y
        if any(start <= x and x + k <= end for start, end in spans[diagonal]):
            continue
        if a.values[x:x+k] != b.values[y:y+k]:
            continue
        left = 0
        while x - left > 0 and y - left > 0 and a.values[x-left-1] == b.values[y-left-1]:
            left += 1
        right = k
        while x + right < len(a.values) and y + right < len(b.values) and a.values[x+right] == b.values[y+right]:
            right += 1
        ax, by, length = x - left, y - left, left + right
        spans[diagonal].append((ax, ax + length))
        if length >= minimum:
            found.append((ax, by, length))
    if mask_a or mask_b:
        retained = []
        for ax, by, length in found:
            excluded_parts = []
            for origin, intervals in [(ax, mask_a), (by, mask_b)]:
                for start, end in intervals:
                    lo, hi = max(0, start - origin), min(length, end - origin)
                    if lo < hi:
                        excluded_parts.append((lo, hi))
            cursor = 0
            for start, end in merge_intervals(excluded_parts) + [(length, length)]:
                if start - cursor >= minimum:
                    retained.append((ax + cursor, by + cursor, start - cursor))
                cursor = end
        found = retained
    # Avoid many redundant occurrence pairings of repetitive code.
    selected, used_a, used_b = [], [], []
    for ax, by, length in sorted(set(found), key=lambda x: (-x[2], x[0], x[1])):
        if covered(used_a + [(ax, ax + length)]) == covered(used_a) and covered(used_b + [(by, by + length)]) == covered(used_b):
            continue
        selected.append((ax, by, length))
        used_a.append((ax, ax + length))
        used_b.append((by, by + length))
    return sorted(selected)


def snippet(f, start, length):
    first, last = f.lines[start][0], f.lines[start + length - 1][1]
    all_lines = f.source.splitlines()
    context_start, context_end = max(1, first - 2), min(len(all_lines), last + 2)
    return {
        "start_line": first, "end_line": last, "context_start": context_start,
        "code": "\n".join(all_lines[context_start - 1:context_end]),
        "fragment": f.source[f.offsets[start][0]:f.offsets[start + length - 1][1]],
    }


def analyze(a_entries, b_entries, options=None, progress: Callable = lambda _: None):
    options = validate_options(options or {})
    progress("正在识别语言和筛选文件")
    a_files, a_skipped = read_files(a_entries, options)
    b_files, b_skipped = read_files(b_entries, options)
    return compare_prepared(a_files, b_files, options, a_skipped, b_skipped, progress)


def file_index(files):
    index = defaultdict(set)
    for i, f in enumerate(files):
        for h in f.fingerprints:
            index[(f.language, h)].add(i)
    return index


def compare_prepared(a_files, b_files, options, a_skipped=(), b_skipped=(),
                     progress=lambda _: None, index_a=None, index_b=None,
                     masks_a=None, masks_b=None, evidence=True, trace=False):
    masks_a, masks_b = masks_a or {}, masks_b or {}
    total_a = sum(len(f.values) - covered(masks_a.get(i, [])) for i, f in enumerate(a_files))
    total_b = sum(len(f.values) - covered(masks_b.get(i, [])) for i, f in enumerate(b_files))
    if sum(len(f.values) for f in list(a_files) + list(b_files)) > MAX_TOKENS:
        raise AnalysisError("两侧有效 token 合计超过 400,000，请缩小范围。")
    index_a = file_index(a_files) if index_a is None else index_a
    index_b = file_index(b_files) if index_b is None else index_b
    frequent = set()
    for files in (a_files, b_files):
        for i, f in enumerate(files):
            for h, positions in f.fingerprints.items():
                key = (f.language, h)
                if len(positions) > 64:
                    frequent.add(key)
    common = set(index_a) & set(index_b)
    frequent |= {key for key in common if len(index_a[key]) + len(index_b[key]) > 60}
    candidates = defaultdict(list)
    for key in sorted(common - frequent):
        for ai in sorted(index_a[key]):
            for bi in sorted(index_b[key]):
                candidates[(ai, bi)].append(key[1])
                if len(candidates) > MAX_CANDIDATES:
                    raise AnalysisError("候选文件对超过 20,000，请增加排除项或提高最小片段长度。")
    progress(f"正在核对 {len(candidates)} 个候选文件对")
    all_a, all_b, pairs = defaultdict(list), defaultdict(list), []
    evidence_count = 0
    for (ai, bi), hashes in sorted(candidates.items()):
        a, b = a_files[ai], b_files[bi]
        anchors = set()
        for h in hashes:
            for x in a.fingerprints[h]:
                for y in b.fingerprints[h]:
                    anchors.add((x, y))
                    if len(anchors) > 30000:
                        raise AnalysisError("单文件对重复锚点超过 30,000，请提高最小长度或排除模板。")
        matched = blocks(a, b, anchors, options["min_match"], masks_a.get(ai, ()), masks_b.get(bi, ()))
        if not matched:
            continue
        evidence_count += len(matched)
        if evidence_count > MAX_EVIDENCE:
            raise AnalysisError("确认匹配片段超过 5,000，请缩小范围；未生成截断报告。")
        intervals_a = [(x, x + n) for x, _, n in matched]
        intervals_b = [(y, y + n) for _, y, n in matched]
        ca, cb = covered(intervals_a), covered(intervals_b)
        all_a[ai].extend(intervals_a)
        all_b[bi].extend(intervals_b)
        reordered = any(matched[i][1] > matched[i + 1][1] for i in range(len(matched) - 1))
        fragments = []
        for x, y, n in matched if evidence else []:
            sa, sb = snippet(a, x, n), snippet(b, y, n)
            if sa["fragment"] == sb["fragment"]:
                kind = "原文片段相同"
            elif a.raw[x:x+n] == b.raw[y:y+n]:
                kind = "格式/注释变化"
            else:
                kind = "标识符/字面量变化"
            sa.pop("fragment")
            sb.pop("fragment")
            fragments.append({"a": sa, "b": sb, "tokens": n, "kind": kind})
        denominator_a = len(a.values) - covered(masks_a.get(ai, []))
        denominator_b = len(b.values) - covered(masks_b.get(bi, []))
        pairs.append({
            "id": f"pair-{ai}-{bi}", "a_path": a.path, "b_path": b.path,
            "language": a.language, "a_tokens": denominator_a, "b_tokens": denominator_b,
            "a_matched": ca, "b_matched": cb, "a_coverage": pct(ca, denominator_a),
            "b_coverage": pct(cb, denominator_b), "coverage": pct(ca + cb, denominator_a + denominator_b),
            "reordered": reordered, "relocated": a.path != b.path, "fragment_count": len(matched),
            "evidence": fragments,
        })
    ca, cb = sum(covered(v) for v in all_a.values()), sum(covered(v) for v in all_b.values())
    language_stats = {}
    for label, files, matches in [("a", a_files, all_a), ("b", b_files, all_b)]:
        for i, f in enumerate(files):
            stat = language_stats.setdefault(f.language, {"language": f.language, "a_files": 0, "b_files": 0, "a_tokens": 0, "b_tokens": 0, "a_matched": 0, "b_matched": 0})
            stat[label + "_files"] += 1
            masks = masks_a if label == "a" else masks_b
            stat[label + "_tokens"] += len(f.values) - covered(masks.get(i, []))
            stat[label + "_matched"] += covered(matches[i])
    warnings = []
    if frequent & common:
        warnings.append(f"忽略了 {len(frequent & common)} 个高频指纹，候选筛选可能漏检重复模板或高度重复代码。")
    if not a_files or not b_files:
        warnings.append("至少一侧没有达到最小长度的有效代码，请查看跳过项。")
    result = {
        "algorithm": ALGORITHM_VERSION, "options": options,
        "summary": {"a_files": len(a_files), "b_files": len(b_files), "a_tokens": total_a,
                    "b_tokens": total_b, "a_matched": ca, "b_matched": cb,
                    "a_coverage": pct(ca, total_a), "b_coverage": pct(cb, total_b),
                    "coverage": pct(ca + cb, total_a + total_b),
                    "pairs": len(pairs), "fragments": evidence_count,
                    "skipped": len(a_skipped) + len(b_skipped)},
        "pairs": sorted(pairs, key=lambda p: (-p["coverage"], p["a_path"], p["b_path"])),
        "languages": sorted(language_stats.values(), key=lambda s: s["language"]),
        "skipped": {"a": a_skipped, "b": b_skipped}, "warnings": warnings,
    }
    if trace:
        result["_intervals"] = {"a": dict(all_a), "b": dict(all_b)}
    return result
