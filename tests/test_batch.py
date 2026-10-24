"""Batch behavior checks, including immutable HTTP evidence and real local Git trees."""
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_platform as fixtures
from codelens import batch, git_source
from codelens.demo import SAMPLES
from codelens.engine import AnalysisError, validate_options
from codelens.report import render_report
from server import Store

OPTIONS = validate_options({})
BASE = SAMPLES["src/cart.js"]
WORK = '\nexport async function extra(values) { const table = new Map(); for (const item of values) { table.set(item.key, item.value * 77); } return JSON.stringify(Array.from(table.entries())); }\n'


def snap(branch, text=None, error=""):
    return (branch, [("src/work.js", text.encode(), "")] if text is not None else None,
            {"url": "https://github.com/example/graduation.git", "branch": branch, "commit": branch + "-fixed"}, error)


def run(rows, baseline="", threshold=70):
    rows = list(rows)
    items = [{"branch": r[0], "label": r[0]} for r in rows if r[0] != baseline]
    return batch.analyze_batch(rows, items, OPTIONS, baseline, threshold)


class BatchChecks(unittest.TestCase):
    def test_50_branch_full_pair_accounting_and_cache(self):
        rows = list(batch.demo_snapshots())
        result = run(rows, "teacher-template")
        self.assertEqual(result["summary"]["students"], 50)
        self.assertEqual(result["summary"]["pairs"], 1225)
        self.assertEqual(result["summary"]["ready"], 49)
        self.assertEqual(result["summary"]["empty"], 1)
        self.assertEqual(result["summary"]["completed_pairs"], 1176)
        self.assertEqual(result["summary"]["unavailable_pairs"], 49)
        self.assertLess(result["work"]["computed_pairs"], 1176)
        self.assertTrue(all(p["raw"] is None for p in result["pairs"] if p["status"] == "unavailable"))
        self.assertFalse(result["partial"])
        self.assertTrue(any(p["adjusted"]["coverage"] == 0 for p in result["pairs"] if p["status"] == "completed"))

    def test_template_only_intervals_and_original_line_numbers(self):
        blobs = {}
        rows = [snap("teacher", BASE), snap("a", BASE + WORK), snap("b", BASE + WORK.replace("extra", "renamed")), snap("empty", BASE)]
        items = [{"branch": s, "label": s} for s in ("a", "b", "empty")]
        result = batch.analyze_batch(rows, items, OPTIONS, "teacher", save=lambda branch, blob: blobs.update({branch: blob}))
        a, b = batch.decode_prepared(blobs["a"]), batch.decode_prepared(blobs["b"])
        detail = batch.compare(a, b, OPTIONS, evidence=True)
        self.assertGreater(result["students"][0]["template_tokens"], 0)
        self.assertGreater(result["students"][0]["independent_tokens"], 24)
        self.assertEqual(detail["summary"], result["pairs"][0]["adjusted"])
        self.assertEqual(detail["summary"]["a_coverage"], 100)
        self.assertGreater(detail["pairs"][0]["evidence"][0]["a"]["start_line"], BASE.count("\n"))
        self.assertGreater(result["pairs"][0]["raw"]["a_tokens"], detail["summary"]["a_tokens"])
        self.assertEqual(result["students"][2]["status"], "empty")

    def test_remaining_short_fragments_are_unavailable(self):
        # Tail smaller than min_match cannot produce independent evidence.
        rows = [snap("teacher", BASE), snap("a", BASE + WORK), snap("b", BASE + WORK), snap("short", BASE + "\nconst x = 1;")]
        result = run(rows, "teacher")
        short = result["students"][-1]
        self.assertGreater(short["independent_tokens"], 0)
        self.assertEqual(short["status"], "empty")
        self.assertIsNone(result["pairs"][-1]["adjusted"])

    def test_partial_failure_empty_and_zero_are_distinct(self):
        result = run([snap("a", BASE), snap("b", WORK), snap("missing", error="分支不存在"), snap("empty", "// nothing")])
        self.assertTrue(result["partial"])
        self.assertEqual(result["summary"]["failed_students"], 1)
        self.assertEqual(result["summary"]["empty"], 1)
        self.assertEqual(result["pairs"][0]["status"], "completed")
        self.assertEqual(result["pairs"][0]["raw"]["coverage"], 0)
        self.assertEqual(result["summary"]["unavailable_pairs"], 5)
        report = render_report({"kind":"batch","name":"<script>bad</script>","created_at":"now",
                                "status":"partial","sources":{"repository":{"url":"demo"}},"result":result})
        self.assertIn("N/A", report)
        self.assertIn("部分完成", report)
        self.assertNotIn("<script>", report)
        self.assertIn("有效 token 少于最小片段长度", report)

    def test_missing_or_empty_template_fails_whole_batch(self):
        for teacher in [snap("teacher", error="not found"), snap("teacher", "// no code")]:
            with self.assertRaisesRegex(AnalysisError, "教师模板"):
                run([teacher, snap("a", BASE), snap("b", BASE)], "teacher")

    def test_smaller_submission_containment_triggers_review(self):
        large = BASE + '\n' + WORK
        result = run([snap("small", BASE), snap("large", large)], threshold=90)
        p = result["pairs"][0]["adjusted"]
        self.assertLess(p["coverage"], 90)
        self.assertEqual(p["a_coverage"], 100)
        self.assertEqual(result["summary"]["review_pairs"], 1)

    def test_batch_validates_duplicates_template_and_limits(self):
        base = {"repository":{"url":"https://github.com/example/a.git"},"branches":["a","b"]}
        for changes in [{"branches":["a","a"]},{"baseline":"a"},{"branches":["a"]},{"review_threshold":True},
                        {"branches":["a", "--evil"]},{"branches":[str(i) for i in range(101)]}]:
            with self.assertRaises(AnalysisError):
                batch.validate_batch({**base, **changes}, git_source.validate_source)
        source, items, baseline, threshold, _ = batch.validate_batch(base, git_source.validate_source)
        self.assertEqual(len(items), 2)

    def test_content_cache_keeps_paths_and_tokenizes_once(self):
        cache = {}
        with patch("codelens.engine.tokenize", wraps=__import__("codelens.engine", fromlist=["tokenize"]).tokenize) as tokenize:
            a = batch.prepare([("a.js",BASE.encode(),"")], {}, OPTIONS, cache)
            b = batch.prepare([("moved.js",BASE.encode(),"")], {}, OPTIONS, cache)
            self.assertEqual(tokenize.call_count, 1)
        self.assertEqual(b["files"][0].path, "moved.js")
        self.assertEqual(batch.compare(a,b,OPTIONS)["summary"]["a_coverage"], 100)

    def test_pair_comparison_failure_is_partial(self):
        with patch("codelens.batch.compare", side_effect=AnalysisError("候选超限")):
            result = run([snap("a", BASE), snap("b", BASE)])
        self.assertTrue(result["partial"])
        self.assertEqual(result["pairs"][0]["status"], "failed")
        self.assertIsNone(result["pairs"][0]["adjusted"])

    def test_real_git_50_refs_shared_tree_and_missing_branch(self):
        # Production Git fetching/archiving runs against a disposable fixture.
        # Only authorization and transport flags are adapted for local test files.
        with tempfile.TemporaryDirectory(prefix="codelens-localgit-") as temp:
            root = Path(temp); remote = root / "remote"; remote.mkdir()
            def git(*args):
                return subprocess.check_output(["git", "-C", str(remote), *args], stderr=subprocess.DEVNULL).decode().strip()
            git("init"); git("config","user.email","fixture@example.test"); git("config","user.name","Fixture")
            (remote / "work.js").write_text(BASE + WORK); git("add","."); git("commit","-m","fixture")
            sha = git("rev-parse","HEAD")
            names = [f"student-{i:03d}" for i in range(50)]
            for name in names: git("branch",name)
            source = {"url":str(remote),"branch":"","username":"","token":""}
            with patch.object(git_source, "validate_source", side_effect=lambda s: dict(s)), patch.object(git_source,"GIT",["git"]):
                with patch.object(git_source,"archive_entries",wraps=git_source.archive_entries) as archive:
                    rows = list(git_source.batch_snapshots(source, names + ["missing"], root/"fetched", []))
                    self.assertEqual(archive.call_count, 1)
            self.assertEqual(len(rows),51)
            self.assertEqual(sum(bool(row[3]) for row in rows),1)
            self.assertTrue(all(row[2]["commit"] == sha for row in rows if not row[3]))
            result = run(rows)
            self.assertEqual(result["summary"]["completed_pairs"],1225)
            self.assertEqual(result["summary"]["unavailable_pairs"],50)
            self.assertEqual(result["work"]["computed_pairs"],1)

    def test_remote_branch_movement_is_not_silently_accepted(self):
        source = {"url":"https://github.com/example/a.git","branch":""}
        def fake_git(args, *_, **__):
            if args[0] == "ls-remote":
                return "a"*40 + "\trefs/heads/moving\n" + "c"*40 + "\trefs/heads/stable\n"
            if args[0] == "rev-parse":
                return "b"*40 if "/0^" in args[-1] else "c"*40
            return ""
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(git_source,"validate_source",side_effect=lambda s:dict(s)), patch.object(git_source,"run_git",side_effect=fake_git), patch.object(git_source,"archive_entries",return_value=[("a.js",BASE.encode(),"")]):
                rows = list(git_source.batch_snapshots(source,["moving","stable"],Path(temp)/"repo",[]))
        by_branch = {r[0]:r for r in rows}
        self.assertIn("发生变动",by_branch["moving"][3])
        self.assertFalse(by_branch["stable"][3])
        self.assertEqual(by_branch["stable"][2]["commit"],"c"*40)

    def test_batch_repository_limit_fails_entire_fetch(self):
        def fake_git(args,*_,**__):
            if args[0] == "ls-remote": return "a"*40 + "\trefs/heads/a\n"
            if args[0] == "fetch": raise AnalysisError("oversize")
            return ""
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(git_source,"validate_source",side_effect=lambda s:dict(s)), patch.object(git_source,"run_git",side_effect=fake_git), patch.object(git_source,"directory_size",return_value=1024*1024*1024+1):
                with self.assertRaisesRegex(AnalysisError,"批次 Git 对象累计"):
                    list(git_source.batch_snapshots({"url":"mock","branch":""},["a"],Path(temp)/"repo",[]))


class BatchHTTPChecks(unittest.TestCase):
    setUpClass = classmethod(fixtures.HTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.HTTPTests.tearDownClass.__func__)
    client = fixtures.HTTPTests.client
    request = fixtures.HTTPTests.request

    def wait(self, client, task):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            _, job = self.request(client,"/api/jobs/"+task["id"])
            if job["status"] in ("completed","partial","failed"): return job
            time.sleep(.02)
        self.fail("batch timeout")

    def test_demo_evidence_scopes_persistence_isolation_and_export(self):
        first, other = self.client(), self.client()
        _, owner = self.request(first,"/api/register",{"username":"batchowner","password":"test-password"})
        _, stranger = self.request(other,"/api/register",{"username":"batchother","password":"test-password"})
        self.assertEqual(self.request(first,"/api/batch-demo",{})[0],403)
        code, task = self.request(first,"/api/batch-demo",{},owner["csrf"])
        self.assertEqual(code,202)
        job = self.wait(first,task)
        self.assertEqual(job["status"],"completed",job)
        self.assertEqual(job["result"]["summary"]["pairs"],1225)
        prefix = "/api/jobs/"+task["id"]
        for suffix in ["","/report","/pairs/0-1","/pairs/0-1/report"]:
            self.assertEqual(self.request(other,prefix+suffix)[0],404)
        self.assertEqual(self.request(first,prefix+"/pairs/0-49")[0],409)
        self.assertEqual(self.request(first,prefix+"/pairs/999-999")[0],404)
        self.assertEqual(self.request(first,prefix+"/pairs/0-1?scope=evil")[0],400)
        _, adjusted = self.request(first,prefix+"/pairs/0-1")
        _, raw = self.request(first,prefix+"/pairs/0-1?scope=raw")
        self.assertEqual(adjusted["result"]["summary"],job["result"]["pairs"][0]["adjusted"])
        self.assertGreater(raw["result"]["summary"]["a_tokens"],adjusted["result"]["summary"]["a_tokens"])
        # Reopen Store to verify completed snapshots survive service initialization.
        reopened = Store(self.server.platform.store.path)
        self.assertEqual(reopened.job(task["id"],1)["status"],"completed")
        self.assertEqual(self.request(first,prefix+"/pairs/0-1")[0],200)
        code, report = self.request(first,prefix+"/report")
        self.assertEqual(code,200); self.assertIn("不含全部代码证据".encode(),report)
        self.assertEqual(self.request(first,prefix+"/pairs/0-1/report?scope=raw")[0],200)
        self.assertEqual(self.request(first,"/api/jobs")[1]["jobs"][0]["kind"],"batch")
        for url in ("/batch.js","/batch.css","/prd/prd.html"):
            self.assertEqual(self.request(first,url)[0],200)

    def test_mocked_missing_branch_finishes_partial_without_credentials(self):
        client = self.client()
        _, owner = self.request(client,"/api/register",{"username":"partialowner","password":"test-password"})
        body = {"repository":{"url":"https://github.com/example/a.git","token":"secret-marker"},
                "branches":["a","b","missing"]}
        with patch("server.batch_snapshots", return_value=iter([snap("a",BASE),snap("b",BASE),snap("missing",error="missing")])):
            code, task = self.request(client,"/api/batches",body,owner["csrf"])
            self.assertEqual(code,202)
            job = self.wait(client,task)
        self.assertEqual(job["status"],"partial",job)
        self.assertEqual(job["result"]["summary"]["unavailable_pairs"],2)
        self.assertNotIn("secret-marker",json.dumps(job))
        self.assertEqual(self.request(client,"/api/jobs/"+task["id"]+"/report")[0],200)


if __name__ == "__main__":
    unittest.main()
