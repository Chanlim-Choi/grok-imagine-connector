"""Offline tests: all paid requests and downloads are mocked; never hit xAI."""
import base64
import importlib.util
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

SERVER = pathlib.Path(__file__).resolve().parents[1] / "secure_mcp" / "server.py"
spec = importlib.util.spec_from_file_location("grok_safe", SERVER)
safe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safe)
PNG = b"\x89PNG\r\n\x1a\n" + b"safe-test-data"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"safe-test-video"
ID = "d97415a1-5796-b7ec-379f-4e6819e08fdf"


@contextmanager
def configured(root):
    inp = root / "input"
    out = root / "output"
    inp.mkdir()
    out.mkdir()
    with patch.dict(os.environ, {"GROK_OUTPUT_DIR": str(out), "GROK_INPUT_DIR": str(inp),
                              "GROK_ALLOW_PAID_REQUESTS": "YES", "XAI_API_KEY": "top-secret"}):
        safe.JOBS = 0
        yield inp, out


class SecurityTests(unittest.TestCase):
    def test_protocol_handshake_and_tool_discovery_without_key(self):
        env = os.environ.copy()
        for k in ("XAI_API_KEY", "GROK_ALLOW_PAID_REQUESTS"):
            env.pop(k, None)
        req = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ]
        import json
        run = subprocess.run([sys.executable, str(SERVER)], input="\n".join(map(json.dumps, req)) + "\n",
                             text=True, capture_output=True, env=env, timeout=5)
        self.assertEqual(run.returncode, 0, run.stderr)
        replies = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(len(replies[1]["result"]["tools"]), 5)
        self.assertEqual(run.stderr, "")

    def test_missing_opt_in_blocks_billable_calls(self):
        with tempfile.TemporaryDirectory() as tmp, configured(pathlib.Path(tmp)):
            with patch.dict(os.environ, {"GROK_ALLOW_PAID_REQUESTS": "NO"}):
                with patch.object(safe, "_json_api") as network:
                    with self.assertRaisesRegex(safe.ToolError, "Paid generation disabled"):
                        safe.generate_image({"prompt": "red fox"})
                    network.assert_not_called()

    def test_output_restricted_to_directory_and_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp, configured(pathlib.Path(tmp)) as (_, out):
            with patch.object(safe, "_json_api", return_value={"data": [{"b64_json": base64.b64encode(PNG).decode()}]}):
                self.assertEqual(pathlib.Path(safe.generate_image({"prompt": "fox", "name": "render"})["file"]), out / "render.png")
                self.assertEqual((out / "render.png").read_bytes(), PNG)
                with self.assertRaises(safe.ToolError):
                    safe.generate_image({"prompt": "fox", "name": "render"})

    def test_rejects_path_traversal_bad_stems(self):
        for name in ("../escape", "..", "../input/a", "/tmp/a", "C:\\evil", "dir/file", "a..b", "", ".hidden"):
            with self.subTest(name=name), self.assertRaises(safe.ToolError):
                safe._stem(name, "out")

    def test_image_input_rejects_symlinks_and_traversal(self):
        with tempfile.TemporaryDirectory() as tmp, configured(pathlib.Path(tmp)) as (inp, _):
            (inp / "test.png").write_bytes(PNG)
            self.assertEqual(safe._read_image("test.png").split(",", 1)[0], "data:image/png;base64")
            for name in ("../test.png", "C:\\secret.png", "../../etc/passwd"):
                with self.assertRaises(safe.ToolError):
                    safe._read_image(name)
            try:
                (inp / "link.png").symlink_to(inp / "test.png")
            except (NotImplementedError, OSError):
                self.skipTest("symlink creation not supported")
            with self.assertRaises(safe.ToolError):
                safe._read_image("link.png")

    def test_edit_only_uses_fixed_api_and_reads_staged_file(self):
        with tempfile.TemporaryDirectory() as tmp, configured(pathlib.Path(tmp)) as (inp, out):
            (inp / "ref.png").write_bytes(PNG)
            with patch.object(safe, "_json_api", return_value={"data": [{"b64_json": base64.b64encode(PNG).decode()}]}) as api:
                res = safe.edit_image({"prompt": "make blue", "image_name": "ref.png", "name": "edited"})
                self.assertEqual(pathlib.Path(res["file"]), out / "edited.png")
                self.assertEqual(api.call_args.args[:2], ("POST", "/v1/images/edits"))
                self.assertIn("data:image/png;base64,", api.call_args.args[2]["image"]["url"])

    def test_no_arbitrary_generation_arguments(self):
        with tempfile.TemporaryDirectory() as tmp, configured(pathlib.Path(tmp)):
            with patch.object(safe, "_json_api") as api:
                for args in ({"prompt": "hi", "url": "https://evil.example"},
                             {"prompt": "hi", "duration": True},
                             {"prompt": "hi", "duration": 1000}):
                    with self.assertRaises(safe.ToolError):
                        safe.start_video(args)
                api.assert_not_called()

    def test_per_process_limit(self):
        with tempfile.TemporaryDirectory() as tmp, configured(pathlib.Path(tmp)):
            with patch.object(safe, "_json_api", return_value={"request_id": ID}) as api:
                for _ in range(5):
                    safe.start_video({"prompt": "safe scene"})
                with self.assertRaisesRegex(safe.ToolError, "limit reached"):
                    safe.start_video({"prompt": "safe scene"})
                self.assertEqual(api.call_count, 5)

    def test_safe_video_request_identifier(self):
        for invalid in ("../../etc/passwd", "bad", ID + "/a", "https://evil", ID + "?x=1"):
            with self.assertRaises(safe.ToolError):
                safe._uuid(invalid)
        self.assertEqual(safe._uuid(ID), ID)

    def test_video_host_allowlist(self):
        allowed = "https://vidgen.x.ai/data/hello/video.mp4?signature=abc"
        self.assertEqual(safe._media_url(allowed), allowed)
        for host in ("localhost", "127.0.0.1", "169.254.169.254", "vidgen.x.ai.evil.com", "evil.com"):
            with self.subTest(host=host), self.assertRaises(safe.ToolError):
                safe._media_url("https://" + host + "/foo.mp4")
        for bad in ("http://vidgen.x.ai/foo.mp4", "https://me:pass@vidgen.x.ai/a.mp4",
                    "https://vidgen.x.ai:444/foo", "https://vidgen.x.ai/a#fragment", "https://vidgen.x.ai\\@evil.com/a"):
            with self.subTest(bad=bad), self.assertRaises(safe.ToolError):
                safe._media_url(bad)

    def test_video_download_is_bounded_and_requires_magic(self):
        class FakeResponse:
            def __init__(self, content):
                self.stream = io.BytesIO(content)
                self.headers = {"Content-Length": str(len(content))}
            def read(self, size):
                return self.stream.read(size)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        class Opener:
            def __init__(self, content):
                self.content = content
            def open(self, request, timeout):
                return FakeResponse(self.content)
        with tempfile.TemporaryDirectory() as tmp, configured(pathlib.Path(tmp)) as (_, out):
            with patch.object(safe, "_opener", return_value=Opener(MP4)):
                value = safe._download_video("https://vidgen.x.ai/m.mp4", "vid")
                self.assertEqual(pathlib.Path(value["file"]).read_bytes(), MP4)
            with patch.object(safe, "_opener", return_value=Opener(b"not a movie")):
                with self.assertRaises(safe.ToolError):
                    safe._download_video("https://vidgen.x.ai/m.mp4", "bad")
                self.assertFalse((out / "bad.mp4").exists())

    def test_disables_default_redirects_and_proxies(self):
        obj = safe._opener()
        handlers = list(obj.handlers)
        self.assertTrue(any(type(h) is safe.NoRedirect for h in handlers))
        self.assertFalse(any(type(h).__name__ == "ProxyHandler" for h in handlers))

    def test_api_failure_never_returns_error_body_or_token(self):
        from urllib.error import HTTPError
        request = "https://api.x.ai/v1/images/generations"
        class Opener:
            def open(self, req, timeout):
                raise HTTPError(request, 401, "SECRET token is bad", {}, None)
        with patch.dict(os.environ, {"XAI_API_KEY": "super-secret"}):
            with patch.object(safe, "_opener", return_value=Opener()):
                with self.assertRaises(safe.ToolError) as exc:
                    safe._json_api("POST", "/v1/images/generations", {})
                self.assertNotIn("super-secret", str(exc.exception))
                self.assertNotIn("SECRET token", str(exc.exception))

    def test_tool_errors_are_not_protocol_exceptions(self):
        req = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
               "params": {"name": "generate_image", "arguments": {"prompt": "hello"}}}
        with patch.dict(os.environ, {"GROK_ALLOW_PAID_REQUESTS": "NO", "GROK_OUTPUT_DIR": "/nonexistent"}):
            reply = safe.handle(req)
            self.assertTrue(reply["result"]["isError"])
        self.assertEqual(safe.handle({"jsonrpc": "2.0", "id": 5, "method": "invalid"})["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
