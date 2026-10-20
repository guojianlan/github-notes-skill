"""Offline behavioral checks for categories and images (no GitHub writes)."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

import importlib.util

SCRIPT = Path(__file__).resolve().parents[1] / "skills/github-notes/scripts/github_notes.py"
spec = importlib.util.spec_from_file_location("github_notes_features", SCRIPT)
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aAZkAAAAASUVORK5CYII=")
CFG = {"repository": "tester/notes", "branch": "main", "root": "research"}


class FakeGitHub:
    def __init__(self):
        self.files = {"research/education/a.md": b"alpha needle", "research/education/child/b.md": b"beta needle",
                      "research/educational/c.md": b"not in education", "research/README.md": b"index",
                      "docs/old.md": b"legacy"}
        self.snapshots = {}
        self.puts = []
        self.force_blob = False
        self.fail_readback = False
        self.corrupt_readback = False
        self.links = []

    @staticmethod
    def sha(data):
        return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

    def api(self, endpoint, method="GET", data=None):
        route = urlsplit(endpoint)
        if "/git/trees/" in route.path:
            return {"tree": [{"path": path, "type": "blob", "mode": "100644"} for path in self.files] + self.links}
        if "/git/blobs/" in route.path:
            sha = route.path.rsplit("/", 1)[-1]
            payload = next(payload for payload in self.files.values() if self.sha(payload) == sha)
            return {"encoding": "base64", "content": base64.b64encode(payload).decode()}
        path = unquote(route.path.split("/contents/", 1)[1])
        if method == "PUT":
            if path in self.files:
                raise notes.Error("Already exists", "conflict", 409)
            payload = base64.b64decode(data["content"])
            self.files[path] = payload
            commit = hashlib.sha1(str(len(self.puts)).encode()).hexdigest()
            self.snapshots[commit] = copy.deepcopy(self.files)
            self.puts.append(path)
            return {"commit": {"sha": commit}, "content": {"html_url": "https://github.com/tester/notes/blob/main/" + path}}
        ref = parse_qs(route.query)["ref"][0]
        if ref != "main" and self.fail_readback:
            raise notes.Error("read failed", "timeout")
        files = self.files if ref == "main" else self.snapshots[ref]
        if path not in files:
            raise notes.Error("missing", "not_found", 404)
        payload = files[path]
        if ref != "main" and self.corrupt_readback:
            payload += b"unexpected"
        return {"type": "file", "size": len(payload), "sha": self.sha(payload),
                "html_url": "https://github.com/tester/notes/blob/" + ref + "/" + path,
                "encoding": "none" if self.force_blob else "base64",
                "content": "" if self.force_blob else base64.b64encode(payload).decode()}


class Service(notes.Notes):
    def binding(self, write=False):
        self.last_write_check = write
        return CFG, {}


class Features(unittest.TestCase):
    def setUp(self):
        self.github = FakeGitHub()
        self.service = Service(self.github)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "image.png"
        self.source.write_bytes(PNG)
        self.target = "assets/education/a/overview.png"

    def assert_code(self, code, operation):
        with self.assertRaises(notes.Error) as result:
            operation()
        self.assertEqual(result.exception.code, code)

    def test_category_prefix_includes_children_without_sibling_leak(self):
        result = self.service.notes_list(category="education", limit=1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["next_cursor"], 1)
        next_page = self.service.notes_list(category="education", cursor=1, limit=1)
        self.assertEqual(next_page["files"], ["education/child/b.md"])
        self.assertIsNone(next_page["next_cursor"])

    def test_search_filters_and_reports_coverage(self):
        result = self.service.notes_search("needle", category="education")
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["matches"]), 2)
        self.assertEqual(result["total_files"], 2)
        self.assertEqual(self.service.notes_search("needle", category="missing")["matches"], [])

    def test_categories_count_nested_and_report_bound_scope(self):
        result = self.service.notes_categories()
        counts = {item["path"]: item["markdown_count"] for item in result["categories"]}
        self.assertEqual(counts, {"education": 2, "education/child": 1, "educational": 1})
        self.assertEqual(result["total_markdown"], 4)
        self.assertEqual(result["root_files"], ["README.md"])

    def test_traversal_and_unsupported_assets_rejected_before_write(self):
        for path in ["assets/../secret.png", "/assets/a.png", "assets/a.svg", "a.png", "assets/.hidden/a.png"]:
            self.assert_code("invalid_path", lambda: self.service.notes_asset_create(path, str(self.source)))
        self.assert_code("invalid_path", lambda: self.service.notes_list(category="../docs"))
        self.assertEqual(self.github.puts, [])

    def test_symlink_and_submodule_cannot_escape_bound_root(self):
        for mode in ["120000", "160000"]:
            self.github.links = [{"path": "research/assets/education", "type": "blob", "mode": mode}]
            self.assert_code("invalid_path", lambda: self.service.notes_asset_create(self.target, str(self.source)))
        self.assertEqual(self.github.puts, [])

    def test_image_upload_readback_and_safe_identical_retry(self):
        result = self.service.notes_asset_create(self.target, str(self.source))
        self.assertTrue(self.service.last_write_check)
        self.assertTrue(result["verified"])
        self.assertEqual(result["sha256"], hashlib.sha256(PNG).hexdigest())
        self.assertEqual(self.github.files["research/" + self.target], PNG)
        again = self.service.notes_asset_create(self.target, str(self.source))
        self.assertFalse(again["changed"])
        self.assertEqual(len(self.github.puts), 1)
        info = self.service.notes_asset_info(self.target)
        self.assertEqual(info["sha256"], result["sha256"])

    def test_changed_image_refuses_overwrite(self):
        self.service.notes_asset_create(self.target, str(self.source))
        self.source.write_bytes(PNG + b"different")
        self.assert_code("already_exists", lambda: self.service.notes_asset_create(self.target, str(self.source)))
        self.assertEqual(self.github.files["research/" + self.target], PNG)

    def test_large_contents_response_falls_back_to_blob(self):
        self.service.notes_asset_create(self.target, str(self.source))
        self.github.force_blob = True
        self.assertEqual(self.service.notes_asset_info(self.target)["sha256"], hashlib.sha256(PNG).hexdigest())

    def test_unknown_verification_can_be_recovered_without_duplicate_write(self):
        self.github.fail_readback = True
        self.assert_code("verification_unknown", lambda: self.service.notes_asset_create(self.target, str(self.source)))
        self.github.fail_readback = False
        self.assertTrue(self.service.notes_asset_info(self.target)["verified"])
        self.assertFalse(self.service.notes_asset_create(self.target, str(self.source))["changed"])
        self.assertEqual(len(self.github.puts), 1)

    def test_readback_difference_is_reported(self):
        self.github.corrupt_readback = True
        self.assert_code("verification_failed", lambda: self.service.notes_asset_create(self.target, str(self.source)))

    def test_local_input_signature_and_size_limits(self):
        self.source.write_text("this is not an image")
        self.assert_code("invalid_image", lambda: self.service.notes_asset_create(self.target, str(self.source)))
        self.source.write_bytes(PNG)
        self.assert_code("invalid_source", lambda: self.service.notes_asset_create(self.target, "relative.png"))
        with self.source.open("wb") as stream:
            stream.truncate(notes.MAX_ASSET_BYTES + 1)
        self.assert_code("file_too_large", lambda: self.service.notes_asset_create(self.target, str(self.source)))
        self.assertEqual(self.github.puts, [])

    def test_stdio_exposes_categories_and_assets_and_optional_filters(self):
        requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        result = subprocess.run([sys.executable, str(Path(notes.__file__)), "stdio"],
                                input="\n".join(json.dumps(r) for r in requests) + "\n", text=True, capture_output=True, check=True)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(responses[0]["result"]["serverInfo"]["version"], "1.1.0")
        tools = {tool["name"]: tool for tool in responses[1]["result"]["tools"]}
        self.assertIn("notes_asset_create", tools)
        self.assertIn("notes_categories", tools)
        self.assertIn("category", tools["notes_search"]["inputSchema"]["properties"])


if __name__ == "__main__":
    unittest.main()
