import http.cookiejar
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from codelens.demo import sources, SAMPLES
from codelens.engine import AnalysisError, analyze, excluded, fingerprints, merge_intervals, tokenize, validate_options
from codelens.git_source import validate_source
from codelens.report import render_report
from server import make_server, Store


def entry(path, text):
    return [(path, text.encode(), "")]


class EngineTests(unittest.TestCase):
    def test_supported_languages_renaming_and_reordering(self):
        a, b = sources()
        r = analyze(a, b)
        self.assertEqual({p["language"] for p in r["pairs"]}, {"Java", "JavaScript", "HTML", "C", "C++", "C#", "PHP"})
        self.assertEqual(r["summary"]["a_coverage"], 100)
        self.assertLess(r["summary"]["b_coverage"], 100)
        js = next(p for p in r["pairs"] if p["language"] == "JavaScript")
        self.assertTrue(js["reordered"])
        self.assertEqual(len(js["evidence"]), 2)
        self.assertTrue(all(p["relocated"] for p in r["pairs"] if p["language"] != "HTML"))
        self.assertFalse(next(p for p in r["pairs"] if p["language"] == "HTML")["relocated"])
        strict = analyze(a, b, {"normalize_identifiers": False})
        self.assertLess(strict["summary"]["coverage"], r["summary"]["coverage"])

    def test_coverage_deduplicates_repeated_destination_files(self):
        a = entry("a.js", SAMPLES["src/cart.js"])
        b = entry("b.js", SAMPLES["src/cart.js"]) + entry("c.js", SAMPLES["src/cart.js"])
        r = analyze(a, b)
        self.assertEqual(r["summary"]["pairs"], 2)
        self.assertEqual(r["summary"]["a_coverage"], 100)
        self.assertEqual(r["summary"]["b_coverage"], 100)
        self.assertEqual(r["summary"]["a_matched"], r["summary"]["a_tokens"])

    def test_minimum_fingerprint_guarantee_with_shifted_context(self):
        for minimum in [12, 24, 50, 100]:
            values = [f"keyword-{i}" for i in range(minimum)]
            for shift in range(8):
                left = fingerprints(["left"] * shift + values + ["tail-A"], minimum)
                right = fingerprints(["right"] * (8 - shift) + values + ["tail-B"], minimum)
                self.assertTrue(set(left) & set(right))

    def test_literals_and_comments_have_explicit_rules(self):
        text = 'export function message() { const value = "first"; const count = 12; if (count > 3) { return value + count; } return "none"; }'
        modified = text.replace('"first"', '"second"').replace("12", "99")
        literal = analyze(entry("a.js", text), entry("b.js", modified), {"normalize_literals": True})
        strict = analyze(entry("a.js", text), entry("b.js", modified))
        self.assertEqual(literal["summary"]["coverage"], 100)
        self.assertLess(strict["summary"]["coverage"], 100)
        comment = analyze(entry("a.js", text), entry("b.js", "// explanation\n" + text))
        self.assertEqual(comment["summary"]["coverage"], 100)
        self.assertEqual(comment["pairs"][0]["evidence"][0]["b"]["start_line"], 2)
        c = tokenize("a.c", "#define LIMIT 10\nint main(void) { return LIMIT; }", validate_options({}))
        self.assertTrue(any("define" in v or "LIMIT" in v for v in c.values))

    def test_exclusions_encoding_and_empty_states(self):
        self.assertTrue(excluded(".github/workflows/a.js", [".github/**"]))
        self.assertTrue(excluded("src/tests/a.js", ["tests/"]))
        r = analyze([("a.java", b"\xff\xff", ""), ("node_modules/a.js", b"x", "")], [])
        self.assertEqual(r["summary"]["a_tokens"], 0)
        self.assertEqual(r["summary"]["coverage"], 0)
        self.assertEqual(r["summary"]["skipped"], 2)
        self.assertTrue(r["warnings"])

    def test_cross_language_is_not_compared(self):
        text = SAMPLES["native/cart.c"]
        r = analyze(entry("a.c", text), entry("b.cpp", text))
        self.assertEqual(r["summary"]["pairs"], 0)

    def test_no_match_for_different_structures(self):
        a = 'function tally(values) { let sum = 0; for (let i = 0; i < values.length; i++) { sum += values[i]; } return sum; }'
        b = 'class Clock extends Date { static async fetch() { try { await Promise.resolve("ready"); } catch (error) { throw new Error("broken"); } finally { console.log("done"); } } }'
        self.assertEqual(analyze(entry("a.js", a), entry("b.js", b))["summary"]["pairs"], 0)

    def test_option_validation_and_interval_union(self):
        for options in [{"min_match": True}, {"min_match": 11}, {"normalize_literals": "yes"}, {"excludes": "src/**"}]:
            with self.assertRaises(AnalysisError):
                validate_options(options)
        self.assertEqual(merge_intervals([(2, 5), (0, 3), (5, 7), (10, 12)]), [(0, 7), (10, 12)])

    def test_report_escapes_untrusted_html_and_placeholder_text(self):
        text = SAMPLES["pages/cart.html"].replace("购物车", '<img src=x onerror="alert(1)">__TITLE__')
        r = analyze(entry("a.html", text), entry("b.html", text))
        job = {"name": "<script>alert(1)</script>", "created_at": "now", "sources": {
            "a": {"url": "<b>remote</b>", "branch": "main"},
            "b": {"url": "remote", "branch": "other"},
        }, "result": r}
        report = render_report(job)
        self.assertNotIn("<script>", report)
        self.assertNotIn("<img src=x", report)
        self.assertIn("&lt;img", report)
        self.assertIn("__TITLE__", report)
        self.assertNotIn('src="https://', report)


class SourceTests(unittest.TestCase):
    def test_rejects_credentials_and_private_network(self):
        for url in ["file:///tmp/repo", "https://user:secret@github.com/a/b",
                    "https://localhost/a/b", "https://github.com/a/b?token=secret"]:
            with self.assertRaises(AnalysisError):
                validate_source({"url": url}, check_dns=False)
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(AnalysisError):
                validate_source({"url": "https://git.example.com/a/b"})
        with self.assertRaises(AnalysisError):
            validate_source({"url": "https://github.com/a/b", "branch": "--upload-pack=evil"}, check_dns=False)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="codelens-tests-")
        cls.server = make_server("127.0.0.1", 0, cls.temp.name)
        cls.origin = "http://127.0.0.1:" + str(cls.server.server_address[1])
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server.platform.executor.shutdown(wait=True)
        cls.temp.cleanup()

    def client(self):
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, client, path, body=None, csrf="", origin=None):
        headers = {"Origin": origin or self.origin, "X-CSRF-Token": csrf}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            response = client.open(urllib.request.Request(self.origin + path, data=data, headers=headers))
            raw = response.read()
            return response.status, json.loads(raw) if "application/json" in response.headers["Content-Type"] else raw
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_login_task_evidence_export_isolation_csrf_and_logout(self):
        first, second = self.client(), self.client()
        code, owner = self.request(first, "/api/register", {"username": "owner", "password": "test-password"})
        self.assertEqual(code, 200)
        self.assertEqual(self.request(second, "/api/jobs")[0], 401)
        self.assertEqual(self.request(first, "/api/demo", {})[0], 403)
        self.assertEqual(self.request(first, "/api/demo", {}, owner["csrf"], "https://attacker.example")[0], 403)
        code, task = self.request(first, "/api/demo", {}, owner["csrf"])
        self.assertEqual(code, 202)
        deadline = time.monotonic() + 5
        job = None
        while time.monotonic() < deadline:
            code, job = self.request(first, "/api/jobs/" + task["id"])
            if job["status"] in ("completed", "failed"):
                break
            time.sleep(.01)
        self.assertEqual(job["status"], "completed", job)
        self.assertEqual(job["result"]["summary"]["pairs"], 7)
        self.assertEqual(self.request(first, "/api/jobs/" + task["id"] + "/report")[0], 200)
        _, other = self.request(second, "/api/register", {"username": "other", "password": "test-password"})
        self.assertEqual(self.request(second, "/api/jobs/" + task["id"])[0], 404)
        self.assertEqual(self.request(second, "/api/jobs/" + task["id"] + "/report")[0], 404)
        self.assertEqual(self.request(second, "/api/jobs")[1]["jobs"], [])
        self.assertEqual(self.request(first, "/api/logout", {}, owner["csrf"])[0], 200)
        self.assertEqual(self.request(first, "/api/jobs")[0], 401)
        self.assertEqual(self.request(first, "/api/login", {"username": "owner", "password": "incorrect-password"})[0], 401)
        self.assertEqual(self.request(first, "/api/login", {"username": "owner", "password": "test-password"})[0], 200)

    def test_restart_marks_interrupted_jobs_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.sqlite3"
            store = Store(path)
            with store.db() as db:
                db.execute("INSERT INTO jobs(id,owner_id,name,status,message,created_at,sources,options) VALUES('abc',1,'test','fetching','','now','{}','{}')")
            recovered = Store(path)
            self.assertEqual(recovered.job("abc", 1)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
