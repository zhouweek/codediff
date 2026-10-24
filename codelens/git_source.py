"""Read immutable snapshots from public HTTPS Git endpoints; never execute sources."""
import ipaddress
import os
import re
import signal
import socket
import subprocess
import tarfile
import threading
import time
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from .engine import AnalysisError, excluded, MAX_FILE_BYTES, LANGUAGES

MAX_REPOSITORY_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
TIMEOUT = 120
GIT = [
    "git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
    "-c", "credential.helper=", "-c", "http.followRedirects=false",
    "-c", "http.sslVerify=true", "-c", "core.hooksPath=/dev/null",
    "-c", "fetch.recurseSubmodules=false", "-c", "gc.auto=0",
    "-c", "maintenance.auto=false",
]


def validate_source(source, check_dns=True):
    if not isinstance(source, dict):
        raise AnalysisError("代码来源格式不正确。")
    url, branch = source.get("url", ""), source.get("branch", "")
    if not isinstance(url, str) or not isinstance(branch, str):
        raise AnalysisError("仓库地址和分支必须为文本。")
    url, branch = url.strip(), branch.strip()
    if len(url) > 1000 or not url:
        raise AnalysisError("请填写有效仓库地址（最多 1,000 字）。")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise AnalysisError("仓库地址格式不正确。")
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or port not in (None, 443)
            or not parsed.path.strip("/") or "\\" in url or any(ord(c) < 33 for c in url)):
        raise AnalysisError("仅支持不含凭据的公开 HTTPS Git 地址，令牌请填在独立字段。")
    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise AnalysisError("首版不支持本机或内网 Git 地址。")
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and not literal_ip.is_global:
        raise AnalysisError("首版不支持本机、内网或保留网段地址。")
    if check_dns:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
        except socket.gaierror:
            raise AnalysisError("无法解析仓库域名，请检查地址或网络。")
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise AnalysisError("首版不支持本机、内网或保留网段地址。")
    if len(branch) > 200 or branch.startswith("-") or (
        branch and subprocess.run(["git", "check-ref-format", "refs/heads/" + branch],
                                  capture_output=True).returncode != 0):
        raise AnalysisError("分支名称不合法，请填写真实分支名（最多 200 字）。")
    credentials = {}
    for key in ("username", "token"):
        value = source.get(key, "")
        if not isinstance(value, str) or len(value) > 4096 or any(c in value for c in "\0\r\n"):
            raise AnalysisError("访问凭据格式不正确。")
        credentials[key] = value
    # Canonical casing prevents treating same URL with host case differences as distinct.
    canonical = urlunsplit(("https", parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))
    return {"url": canonical, "branch": branch, **credentials}


def public_source(source):
    return {key: source[key] for key in ("url", "branch")}


def environment(workdir, source):
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"}}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"})
    askpass = Path(workdir) / "askpass.sh"
    askpass.write_text(
        '#!/bin/sh\ncase "$1" in\n*Username*) printf "%s\\n" "$CODELENS_GIT_USERNAME";;\n'
        '*) printf "%s\\n" "$CODELENS_GIT_TOKEN";;\nesac\n'
    )
    askpass.chmod(0o700)
    env.update({"GIT_ASKPASS": str(askpass),
                "CODELENS_GIT_USERNAME": source.get("username") or "x-access-token",
                "CODELENS_GIT_TOKEN": source.get("token", "")})
    return env


def directory_size(directory):
    return sum(p.stat().st_size for p in Path(directory).rglob("*") if p.is_file())


def stop_process(proc):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait(timeout=5)


def run_git(args, directory, env, output_limit=1024 * 1024, repository_limit=MAX_REPOSITORY_BYTES):
    """Bound network commands by wall time, output size and downloaded object size."""
    output = Path(directory) / "command.out"
    errors = Path(directory) / "command.err"
    with output.open("wb") as out, errors.open("wb") as err:
        proc = subprocess.Popen(GIT + args, cwd=directory, env=env, stdout=out, stderr=err,
                                start_new_session=True)
        deadline = time.monotonic() + TIMEOUT
        try:
            while proc.poll() is None:
                if time.monotonic() > deadline:
                    raise AnalysisError("获取代码超时（120 秒），请稍后重试或缩小仓库。")
                if output.stat().st_size + errors.stat().st_size > output_limit:
                    raise AnalysisError("Git 返回内容超过安全处理范围，请缩小来源。")
                if directory_size(directory) > repository_limit:
                    raise AnalysisError("仓库 Git 对象超过处理范围，请缩小仓库。")
                time.sleep(0.1)
            if proc.returncode:
                # Never expose remote-controlled stderr or credentials.
                raise AnalysisError("无法获取 Git 版本：请检查地址、分支、访问权限和网络。")
            if output.stat().st_size > output_limit or directory_size(directory) > repository_limit:
                raise AnalysisError("Git 内容超过处理范围，请缩小仓库。")
            return output.read_text("utf-8", errors="replace")
        finally:
            stop_process(proc)


class LimitedStream:
    def __init__(self, stream, limit):
        self.stream, self.limit, self.count = stream, limit, 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.count += len(data)
        if self.count > self.limit:
            raise AnalysisError("快照展开后超过 100 MB，请缩小仓库。")
        return data


def snapshot(source, directory, patterns):
    """Return entries and resolved branch/SHA, with archive paths never extracted."""
    directory = Path(directory)
    directory.mkdir()
    env = environment(directory, source)
    run_git(["init", "--bare", "."], directory, env)
    branch = source["branch"]
    if not branch:
        refs = run_git(["ls-remote", "--symref", "--", source["url"], "HEAD"], directory, env)
        match = re.search(r"^ref: refs/heads/(.+)\tHEAD$", refs, re.MULTILINE)
        if not match:
            raise AnalysisError("无法解析默认分支，仓库可能为空；请填写具体分支。")
        branch = match.group(1)
        validate_source({**source, "branch": branch}, check_dns=False)
    run_git(["fetch", "--depth=1", "--no-tags", "--no-recurse-submodules", "--",
             source["url"], "refs/heads/" + branch], directory, env)
    sha = run_git(["rev-parse", "--verify", "FETCH_HEAD^{commit}"], directory, env).strip()
    if not re.fullmatch(r"[a-f0-9]{40,64}", sha):
        raise AnalysisError("未取得有效提交标识。")
    entries = archive_entries(directory, sha, env, patterns)
    return entries, {"url": source["url"], "branch": source["branch"],
                     "resolved_branch": branch, "commit": sha, "label": branch}


def archive_entries(directory, sha, env, patterns, repository_limit=MAX_REPOSITORY_BYTES):
    # Submodule trees are not traversed and are explicitly reported as skipped.
    tree = run_git(["ls-tree", "-r", "-z", sha], directory, env, 8 * 1024 * 1024, repository_limit)
    entries = []
    for row in tree.split("\0"):
        if row.startswith("160000 ") and "\t" in row:
            entries.append((row.split("\t", 1)[1], b"", "Git 子模块未获取"))
    err_path = directory / "archive.err"
    with err_path.open("wb") as err:
        proc = subprocess.Popen(GIT + ["archive", "--format=tar", sha], cwd=directory,
                                env=env, stdout=subprocess.PIPE, stderr=err, start_new_session=True)
        timeout = threading.Timer(TIMEOUT, stop_process, args=(proc,))
        timeout.daemon = True
        timeout.start()
        try:
            stream = LimitedStream(proc.stdout, MAX_ARCHIVE_BYTES)
            with tarfile.open(fileobj=stream, mode="r|") as archive:
                count = 0
                for member in archive:
                    if member.isdir() or member.name == ".gitmodules":
                        continue
                    count += 1
                    if count > 50000:
                        raise AnalysisError("快照文件超过 50,000 个，请缩小仓库。")
                    path = member.name
                    if path.startswith("/") or ".." in PurePosixPath(path).parts:
                        raise AnalysisError("快照包含不合法文件路径。")
                    reason = excluded(path, patterns)
                    if not member.isfile():
                        reason = "符号链接/非普通文件未读取"
                    elif member.size > MAX_FILE_BYTES:
                        reason = "文件超过 512 KB"
                    elif PurePosixPath(path).suffix.lower() not in LANGUAGES:
                        reason = reason or "不支持的文件类型"
                    data = b"" if reason else archive.extractfile(member).read(MAX_FILE_BYTES + 1)
                    entries.append((path, data, reason))
            if proc.wait(timeout=10) != 0:
                raise AnalysisError("快照读取失败或超时，请重新分析。")
        except (tarfile.TarError, OSError, subprocess.TimeoutExpired):
            raise AnalysisError("无法完整读取 Git 快照，请缩小范围后重试。")
        finally:
            timeout.cancel()
            stop_process(proc)
            if proc.stdout:
                proc.stdout.close()
    return entries


def remote_branches(source, directory, env):
    text = run_git(["ls-remote", "--heads", "--", source["url"]], directory, env, 4 * 1024 * 1024,
                   1024 * 1024 * 1024)
    refs = {}
    for row in text.splitlines():
        match = re.fullmatch(r"([a-f0-9]{40,64})\trefs/heads/(.+)", row)
        if match:
            refs[match[2]] = match[1]
    if len(refs) > 5000:
        raise AnalysisError("远端超过 5,000 个分支，请缩小仓库范围。")
    return refs


def list_branches(source):
    from tempfile import TemporaryDirectory
    source = validate_source(source)
    with TemporaryDirectory(prefix="codelens-refs-") as temp:
        env = environment(temp, source)
        refs = remote_branches(source, temp, env)
        env.clear()
    return [{"branch": branch, "commit": sha} for branch, sha in sorted(refs.items())]


def batch_snapshots(source, branches, directory, patterns):
    """One bare object store, bounded ref batches; each snapshot is independently reported."""
    source = validate_source(source)
    directory = Path(directory)
    directory.mkdir()
    env = environment(directory, source)
    limit = 1024 * 1024 * 1024
    try:
        run_git(["init", "--bare", "."], directory, env)
        refs = remote_branches(source, directory, env)
        available = [(i, branch) for i, branch in enumerate(branches) if branch in refs]
        errors = {branch: "分支不存在，请检查名称后重新分析。" for branch in branches if branch not in refs}
        fetched = {}
        for begin in range(0, len(available), 8):
            chunk = available[begin:begin + 8]
            specs = [f"refs/heads/{branch}:refs/codelens/{i}" for i, branch in chunk]
            args = ["fetch", "--depth=1", "--no-tags", "--no-recurse-submodules", "--", source["url"]]
            try:
                run_git(args + specs, directory, env, repository_limit=limit)
            except AnalysisError:
                if directory_size(directory) > limit:
                    raise AnalysisError("批次 Git 对象累计超过 1 GB，请缩小范围。")
                for (i, branch), spec in zip(chunk, specs):
                    try:
                        run_git(args + [spec], directory, env, repository_limit=limit)
                    except AnalysisError as error:
                        if directory_size(directory) > limit:
                            raise AnalysisError("批次 Git 对象累计超过 1 GB，请缩小范围。")
                        errors[branch] = str(error)
            for i, branch in chunk:
                if branch in errors:
                    continue
                try:
                    sha = run_git(["rev-parse", "--verify", f"refs/codelens/{i}^{{commit}}"],
                                  directory, env, repository_limit=limit).strip()
                    if sha != refs[branch]:
                        raise AnalysisError("分支在获取期间发生变动，请重新分析以固定版本。")
                    fetched[branch] = sha
                except AnalysisError as error:
                    errors[branch] = str(error)
        # Keep the template first; group subsequent equal SHAs to read each tree once
        # without retaining every expanded snapshot in memory.
        last_sha, last_entries = None, None
        ordered = branches[:1] + sorted(branches[1:], key=lambda branch: fetched.get(branch, ""))
        for branch in ordered:
            meta = {"url": source["url"], "branch": branch, "resolved_branch": branch,
                    "commit": fetched.get(branch, refs.get(branch, ""))}
            if branch in errors:
                yield branch, None, meta, errors[branch]
                continue
            try:
                sha = fetched[branch]
                entries = last_entries if sha == last_sha else archive_entries(directory, sha, env, patterns, limit)
                last_sha, last_entries = sha, entries
                yield branch, entries, meta, ""
            except AnalysisError as error:
                yield branch, None, meta, str(error)
    finally:
        env.clear()
        source["token"] = source["username"] = ""
