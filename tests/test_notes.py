import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit, parse_qs

SCRIPT = Path(__file__).resolve().parents[1] / "skills/github-notes/scripts/github_notes.py"
spec = importlib.util.spec_from_file_location("github_notes", SCRIPT)
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)


class FakeGitHub:
    def __init__(self):
        self.account = "tester"
        self.private = True
        self.writable = True
        self.files = {}
        self.commits = {}
        self.writes = []
        self.created = []
        self.fail_readback = False

    @staticmethod
    def sha(content):
        raw = content.encode()
        return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()

    def api(self, endpoint, method="GET", data=None):
        if endpoint == "user":
            return {"login": self.account}
        if endpoint == "user/repos" and method == "POST":
            self.created.append(data)
            return {"full_name": "tester/" + data["name"], "private": data["private"], "html_url": "https://github.com/tester/" + data["name"]}
        meta = {"full_name": "tester/notebook", "name": "notebook", "private": self.private,
                "description": "My research notes", "default_branch": "main", "size": 1,
                "permissions": {"push": self.writable}, "archived": False,
                "html_url": "https://github.com/tester/notebook"}
        if endpoint.startswith("user/repos?"):
            return [meta]
        if endpoint == "repos/tester/notebook":
            return meta
        if "/git/trees/" in endpoint:
            return {"tree": [{"type": "blob", "mode": "100644", "path": p} for p in self.files], "truncated": False}
        if "/contents/" in endpoint:
            parsed = urlsplit(endpoint)
            path = unquote(parsed.path.split("/contents/", 1)[1])
            ref = parse_qs(parsed.query).get("ref", ["main"])[0]
            if method == "PUT":
                old = self.files.get(path)
                if old is not None and data.get("sha") != self.sha(old):
                    raise notes.Error("Conflict", "conflict", 409)
                if old is None and "sha" in data:
                    raise notes.Error("Conflict", "conflict", 409)
                text = base64.b64decode(data["content"]).decode()
                self.files[path] = text
                self.writes.append(path)
                commit = str(len(self.writes)).zfill(40)
                self.commits[commit] = dict(self.files)
                return {"commit": {"sha": commit}, "content": {"html_url": "https://github.com/tester/notebook/blob/main/" + path}}
            if ref != "main" and self.fail_readback:
                raise notes.Error("Readback failed", "timeout")
            files = self.files if ref == "main" else self.commits.get(ref, {})
            if path not in files:
                raise notes.Error("Not found", "not_found", 404)
            text = files[path]
            return {"type": "file", "encoding": "base64", "size": len(text.encode()), "content": base64.b64encode(text.encode()).decode(),
                    "sha": self.sha(text), "html_url": "https://github.com/tester/notebook/blob/" + ref + "/" + path}
        raise AssertionError("Unexpected API endpoint: " + endpoint)


class NotesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"GITHUB_NOTES_CONFIG": str(Path(self.temp.name) / "config.json")})
        self.env.start()
        self.api = FakeGitHub()
        self.service = notes.Notes(self.api)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def bind(self):
        return self.service.notes_bind_repository("tester/notebook", "research")

    def test_first_use_binding_and_portable_marker(self):
        self.assertFalse(self.service.notes_status()["bound"])
        self.api.files["research/old.md"] = "# Existing note\n"
        self.assertEqual(self.service.notes_inspect_repository("tester/notebook")["suggested_root"], "research")
        self.bind()
        config = notes.config_path().read_text()
        self.assertNotIn("token", config.lower())
        self.assertEqual(notes.config_path().stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.service.notes_initialize()["verified"])
        self.assertFalse(self.service.notes_initialize()["changed"])
        self.assertEqual(self.api.files["research/old.md"], "# Existing note\n")
        notes.config_path().unlink()  # Simulate a fresh computer without personal binding.
        candidate = self.service.notes_discover()["candidates"][0]
        self.assertEqual(candidate["marker"]["root"], "research")
        self.service.notes_bind_repository("tester/notebook")
        self.assertEqual(notes.load_config()["root"], "research")

    def test_create_get_update_and_stale_sha(self):
        self.bind()
        result = self.service.notes_create("实验/公式 #1.md", "# 公式\n1/2 = 0.5\n")
        self.assertTrue(result["verified"])
        old = self.service.notes_get("实验/公式 #1.md")
        updated = self.service.notes_update("实验/公式 #1.md", "# 公式\n已验证\n", old["sha"])
        self.assertNotEqual(old["sha"], updated["sha"])
        count = len(self.api.writes)
        with self.assertRaises(notes.Error) as context:
            self.service.notes_update("实验/公式 #1.md", "覆盖他人修改", old["sha"])
        self.assertEqual(context.exception.code, "conflict")
        self.assertEqual(len(self.api.writes), count)
        with self.assertRaises(notes.Error) as context:
            self.service.notes_create("实验/公式 #1.md", "不能覆盖")
        self.assertEqual(context.exception.code, "already_exists")

    def test_append_and_duplicate_retry_conflict(self):
        self.bind()
        self.service.notes_create("test.md", "# Test\n")
        sha = self.service.notes_get("test.md")["sha"]
        self.service.notes_append("test.md", "Verified", sha)
        with self.assertRaises(notes.Error):
            self.service.notes_append("test.md", "Verified", sha)
        self.assertEqual(self.api.files["research/test.md"].count("Verified"), 1)

    def test_scope_and_sensitive_content(self):
        self.bind()
        for path in ("../other.md", "/other.md", "x/../../other.md", "x\\other.md", ".github/a.md", "x//a.md", "a.json", "x\n.md"):
            with self.subTest(path=path), self.assertRaises(notes.Error):
                self.service.notes_create(path, "content")
        with self.assertRaises(notes.Error) as context:
            self.service.notes_create("secret.md", "ghp_" + "a" * 40)
        self.assertEqual(context.exception.code, "possible_secret")
        self.assertEqual(self.api.writes, [])

    def test_repository_symlink_cannot_escape_notes_directory(self):
        self.bind()
        original_api = self.api.api
        def symlink_api(endpoint, method="GET", data=None):
            if "/git/trees/" in endpoint:
                return {"tree": [{"type": "blob", "mode": "120000", "path": "research/link.md"}], "truncated": False}
            return original_api(endpoint, method, data)
        self.api.api = symlink_api
        with self.assertRaises(notes.Error) as context:
            self.service.notes_get("link.md")
        self.assertEqual(context.exception.code, "invalid_path")
        with self.assertRaises(notes.Error):
            self.service.notes_create("link.md", "content")
        self.assertEqual(self.service.notes_list()["files"], [])

    def test_account_visibility_permissions_and_marker_changes(self):
        self.bind()
        self.api.account = "someone-else"
        with self.assertRaises(notes.Error) as context:
            self.service.notes_create("a.md", "data")
        self.assertEqual(context.exception.code, "account_changed")
        self.api.account = "tester"
        self.api.private = False
        with self.assertRaises(notes.Error) as context:
            self.service.notes_create("a.md", "data")
        self.assertEqual(context.exception.code, "public_repository")
        self.api.private = True
        self.api.writable = False
        with self.assertRaises(notes.Error) as context:
            self.service.notes_create("a.md", "data")
        self.assertEqual(context.exception.code, "not_writable")
        self.api.writable = True
        self.api.files[notes.MARKER] = json.dumps({"schema_version": 1, "root": "elsewhere"})
        with self.assertRaises(notes.Error) as context:
            self.service.notes_create("a.md", "data")
        self.assertEqual(context.exception.code, "binding_changed")

    def test_private_repository_default_and_public_opt_in(self):
        self.service.notes_create_repository()
        self.assertTrue(self.api.created[0]["private"])
        self.api.private = False
        with self.assertRaises(notes.Error):
            self.bind()
        self.service.notes_bind_repository("tester/notebook", "research", allow_public=True)
        self.assertTrue(self.service.notes_create("public.md", "Public by explicit choice")["verified"])

    def test_bounded_search_and_list(self):
        self.bind()
        self.api.files.update({"research/a.md": "# Apple\n", "research/b.md": "# Apple again\n", "docs/private.md": "Apple"})
        first = self.service.notes_search("Apple", limit=1)
        self.assertEqual(first["total_files"], 2)
        self.assertFalse(first["complete"])
        self.assertEqual(first["next_cursor"], 1)
        second = self.service.notes_search("Apple", cursor=1, limit=1)
        self.assertEqual(second["matches"][0]["path"], "b.md")
        self.assertEqual(self.service.notes_list()["files"], ["a.md", "b.md"])

    def test_commit_succeeded_readback_failed_is_not_retried(self):
        self.bind()
        self.api.fail_readback = True
        with self.assertRaises(notes.Error) as context:
            self.service.notes_create("a.md", "# Stored\n")
        self.assertEqual(context.exception.code, "verification_unknown")
        self.assertEqual(self.api.writes, ["research/a.md"])
        self.assertIn("research/a.md", self.api.files)

    def test_schema_blocks_unsafe_argument_types(self):
        for name, args in [("notes_bind_repository", {"repository": "tester/notebook", "allow_public": "false"}),
                           ("notes_create_repository", {"private": "false"}),
                           ("notes_list", {"cursor": True}), ("notes_get", {"path": "a.md", "repository": "elsewhere/repo"})]:
            with self.subTest(name=name), self.assertRaises(notes.Error):
                notes.call(name, args, self.service)

    def test_authorization_error_does_not_echo_credentials(self):
        fake = subprocess.CompletedProcess([], 1, "", "gh: invalid ghp_" + "z" * 40 + " (HTTP 401)")
        with patch.object(notes.shutil, "which", return_value="gh"), patch.object(notes.subprocess, "run", return_value=fake):
            with self.assertRaises(notes.Error) as context:
                notes.GitHub().api("user")
        self.assertEqual(context.exception.code, "auth_required")
        self.assertNotIn("ghp_", str(context.exception))


class ProtocolTests(unittest.TestCase):
    def test_stdio_handshake_tools_and_error(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "unknown", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        ]
        result = subprocess.run([sys.executable, str(SCRIPT), "stdio"], input="\n".join(json.dumps(r) for r in requests) + "\n", text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r["id"] for r in responses], [1, 2, 3, 4])
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-03-26")
        original_tools = {"notes_status", "notes_discover", "notes_inspect_repository", "notes_read_repository_file", "notes_bind_repository", "notes_create_repository", "notes_initialize", "notes_list", "notes_get", "notes_search", "notes_create", "notes_update", "notes_append"}
        self.assertTrue(original_tools.issubset({tool["name"] for tool in responses[1]["result"]["tools"]}))
        self.assertTrue(responses[2]["result"]["isError"])
        self.assertEqual(responses[3]["result"], {})


if __name__ == "__main__":
    unittest.main()
