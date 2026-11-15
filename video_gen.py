"""Server-side image-to-video generation (Python standard library only).

The hosting server has its own outbound IP address, which means it has its
own generation quota - the workstation that authored this file may already be
rate limited.

This module speaks the Gradio HTTP API directly (upload -> queue/join ->
queue/data SSE) so it runs inside the cPanel virtualenv without installing
any extra packages.

Public helpers:
    start_generation(image_path, prompt, ...)  -> job dict
    collect_generation(job, dest_path)        -> result dict
"""

import json
import mimetypes
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

SPACE_URL = "https://vml12-wan-video-studio.hf.space"
API_NAME = "generate_video"
LTX_URL = "https://deeprat-ltx-video-zerogpu-optimized.hf.space"
LTX_API = "image_to_video"
SSL_CTX = ssl.create_default_context()
USER_AGENT = "ables-video-tool"


def _urlopen(request, timeout):
    return urllib.request.urlopen(request, timeout=timeout, context=SSL_CTX)


def _use_ipv6():
    """Prefer IPv6 for outbound connections (separate quota bucket at the
    generation service when the host has a second address)."""
    import socket

    original = socket.getaddrinfo

    def gai(host, port, family=0, type=0, proto=0, flags=0):
        try:
            return original(host, port, socket.AF_INET6, type, proto, flags)
        except socket.gaierror:
            return original(host, port, family, type, proto, flags)

    socket.getaddrinfo = gai


def _outbound_ip():
    import socket

    try:
        with socket.create_connection(("api.ipify.org", 443), timeout=15) as sock:
            return socket.gethostbyname(socket.getfqdn()) or "?"
    except Exception:
        return "?"


def _get(url, timeout=60):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _urlopen(request, timeout) as response:
        return response.read()


def _get_json(url, timeout=60):
    return json.loads(_get(url, timeout).decode("utf-8"))


def _post_json(url, payload, headers=None, timeout=120):
    body = json.dumps(payload).encode("utf-8")
    all_headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    all_headers.update(headers or {})
    request = urllib.request.Request(url, data=body, method="POST", headers=all_headers)
    with _urlopen(request, timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _upload_file(url, file_path):
    """Upload a file to the Gradio upload endpoint and return the remote path."""
    filename = os.path.basename(file_path)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with open(file_path, "rb") as handle:
        payload = handle.read()
    boundary = "----ables" + uuid.uuid4().hex
    body = b"".join([
        ("--%s\r\n" % boundary).encode(),
        ('Content-Disposition: form-data; name="files"; filename="%s"\r\n' % filename).encode(),
        ("Content-Type: %s\r\n\r\n" % content_type).encode(),
        payload,
        ("\r\n--%s--\r\n" % boundary).encode(),
    ])
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary,
                 "User-Agent": USER_AGENT},
    )
    with _urlopen(request, 180) as response:
        result = json.loads(response.read().decode("utf-8"))
    if isinstance(result, dict):
        result = result.get("files") or result.get("data") or []
    return result[0]


def _space_info(base_url=None, api_name=None):
    config = _get_json((base_url or SPACE_URL) + "/config")
    prefix = config.get("api_prefix") or "/gradio_api"
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    wanted = (api_name or API_NAME).lstrip("/")
    fn_index = None
    for index, dependency in enumerate(config.get("dependencies", [])):
        name = (dependency.get("api_name") or "").lstrip("/")
        if name == wanted and dependency.get("backend_fn"):
            fn_index = index
            break
    if fn_index is None:
        raise RuntimeError("endpoint %s not found" % wanted)
    return prefix.rstrip("/"), fn_index


def start_ltx_generation(image_path, prompt,
                         negative_prompt="text, watermark, cartoon, blurry, warping, morphing, camera movement",
                         duration=6.0, frames=145, width=704, height=416, seed=20261112, guidance=3.0):
    """Queue a generation on the LTX image-to-video space (one long take)."""
    prefix, fn_index = _space_info(LTX_URL, LTX_API)
    uploaded = _upload_file(LTX_URL + prefix + "/upload", image_path)
    data = [
        prompt,
        negative_prompt,
        {"path": uploaded, "meta": {"_type": "gradio.FileData"}},
        height,
        width,
        "image-to-video",
        duration,
        frames,
        seed,
        False,
        guidance,
        True,
        False,
    ]
    session_hash = str(uuid.uuid4())
    joined = _post_json(
        LTX_URL + prefix + "/queue/join",
        {"data": data, "fn_index": fn_index, "session_hash": session_hash},
        headers={"x-gradio-user": "api"},
    )
    return {"event_id": joined.get("event_id"), "session_hash": session_hash,
            "prefix": prefix, "fn_index": fn_index, "space": "ltx"}


def collect_ltx_generation(job, dest_path=None, timeout=900):
    """Collect a job started with start_ltx_generation()."""
    return _collect(job, dest_path, timeout, base_url=LTX_URL)


def start_generation(image_path, prompt,
                     negative_prompt=("text, watermark, cartoon, blurry, low quality, warping, morphing, "
                                      "distorted faces, distorted hands, extra limbs, camera movement, zoom, pan"),
                     duration=2.0, steps=12, quality=6, fps=16, seed=20261112,
                     guidance=5.0, guidance2=3.5):
    """Upload the source frame and queue a generation. Returns a job dict."""
    prefix, fn_index = _space_info()
    uploaded = _upload_file(SPACE_URL + prefix + "/upload", image_path)

    data = [
        {"path": uploaded, "meta": {"_type": "gradio.FileData"}},   # input_image
        None,                                                        # last_image
        prompt,
        steps,
        negative_prompt,
        duration,
        guidance,
        guidance2,
        seed,
        False,                                                       # randomize_seed
        quality,
        "FlowMatchEulerDiscrete",
        5.0,                                                         # flow_shift
        fps,
        True,                                                        # safe_mode
        ["(None)"],                                                  # lora_groups
        False,                                                       # video_component
        False,                                                       # add_soundtrack
        None,                                                        # voice_audio
        "",                                                          # audio_prompt
    ]

    session_hash = str(uuid.uuid4())
    joined = _post_json(
        SPACE_URL + prefix + "/queue/join",
        {"data": data, "fn_index": fn_index, "session_hash": session_hash},
        headers={"x-gradio-user": "api"},
    )
    return {
        "event_id": joined.get("event_id"),
        "session_hash": session_hash,
        "prefix": prefix,
        "fn_index": fn_index,
    }


def _extract_video(output):
    """The space returns [status, {video: {...}}, seed]; find the video anywhere."""
    if not isinstance(output, dict):
        return None
    data = output.get("data")
    if not isinstance(data, list):
        return None
    for item in data:
        if isinstance(item, dict):
            video = item.get("video")
            if isinstance(video, dict):
                path = video.get("path")
                if path:
                    return path
            elif isinstance(video, str) and video:
                return video
            if item.get("path") and str(item.get("path")).endswith(".mp4"):
                return item["path"]
    return None


def collect_generation(job, dest_path=None, timeout=900):
    """Follow the SSE stream for a queued job and save the finished video."""
    return _collect(job, dest_path, timeout, base_url=SPACE_URL)


def _collect(job, dest_path=None, timeout=900, base_url=None):
    base = base_url or SPACE_URL
    prefix = job.get("prefix") or "/gradio_api"
    session_hash = job.get("session_hash")
    if not session_hash:
        return {"status": "error", "error": "no session hash"}

    stream_url = base + prefix + "/queue/data?session_hash=" + urllib.parse.quote(session_hash)
    request = urllib.request.Request(stream_url, headers={"Accept": "*/*", "User-Agent": USER_AGENT})
    deadline = time.time() + timeout
    output = None
    failure = None

    with _urlopen(request, timeout) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk:
                continue
            try:
                message = json.loads(chunk)
            except ValueError:
                continue
            kind = message.get("msg")
            if kind == "process_completed":
                output = message.get("output")
                if message.get("success") is False:
                    failure = json.dumps(output)[:600]
                break
            if kind == "process_starts":
                continue
            if time.time() > deadline:
                break

    if failure:
        return {"status": "error", "error": failure}
    if output is None:
        return {"status": "pending", "error": "no completion event"}

    video_path = _extract_video(output)
    if not video_path:
        return {"status": "error", "error": "no video in output: " + json.dumps(output)[:400]}

    file_url = base + prefix + "/file=" + urllib.parse.quote(video_path)
    blob = _get(file_url, 300)
    if dest_path:
        folder = os.path.dirname(dest_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(dest_path, "wb") as handle:
            handle.write(blob)
    return {"status": "done", "bytes": len(blob), "saved": dest_path, "remote": video_path}
