#!/usr/bin/env python3
"""CodeLens local web platform. Run: python3 server.py"""
import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote

from codelens import demo, batch
from codelens.engine import AnalysisError, analyze, validate_options, LANGUAGES
from codelens.git_source import validate_source, public_source, snapshot, list_branches, batch_snapshots
from codelens.report import render_report

ROOT = Path(__file__).resolve().parent
SESSION_DAYS = 7
MAX_PENDING = 8
MAX_RESULT_BYTES = 16 * 1024 * 1024
LOG = logging.getLogger("codelens")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 240000).hex()


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Store:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users(
                    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    salt TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions(
                    token_hash TEXT PRIMARY KEY, owner_id INTEGER NOT NULL,
                    csrf TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, name TEXT NOT NULL,
                    status TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL,
                    completed_at TEXT, sources TEXT NOT NULL, options TEXT NOT NULL,
                    result TEXT, demo INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner_id, created_at);
                CREATE TABLE IF NOT EXISTS batch_snapshots(
                    job_id TEXT NOT NULL, branch TEXT NOT NULL, content BLOB NOT NULL,
                    PRIMARY KEY(job_id, branch)
                );
            """)
            db.execute("UPDATE jobs SET status='failed', message='服务重启，原分析已中断。请重新分析。', completed_at=? WHERE status IN ('queued','fetching','analyzing')", (now(),))
            db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(str(self.path), timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def session(self, token):
        with self.db() as db:
            row = db.execute(
                "SELECT s.*, u.username FROM sessions s JOIN users u ON u.id=s.owner_id WHERE token_hash=? AND expires>?",
                (digest(token), time.time()),
            ).fetchone()
            return dict(row) if row else None

    def create_session(self, user_id):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(token), user_id, csrf, time.time() + SESSION_DAYS * 86400))
        return token, csrf

    def job(self, job_id, owner_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND owner_id=?", (job_id, owner_id)).fetchone()
        if not row:
            raise APIError(404, "任务不存在或无访问权限。")
        return self.decode_job(row, include_result=True)

    @staticmethod
    def decode_job(row, include_result=False):
        job = dict(row)
        job.pop("owner_id", None)
        job["sources"] = json.loads(job["sources"])
        job["kind"] = job["sources"].get("kind", "pair")
        job["options"] = json.loads(job["options"])
        job["demo"] = bool(job["demo"])
        raw_result = job.pop("result", None)
        if raw_result:
            result = json.loads(raw_result)
            job["summary"] = result["summary"]
            if include_result:
                job["result"] = result
        return job

    def update(self, job_id, **fields):
        allowed = {"status", "message", "completed_at", "sources", "result"}
        if set(fields) - allowed:
            raise ValueError("Invalid update")
        with self.db() as db:
            db.execute("UPDATE jobs SET " + ",".join(key + "=?" for key in fields) + " WHERE id=?",
                       list(fields.values()) + [job_id])


class Platform:
    def __init__(self, store):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="analysis")
        self.queue_lock = threading.Lock()
        self.rate_lock = threading.Lock()
        self.auth_attempts = {}
        self.batch_lock = threading.Lock()
        self.package_lock = threading.BoundedSemaphore(1)

    def rate_auth(self, ip):
        with self.rate_lock:
            cutoff = time.monotonic() - 60
            self.auth_attempts = {key: [t for t in values if t > cutoff]
                                  for key, values in self.auth_attempts.items() if any(t > cutoff for t in values)}
            attempts = self.auth_attempts.setdefault(ip, [])
            if len(attempts) >= 20:
                raise APIError(429, "登录/注册尝试过于频繁，请一分钟后重试。")
            attempts.append(time.monotonic())

    def submit(self, owner, body, is_demo=False):
        if not isinstance(body, dict):
            raise APIError(400, "请求格式不正确。")
        options = validate_options(body.get("options", {}))
        name = body.get("name", "")
        if not isinstance(name, str) or len(name) > 80:
            raise APIError(400, "任务名称最多 80 字。")
        if is_demo:
            sources = {label: {"url": "离线示例 / shopping-cart",
                               "branch": "original" if label == "a" else "renamed-and-reordered"}
                       for label in ("a", "b")}
            credentials = None
            name = name.strip() or "多语言 · 改名与换序演示"
        else:
            credentials = {label: validate_source(body.get(label, {}), check_dns=False) for label in ("a", "b")}
            sources = {label: public_source(credentials[label]) for label in ("a", "b")}
            name = name.strip() or " ↔ ".join(urlsplit(sources[label]["url"]).path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") for label in ("a", "b"))
            name = name[:80]
        job_id = secrets.token_hex(12)
        with self.queue_lock:
            with self.store.db() as db:
                count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','fetching','analyzing')").fetchone()[0]
                if count >= MAX_PENDING:
                    raise APIError(429, "当前已有 8 个待完成任务，请稍后提交。")
                db.execute(
                    "INSERT INTO jobs(id,owner_id,name,status,message,created_at,sources,options,demo) VALUES(?,?,?,?,?,?,?,?,?)",
                    (job_id, owner, name, "queued", "等待分析资源", now(),
                     json.dumps(sources, ensure_ascii=False), json.dumps(options, ensure_ascii=False), int(is_demo)),
                )
            self.executor.submit(self.run, job_id, sources, options, credentials, is_demo)
        return {"id": job_id}

    def submit_batch(self, owner, body, is_demo=False):
        source, branches, baseline, threshold, options = batch.validate_batch(body, validate_source)
        name = body.get("name", "")
        if not isinstance(name, str) or len(name) > 80:
            raise APIError(400, "任务名称最多 80 字。")
        sources = {"kind": "batch", "repository": public_source(source), "branches": branches,
                   "baseline": baseline, "review_threshold": threshold}
        if is_demo:
            sources["repository"]["url"] = "离线示例 / graduation"
        job_id = secrets.token_hex(12)
        with self.queue_lock:
            with self.store.db() as db:
                count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','fetching','analyzing')").fetchone()[0]
                if count >= MAX_PENDING:
                    raise APIError(429, "已有 8 个待完成任务，请稍后提交。")
                db.execute("INSERT INTO jobs(id,owner_id,name,status,message,created_at,sources,options,demo) VALUES(?,?,?,?,?,?,?,?,?)",
                           (job_id, owner, name.strip() or f"毕业实习作品批次 · {len(branches)} 个分支", "queued",
                            "等待批量分析资源", now(), json.dumps(sources, ensure_ascii=False),
                            json.dumps(options, ensure_ascii=False), int(is_demo)))
            self.executor.submit(self.run_batch, job_id, sources, source, options, is_demo)
        return {"id": job_id}

    def run_batch(self, job_id, sources, source, options, is_demo=False):
        try:
            with self.batch_lock:
                self.store.update(job_id, status="fetching", message="正在批量取得学生分支的固定版本")
                def progress(message):
                    self.store.update(job_id, status="analyzing" if message.startswith("已比较") else "fetching",
                                      message=message)
                def save(branch, content):
                    with self.store.db() as db:
                        db.execute("INSERT OR REPLACE INTO batch_snapshots VALUES(?,?,?)", (job_id, branch, content))
                with tempfile.TemporaryDirectory(prefix="codelens-batch-") as temp:
                    names = ([sources["baseline"]] if sources["baseline"] else []) + [s["branch"] for s in sources["branches"]]
                    snapshots = batch.demo_snapshots(len(sources["branches"])) if is_demo else batch_snapshots(
                        source, names, Path(temp) / "repository", options["excludes"])
                    result = batch.analyze_batch(snapshots, sources["branches"], options, sources["baseline"],
                                                 sources["review_threshold"], progress, save)
                text = json.dumps(result, ensure_ascii=False)
                if len(text.encode()) > MAX_RESULT_BYTES:
                    raise AnalysisError("批次摘要超过 16 MB，请缩小范围。")
                self.store.update(job_id, status="partial" if result["partial"] else "completed",
                                  message="部分完成，请检查不可判定的作品对。" if result["partial"] else "批次分析完成",
                                  completed_at=now(), result=text)
        except AnalysisError as error:
            self.store.update(job_id, status="failed", message=str(error), completed_at=now())
        except Exception:
            LOG.error("Batch %s failed with an internal error", job_id)
            self.store.update(job_id, status="failed", message="批量分析出现内部错误，请检查环境后重新分析。", completed_at=now())
        finally:
            source["token"] = source["username"] = ""

    def batch_pair(self, owner, job_id, pair_id, scope="adjusted"):
        job = self.store.job(job_id, owner)
        return self._batch_pair_detail(job, pair_id, scope)

    def _read_prepared(self, job_id, branch):
        with self.store.db() as db:
            row = db.execute("SELECT content FROM batch_snapshots WHERE job_id=? AND branch=?",
                             (job_id, branch)).fetchone()
        if not row:
            raise APIError(409, "固定快照缓存不可用，请重新分析批次。")
        return batch.decode_prepared(row["content"])

    def _batch_pair_detail(self, job, pair_id, scope, prepared_loader=None):
        job_id = job["id"]
        if job["kind"] != "batch" or job["status"] not in ("completed", "partial"):
            raise APIError(409, "批次尚未生成可用结果。")
        if scope not in ("raw", "adjusted"):
            raise APIError(400, "请选择原始或扣模板后的口径。")
        result = job["result"]
        row = next((p for p in result["pairs"] if p["id"] == pair_id), None)
        if not row:
            raise APIError(404, "作品对不存在。")
        if row["status"] != "completed":
            raise APIError(409, row["reason"])
        a_meta, b_meta = result["students"][row["a"]], result["students"][row["b"]]
        loader = prepared_loader or (lambda branch: self._read_prepared(job_id, branch))
        a, b = (loader(meta["branch"]) for meta in (a_meta, b_meta))
        evidence = batch.compare(a, b, job["options"], adjusted=scope == "adjusted", evidence=True)
        if len(json.dumps(evidence, ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
            raise APIError(413, "该作品对完整证据超过 16 MB，请缩小分析范围。")
        evidence["warnings"].insert(0, "扣模板后的证据。" if scope == "adjusted" and result["baseline"] else "原始证据，可能包含共同模板与课堂示例。")
        evidence["batch_context"] = {"name": job["name"], "baseline": result["baseline"], "scope": scope,
                                     "raw": row["raw"], "adjusted": row["adjusted"]}
        return {**job, "kind": "pair", "name": f'{job["name"]} · {a_meta["label"]} ↔ {b_meta["label"]}',
                "status": "completed", "sources": {"a": a_meta, "b": b_meta}, "result": evidence,
                "parent_batch_id": job_id, "batch_pair_id": pair_id, "scope": scope,
                "report_url": f"/api/jobs/{job_id}/pairs/{pair_id}/report?scope={scope}"}

    @contextmanager
    def report_package(self, owner, job_id):
        from codelens.report_package import write_package
        job = self.store.job(job_id, owner)
        if job["kind"] != "batch" or job["status"] not in ("completed", "partial"):
            raise APIError(409, "批次尚未生成可导出的结果。")
        if not self.package_lock.acquire(blocking=False):
            raise APIError(429, "正在生成另一个报告包，请稍后重试。")
        try:
            cache = OrderedDict()
            def prepared(branch):
                if branch not in cache:
                    cache[branch] = self._read_prepared(job_id, branch)
                cache.move_to_end(branch)
                while len(cache) > 2:
                    cache.popitem(last=False)
                return cache[branch]
            def detail(pair_id, scope):
                return self._batch_pair_detail(job, pair_id, scope, prepared)
            with tempfile.TemporaryFile(prefix="codelens-export-") as output:
                write_package(job, detail, output)
                yield output
        finally:
            self.package_lock.release()

    def run(self, job_id, sources, options, credentials, is_demo):
        try:
            if is_demo:
                a_entries, b_entries = demo.sources()
                resolved = {label: {**src, "resolved_branch": src["branch"], "commit": "demo-fixture-v1"}
                            for label, src in sources.items()}
            else:
                self.store.update(job_id, status="fetching", message="正在取得来源 A 的固定版本")
                with tempfile.TemporaryDirectory(prefix="codelens-git-") as temp:
                    a = validate_source(credentials["a"])
                    b = validate_source(credentials["b"])
                    a_entries, a_meta = snapshot(a, Path(temp) / "a", options["excludes"])
                    self.store.update(job_id, message="正在取得来源 B 的固定版本")
                    b_entries, b_meta = snapshot(b, Path(temp) / "b", options["excludes"])
                    resolved = {"a": a_meta, "b": b_meta}
                    a["token"] = b["token"] = ""
            self.store.update(job_id, status="analyzing", message="正在归一化代码并核对片段",
                              sources=json.dumps(resolved, ensure_ascii=False))
            result = analyze(a_entries, b_entries, options,
                             lambda message: self.store.update(job_id, message=message))
            if not is_demo and sources["a"]["url"] == sources["b"]["url"]:
                if resolved["a"]["commit"] == resolved["b"]["commit"]:
                    result["warnings"].insert(0, "两个来源解析为相同提交；高相似覆盖是同一版本的预期结果。")
                else:
                    result["warnings"].insert(0, "两个来源属于同一仓库，分支共享历史可能造成相似，请结合提交关系复核。")
            text = json.dumps(result, ensure_ascii=False)
            if len(text.encode()) > MAX_RESULT_BYTES:
                raise AnalysisError("完整证据超过 16 MB，请缩小范围或增加排除项；未生成截断报告。")
            self.store.update(job_id, status="completed", message="分析完成，可以查看证据与导出报告",
                              completed_at=now(), result=text)
        except AnalysisError as error:
            self.store.update(job_id, status="failed", message=str(error), completed_at=now())
        except Exception:
            # Tracebacks can contain source paths; keep response neutral and logs credential-free.
            LOG.error("Analysis %s failed with an internal error", job_id)
            self.store.update(job_id, status="failed", message="分析出现内部错误，请检查服务环境后重新运行。", completed_at=now())
        finally:
            if credentials:
                for source in credentials.values():
                    source["token"] = source["username"] = ""


class Handler(BaseHTTPRequestHandler):
    server_version = "CodeLens/2.0"

    @property
    def platform(self):
        return self.server.platform

    def log_message(self, fmt, *args):
        # Never log request body, password, query, source or token.
        LOG.info("%s %s", self.command, urlsplit(self.path).path)

    def headers_common(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Cache-Control", "no-store")

    def send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        if not isinstance(body, bytes):
            body = body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.headers_common()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_package(self, output, job_id):
        output.seek(0, 2)
        length = output.tell()
        output.seek(0)
        self.send_response(200)
        self.headers_common()
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Disposition", f'attachment; filename="codelens-{job_id}-reports.zip"')
        self.end_headers()
        while True:
            chunk = output.read(64 * 1024)
            if not chunk:
                break
            self.wfile.write(chunk)

    def check_host(self):
        if self.headers.get("Host") not in self.server.authorities:
            raise APIError(403, "请求来源不合法，请使用平台本机地址。")

    def require_session(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            raise APIError(401, "请重新登录。")
        token = cookie["codelens_session"].value if "codelens_session" in cookie else ""
        session = self.platform.store.session(token) if token else None
        if not session:
            raise APIError(401, "请先登录。")
        return session

    def check_write(self, session=None):
        origin = self.headers.get("Origin", "")
        if origin not in {"http://" + a for a in self.server.authorities}:
            raise APIError(403, "请求来源校验失败，请刷新页面后重试。")
        if session and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
            raise APIError(403, "会话校验失败，请刷新页面后重试。")

    def body(self):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise APIError(415, "请求必须使用 JSON。")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise APIError(400, "请求长度不正确。")
        if length <= 0 or length > 65536:
            raise APIError(413, "请求为空或超过 64 KB。")
        self.connection.settimeout(15)
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError, TimeoutError):
            raise APIError(400, "请求 JSON 不正确。")
        if not isinstance(body, dict):
            raise APIError(400, "请求必须为 JSON 对象。")
        return body

    def do_GET(self):
        try:
            self.check_host()
            path = urlsplit(self.path).path
            if path == "/api/session":
                session = self.require_session()
                self.send(200, {"username": session["username"], "csrf": session["csrf"],
                                "languages": sorted({v[0] for v in LANGUAGES.values()})})
                return
            if path == "/api/jobs":
                session = self.require_session()
                with self.platform.store.db() as db:
                    rows = db.execute("SELECT * FROM jobs WHERE owner_id=? ORDER BY created_at DESC, rowid DESC",
                                      (session["owner_id"],)).fetchall()
                self.send(200, {"jobs": [Store.decode_job(row) for row in rows]})
                return
            package_match = re.fullmatch(r"/api/jobs/([a-f0-9]{24})/report-package", path)
            if package_match:
                session = self.require_session()
                with self.platform.report_package(session["owner_id"], package_match[1]) as output:
                    self.send_package(output, package_match[1])
                return
            pair_match = re.fullmatch(r"/api/jobs/([a-f0-9]{24})/pairs/(\d+-\d+)(/report)?", path)
            if pair_match:
                from urllib.parse import parse_qs
                session = self.require_session()
                scope = parse_qs(urlsplit(self.path).query).get("scope", ["adjusted"])[0]
                detail = self.platform.batch_pair(session["owner_id"], pair_match[1], pair_match[2], scope)
                if pair_match[3]:
                    self.send(200, render_report(detail), "text/html; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="codelens-{pair_match[1]}-{pair_match[2]}.html"'})
                else:
                    self.send(200, detail)
                return
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{24})(/report)?", path)
            if match:
                session = self.require_session()
                job = self.platform.store.job(match[1], session["owner_id"])
                if match[2]:
                    if job["status"] not in ("completed", "partial"):
                        raise APIError(409, "分析尚未完成，暂时不能导出。")
                    self.send(200, render_report(job), "text/html; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="codelens-{job["id"]}.html"'})
                else:
                    self.send(200, job)
                return
            static = {"/": ("web/index.html", "text/html; charset=utf-8"),
                      "/app.js": ("web/app.js", "text/javascript; charset=utf-8"),
                      "/styles.css": ("web/styles.css", "text/css; charset=utf-8")}
            static["/batch.js"] = ("web/batch.js", "text/javascript; charset=utf-8")
            static["/batch.css"] = ("web/batch.css", "text/css; charset=utf-8")
            static["/evidence.js"] = ("web/evidence.js", "text/javascript; charset=utf-8")
            static["/evidence.css"] = ("web/evidence.css", "text/css; charset=utf-8")
            if path in static:
                relative, mime = static[path]
                self.send(200, (ROOT / relative).read_bytes(), mime)
                return
            if path.startswith("/prd/"):
                target = (ROOT / unquote(path).lstrip("/")).resolve()
                if target.is_relative_to((ROOT / "prd").resolve()) and target.is_file():
                    mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                            ".ttf": "font/ttf"}.get(target.suffix)
                    if mime:
                        self.send(200, target.read_bytes(), mime)
                        return
            raise APIError(404, "页面不存在。")
        except (APIError, AnalysisError) as error:
            self.send(getattr(error, "status", 400), {"error": str(error.message if isinstance(error, APIError) else error)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            LOG.error("GET failed")
            self.send(500, {"error": "服务内部错误。"})

    def do_POST(self):
        try:
            self.check_host()
            path = urlsplit(self.path).path
            if path in ("/api/login", "/api/register"):
                self.check_write()
                self.platform.rate_auth(self.client_address[0])
                body = self.body()
                username, password = body.get("username", ""), body.get("password", "")
                if not isinstance(username, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{3,40}", username):
                    raise APIError(400, "用户名应为 3–40 位字母、数字、下划线、点或短横线。")
                minimum = 8 if path == "/api/register" else 1
                if not isinstance(password, str) or not minimum <= len(password) <= 128:
                    raise APIError(400, "注册密码应为 8–128 个字符。" if path == "/api/register" else "请填写密码（最多 128 个字符）。")
                with self.platform.store.db() as db:
                    if path == "/api/register":
                        salt = secrets.token_hex(16)
                        try:
                            cursor = db.execute("INSERT INTO users(username,salt,password_hash,created_at) VALUES(?,?,?,?)",
                                                (username, salt, password_hash(password, salt), now()))
                        except sqlite3.IntegrityError:
                            raise APIError(409, "用户名已被使用，请更换或直接登录。")
                        owner = cursor.lastrowid
                    else:
                        row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
                        salt = row["salt"] if row else "00" * 16
                        hashed = password_hash(password, salt)
                        if not row or not hmac.compare_digest(hashed, row["password_hash"]):
                            raise APIError(401, "用户名或密码不正确。")
                        owner = row["id"]
                token, csrf = self.platform.store.create_session(owner)
                self.send(200, {"username": username, "csrf": csrf},
                          extra={"Set-Cookie": f"codelens_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_DAYS * 86400}"})
                return
            session = self.require_session()
            self.check_write(session)
            body = self.body()
            if path == "/api/logout":
                with self.platform.store.db() as db:
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (session["token_hash"],))
                self.send(200, {"ok": True}, extra={"Set-Cookie": "codelens_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
            elif path in ("/api/jobs", "/api/demo"):
                self.send(202, self.platform.submit(session["owner_id"], body, path == "/api/demo"))
            elif path == "/api/branches":
                self.send(200, {"branches": list_branches(body.get("repository", {}))})
            elif path in ("/api/batches", "/api/batch-demo"):
                if path == "/api/batch-demo":
                    body = {"repository": {"url": "https://github.com/example/graduation.git"},
                            "branches": [{"branch": f"student-{i+1:03d}", "label": f"示例作品 {i+1:03d}"} for i in range(50)],
                            "baseline": "teacher-template", "name": "50 个学生分支 · 毕业作品演示",
                            "review_threshold": body.get("review_threshold", 70), "options": body.get("options", {})}
                self.send(202, self.platform.submit_batch(session["owner_id"], body, path == "/api/batch-demo"))
            else:
                raise APIError(404, "接口不存在。")
        except (APIError, AnalysisError) as error:
            self.send(getattr(error, "status", 400), {"error": str(error.message if isinstance(error, APIError) else error)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            LOG.error("POST failed")
            self.send(500, {"error": "服务内部错误。"})


def make_server(host, port, data_dir):
    platform = Platform(Store(Path(data_dir) / "codelens.sqlite3"))
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.platform = platform
    actual_port = server.server_address[1]
    server.authorities = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}
    return server


def main():
    parser = argparse.ArgumentParser(description="CodeLens 本地代码相似性分析平台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", choices=["127.0.0.1", "localhost"], default="127.0.0.1")
    base = Path(os.environ.get("CODELENS_DATA_DIR", Path.home() / "Library" / "Application Support" / "CodeLens"))
    parser.add_argument("--data-dir", default=str(base))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    server = make_server(args.host, args.port, args.data_dir)
    print(f"\nCodeLens 已启动：http://127.0.0.1:{server.server_address[1]}\n按 Ctrl+C 停止。\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        server.platform.executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
