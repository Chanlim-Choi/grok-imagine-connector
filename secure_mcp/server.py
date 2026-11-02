#!/usr/bin/env python3
"""Small, dependency-free, stdio MCP gateway to xAI Imagine.

Security boundary: no listening socket, browser extensions, npm packages,
subprocesses, arbitrary URLs, arbitrary paths, or dynamic remote configuration.
All paid generation requires an explicitly enabled local environment flag.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import stat
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

API_ROOT = "https://api.x.ai"
IMAGE_MODEL = "grok-imagine-image-2.0"
VIDEO_MODELS = ("grok-imagine-video-1.5-lite", "grok-imagine-video-1.5")
RATIOS = ("auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9")
VIDEO_RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9", "5:2")
MAX_FRAME = 1024 * 1024
MAX_JSON = 32 * 1024 * 1024
MAX_IMAGE = 16 * 1024 * 1024
MAX_VIDEO = 200 * 1024 * 1024
MAX_REFERENCE = 10 * 1024 * 1024
MAX_JOBS = 5
SAFE_STEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
JOBS = 0


class ToolError(Exception):
    """User-facing, sanitized error (must not include secrets or remote bodies)."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener():
    # Do not silently send the API token to a proxy inherited from the shell.
    return build_opener(ProxyHandler({}), NoRedirect())


def _json_api(method: str, endpoint: str, payload: dict | None = None) -> dict:
    # endpoint is formed only by fixed literals or a validated UUID.
    if not endpoint.startswith("/v1/") or "?" in endpoint or "#" in endpoint:
        raise ToolError("Invalid internal API route")
    key = os.environ.get("XAI_API_KEY", "")
    if not key or "\r" in key or "\n" in key:
        raise ToolError("XAI_API_KEY is missing or invalid")
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        API_ROOT + endpoint,
        data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json"},
        method=method,
    )
    try:
        with _opener().open(request, timeout=75) as response:
            encoded = response.read(MAX_JSON + 1)
    except HTTPError as exc:
        raise ToolError("xAI API request failed (HTTP %d)" % exc.code) from None
    except (URLError, OSError, TimeoutError):
        raise ToolError("xAI API connection failed") from None
    if len(encoded) > MAX_JSON:
        raise ToolError("xAI response exceeded the size limit")
    try:
        result = json.loads(encoded)
    except (UnicodeDecodeError, ValueError):
        raise ToolError("xAI returned an invalid JSON response") from None
    if not isinstance(result, dict):
        raise ToolError("xAI returned an unexpected response")
    return result


def _choice(value, allowed: tuple[str, ...], name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ToolError(name + " must be one of: " + ", ".join(allowed))
    return value


def _prompt(value) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 3000 or "\x00" in value:
        raise ToolError("prompt must contain 1 to 3000 characters")
    return value.strip()


def _stem(value, prefix: str) -> str:
    if value is None:
        value = prefix + "-" + uuid.uuid4().hex[:12]
    if not isinstance(value, str) or not SAFE_STEM.fullmatch(value) or ".." in value:
        raise ToolError("name must be a simple filename stem (no extension or directories)")
    return value


def _root(env: str) -> Path:
    raw = os.environ.get(env, "")
    if not raw:
        raise ToolError(env + " must specify an existing, trusted directory")
    path = Path(raw).expanduser()
    if path.is_symlink() or not path.is_dir():
        raise ToolError(env + " must specify an existing non-symlink directory")
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ToolError(env + " cannot be resolved") from None


def _open_new(name: str, suffix: str):
    root = _root("GROK_OUTPUT_DIR")
    target = root / (name + suffix)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags, 0o600)
    except FileExistsError:
        raise ToolError("output file already exists; select another name") from None
    except OSError:
        raise ToolError("cannot create output file in the configured directory") from None
    return target, os.fdopen(fd, "wb")


def _save_bytes(data: bytes, stem: str, suffix: str) -> str:
    target, stream = _open_new(stem, suffix)
    try:
        with stream:
            stream.write(data)
    except OSError:
        target.unlink(missing_ok=True)
        raise ToolError("failed to save output") from None
    return str(target)


def _image_type(data: bytes):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise ToolError("unsupported or malformed image format")


def _read_image(name: str) -> str:
    # MCP callers can name one pre-staged file, not supply a filesystem path.
    if not isinstance(name, str) or not SAFE_STEM.fullmatch(name) or ".." in name:
        raise ToolError("image_name must be a basename in GROK_INPUT_DIR")
    if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        raise ToolError("reference image must be PNG, JPEG or WebP")
    path = _root("GROK_INPUT_DIR") / name
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        if path.is_symlink():
            raise ToolError("reference image cannot be a symlink")
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as source:
            meta = os.fstat(source.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_size > MAX_REFERENCE:
                raise ToolError("reference image must be a regular file under 10 MiB")
            data = source.read(MAX_REFERENCE + 1)
    except ToolError:
        raise
    except OSError:
        raise ToolError("reference image is not readable") from None
    if len(data) > MAX_REFERENCE:
        raise ToolError("reference image exceeds 10 MiB")
    mime, _ = _image_type(data)
    return "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")


def _image_response(result: dict, name: str) -> dict:
    try:
        encoded = result["data"][0]["b64_json"]
        if not isinstance(encoded, str) or len(encoded) > (MAX_IMAGE * 4 // 3 + 8):
            raise ValueError()
        data = base64.b64decode(encoded, validate=True)
    except (KeyError, IndexError, TypeError, ValueError, binascii.Error):
        raise ToolError("xAI returned no valid base64 image") from None
    if not data or len(data) > MAX_IMAGE:
        raise ToolError("image exceeds size limit")
    _, suffix = _image_type(data)
    return {"file": _save_bytes(data, name, suffix), "bytes": len(data)}


def _paid_job():
    global JOBS
    if os.environ.get("GROK_ALLOW_PAID_REQUESTS") != "YES":
        raise ToolError("Paid generation disabled; set GROK_ALLOW_PAID_REQUESTS=YES explicitly")
    if JOBS >= MAX_JOBS:
        raise ToolError("Per-process generation limit reached; restart only after reviewing costs")
    # Count before dispatch: failures and timeouts can still be billable.
    JOBS += 1


def generate_image(args: dict) -> dict:
    _only(args, {"prompt", "name", "aspect_ratio", "resolution", "quality"})
    prompt = _prompt(args.get("prompt"))
    name = _stem(args.get("name"), "image")
    aspect = _choice(args.get("aspect_ratio", "auto"), RATIOS, "aspect_ratio")
    resolution = _choice(args.get("resolution", "1k"), ("1k", "2k"), "resolution")
    quality = _choice(args.get("quality", "low"), ("low", "medium"), "quality")
    _root("GROK_OUTPUT_DIR")
    _paid_job()
    result = _json_api("POST", "/v1/images/generations", {"model": IMAGE_MODEL, "prompt": prompt, "n": 1,
                 "aspect_ratio": aspect, "resolution": resolution, "quality": quality, "response_format": "b64_json"})
    return _image_response(result, name)


def edit_image(args: dict) -> dict:
    _only(args, {"prompt", "image_name", "name", "aspect_ratio", "resolution", "quality"})
    prompt = _prompt(args.get("prompt"))
    name = _stem(args.get("name"), "edit")
    aspect = _choice(args.get("aspect_ratio", "auto"), RATIOS, "aspect_ratio")
    resolution = _choice(args.get("resolution", "1k"), ("1k", "2k"), "resolution")
    quality = _choice(args.get("quality", "medium"), ("low", "medium"), "quality")
    reference = _read_image(args.get("image_name"))
    _root("GROK_OUTPUT_DIR")
    _paid_job()
    result = _json_api("POST", "/v1/images/edits", {"model": IMAGE_MODEL, "prompt": prompt,
                   "image": {"url": reference, "type": "image_url"}, "aspect_ratio": aspect,
                   "resolution": resolution, "quality": quality, "response_format": "b64_json"})
    return _image_response(result, name)


def _uuid(value) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise ToolError("request_id must be a UUID")
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise ToolError("request_id must be a UUID") from None
    if parsed != value.lower():
        raise ToolError("request_id must be canonical UUID format")
    return parsed


def start_video(args: dict) -> dict:
    _only(args, {"prompt", "model", "image_name", "duration", "aspect_ratio", "resolution", "generate_audio"})
    prompt = _prompt(args.get("prompt"))
    model = _choice(args.get("model", VIDEO_MODELS[0]), VIDEO_MODELS, "model")
    ratio = _choice(args.get("aspect_ratio", "16:9"), VIDEO_RATIOS, "aspect_ratio")
    resolution = _choice(args.get("resolution", "720p"), ("480p", "720p", "1080p"), "resolution")
    duration = args.get("duration", 6)
    if type(duration) is not int or not 1 <= duration <= 15:
        raise ToolError("duration must be an integer between 1 and 15 seconds")
    audio = args.get("generate_audio", True)
    if type(audio) is not bool:
        raise ToolError("generate_audio must be boolean")
    body = {"model": model, "prompt": prompt, "duration": duration, "aspect_ratio": ratio,
            "resolution": resolution, "generate_audio": audio}
    if "image_name" in args:
        body["image"] = {"url": _read_image(args["image_name"])}
        body.pop("aspect_ratio")  # image-to-video inherits input aspect ratio
    _root("GROK_OUTPUT_DIR")
    _paid_job()
    result = _json_api("POST", "/v1/videos/generations", body)
    return {"request_id": _uuid(result.get("request_id")), "status": "submitted"}


def _video_status(request_id: str) -> dict:
    result = _json_api("GET", "/v1/videos/" + _uuid(request_id))
    status = result.get("status")
    if status not in ("pending", "done", "failed"):
        raise ToolError("unexpected video job status")
    return result


def get_video(args: dict) -> dict:
    _only(args, {"request_id"})
    request_id = _uuid(args.get("request_id"))
    result = _video_status(request_id)
    answer = {"request_id": request_id, "status": result["status"]}
    if result["status"] == "done":
        video = result.get("video")
        if not isinstance(video, dict):
            raise ToolError("completed video has no metadata")
        answer["duration"] = video.get("duration")
        answer["download_ready"] = True
    return answer


def _media_url(url):
    if not isinstance(url, str) or len(url) > 4096 or "\\" in url or any(ord(x) < 32 for x in url):
        raise ToolError("video URL is invalid")
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname != "vidgen.x.ai" or
                parts.port not in (None, 443) or parts.username or parts.password or
                parts.fragment or not parts.path.startswith("/")):
            raise ValueError()
    except ValueError:
        raise ToolError("video URL host is not on the approved xAI allowlist") from None
    return url


def _download_video(url: str, name: str) -> dict:
    url = _media_url(url)
    target, stream = _open_new(name, ".mp4")
    count = 0
    head = b""
    try:
        request = Request(url, headers={"Accept": "video/mp4,application/octet-stream"}, method="GET")
        with stream:
            with _opener().open(request, timeout=90) as response:
                header = response.headers.get("Content-Length")
                if header is not None and header.isdecimal() and int(header) > MAX_VIDEO:
                    raise ToolError("video exceeds 200 MiB limit")
                while True:
                    part = response.read(65536)
                    if not part:
                        break
                    count += len(part)
                    if count > MAX_VIDEO:
                        raise ToolError("video exceeds 200 MiB limit")
                    if len(head) < 16:
                        head = (head + part)[:16]
                    stream.write(part)
        if len(head) < 12 or head[4:8] != b"ftyp":
            raise ToolError("download was not an MP4 video")
    except (HTTPError, URLError, OSError, TimeoutError):
        target.unlink(missing_ok=True)
        raise ToolError("video download failed or redirect was rejected") from None
    except ToolError:
        target.unlink(missing_ok=True)
        raise
    return {"file": str(target), "bytes": count}


def save_video(args: dict) -> dict:
    _only(args, {"request_id", "name"})
    request_id = _uuid(args.get("request_id"))
    name = _stem(args.get("name"), "video")
    _root("GROK_OUTPUT_DIR")
    status = _video_status(request_id)
    if status["status"] != "done":
        return {"request_id": request_id, "status": status["status"], "saved": False}
    video = status.get("video")
    if not isinstance(video, dict):
        raise ToolError("video URL unavailable")
    return _download_video(video.get("url"), name)


def _only(args, keys: set[str]):
    if not isinstance(args, dict) or set(args) - keys:
        raise ToolError("unsupported tool arguments")


TOOLS = [
    {"name": "generate_image", "description": "PAID: generate one image with xAI and save within GROK_OUTPUT_DIR. Never overwrites files.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["prompt"],
         "properties": {"prompt": {"type": "string", "maxLength": 3000}, "name": {"type": "string"},
             "aspect_ratio": {"type": "string", "enum": list(RATIOS)},
             "resolution": {"type": "string", "enum": ["1k", "2k"]},
             "quality": {"type": "string", "enum": ["low", "medium"]}}}},
    {"name": "edit_image", "description": "PAID: edit one staged image (basename only, GROK_INPUT_DIR) and save the result.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["prompt", "image_name"],
         "properties": {"prompt": {"type": "string", "maxLength": 3000}, "image_name": {"type": "string"},
             "name": {"type": "string"}, "aspect_ratio": {"type": "string", "enum": list(RATIOS)},
             "resolution": {"type": "string", "enum": ["1k", "2k"]},
             "quality": {"type": "string", "enum": ["low", "medium"]}}}},
    {"name": "start_video", "description": "PAID: submit text-to-video or image-to-video job. Returns UUID; poll with get_video.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["prompt"],
         "properties": {"prompt": {"type": "string", "maxLength": 3000},
             "model": {"type": "string", "enum": list(VIDEO_MODELS)},
             "image_name": {"type": "string"}, "duration": {"type": "integer", "minimum": 1, "maximum": 15},
             "aspect_ratio": {"type": "string", "enum": list(VIDEO_RATIOS)},
             "resolution": {"type": "string", "enum": ["480p", "720p", "1080p"]},
             "generate_audio": {"type": "boolean"}}}},
    {"name": "get_video", "description": "Read-only: fetch video generation job status by UUID.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["request_id"],
         "properties": {"request_id": {"type": "string"}}}},
    {"name": "save_video", "description": "Read-only job check plus download of generated video from verified vidgen.x.ai URL; saves only within GROK_OUTPUT_DIR.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["request_id"],
         "properties": {"request_id": {"type": "string"}, "name": {"type": "string"}}}}
]
CALLS = {"generate_image": generate_image, "edit_image": edit_image, "start_video": start_video,
         "get_video": get_video, "save_video": save_video}
INSTRUCTIONS = ("Paid xAI image/video generation only; never use the historical Chrome extension or grok-auto CLI. "
                "Only the configured staging and output folders are accessible. Do not include secrets or sensitive "
                "company data in prompts; prompts and reference images go to xAI. Submit one generation only when requested. "
                "Limit: five attempted generations per server process. Video: start_video, get_video, save_video.")


def handle(request):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    method = request["method"]
    if "id" not in request:
        return None  # notifications never receive JSON-RPC responses
    rid = request["id"]
    params = request.get("params") or {}
    try:
        if not isinstance(params, dict):
            raise ToolError("params must be an object")
        if method == "initialize":
            wanted = params.get("protocolVersion")
            protocol = wanted if wanted in ("2025-06-18", "2025-03-26", "2024-11-05") else "2025-06-18"
            result = {"protocolVersion": protocol, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "grok-imagine-secure", "version": "0.1.0"},
                      "instructions": INSTRUCTIONS}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            if name not in CALLS:
                raise ToolError("unknown tool")
            args = params.get("arguments", {})
            if not isinstance(args, dict):
                raise ToolError("arguments must be an object")
            try:
                value = CALLS[name](args)
                result = {"content": [{"type": "text", "text": json.dumps(value)}]}
            except ToolError as exc:
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}
    except ToolError as exc:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": str(exc)}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main():
    while True:
        line = sys.stdin.buffer.readline(MAX_FRAME + 1)
        if not line:
            return
        if len(line) > MAX_FRAME:
            # Flush remaining bytes of this oversized JSON line (bounded reads).
            while line and not line.endswith(b"\n"):
                line = sys.stdin.buffer.readline(MAX_FRAME + 1)
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Message exceeds limit"}}
        else:
            try:
                req = json.loads(line)
                reply = handle(req)
            except (ValueError, UnicodeDecodeError):
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            except Exception:
                # Never leak a traceback, request payload, or API token on stdout.
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "Internal error"}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=True, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
