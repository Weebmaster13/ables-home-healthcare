"""Cron-driven hero-video pipeline (runs on the hosting server).

Steps on every run:
  1. make sure ffmpeg is available (downloads a static build if needed)
  2. generate the next missing segment into static/img/care/segments/
  3. once enough segments exist, assemble the final looping clip

The generation service meters GPU time per IP per day, so the job reads the
quota error, works out how many sampling steps still fit, and uses whatever
budget is left instead of failing.  A short public status file is written to
static/img/care/gen_status.json so progress can be checked over HTTP.
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import traceback
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import video_gen  # noqa: E402
import video_assemble  # noqa: E402

CARE = os.path.join(BASE, "static", "img", "care")
SEG_DIR = os.path.join(CARE, "segments")
LOG_DIR = os.path.join(BASE, "instance", "logs")
LOG_PATH = os.path.join(LOG_DIR, "gen.log")
STATUS_PATH = os.path.join(CARE, "gen_status.json")
SRC = os.path.join(CARE, "hero-family-room-1280.jpg")
FFMPEG_LOCAL = os.path.join(BASE, "tools", "ffmpeg")

SCENE = (
    "Three people sitting together on a couch in a warm sunlit living room. "
    "The caregiver in navy blue scrubs on the left, the older woman in the mint green cardigan in the middle, "
    "the man in the dark green sweater on the right. "
    "Warm natural window light, realistic documentary look, locked static camera, fixed framing, no camera movement."
)

ACTIONS = [
    ("The older woman in the mint green cardigan speaks with natural lip movement and gentle hand gestures "
     "while the caregiver listens and nods and the man watches attentively.", 9101),
    ("The caregiver in navy blue scrubs speaks calmly, leaning slightly forward, while the older woman smiles "
     "warmly and listens and the man shifts on the couch.", 9102),
    ("The man in the dark green sweater talks animatedly with small natural hand gestures while the older woman "
     "smiles warmly at him and the caregiver listens attentively.", 9103),
    ("The three of them laugh gently together, the older woman smiles broadly, the caregiver smiles and nods, "
     "and the man leans back with a grin.", 9104),
    ("The caregiver explains something with a gentle hand gesture, the older woman listens with a warm expression, "
     "and the man looks down briefly then back up.", 9105),
]

DURATION = 2.0
BEST_STEPS = 12
MIN_STEPS = 3
QUALITY = 6
FPS = 16
COST_PER_UNIT = 5.6

FFMPEG_URLS = [
    "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz",
]


def log(message):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except Exception:
        pass


def write_status(**fields):
    try:
        fields["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(STATUS_PATH, "w", encoding="utf-8") as handle:
            json.dump(fields, handle, indent=1)
    except Exception:
        pass


def ensure_ffmpeg():
    """Return a usable ffmpeg path, downloading a static build if needed."""
    system = shutil.which("ffmpeg")
    if system:
        return system
    if os.path.exists(FFMPEG_LOCAL):
        return FFMPEG_LOCAL
    for url in FFMPEG_URLS:
        try:
            log("downloading ffmpeg from %s" % url)
            with urllib.request.urlopen(url, timeout=900) as response:
                blob = response.read()
            archive = tarfile.open(fileobj=io.BytesIO(blob), mode="r:xz")
            for member in archive.getmembers():
                if member.name.endswith("ffmpeg") and member.isfile():
                    payload = archive.extractfile(member)
                    os.makedirs(os.path.dirname(FFMPEG_LOCAL), exist_ok=True)
                    with open(FFMPEG_LOCAL, "wb") as handle:
                        handle.write(payload.read())
                    os.chmod(FFMPEG_LOCAL, 0o755)
                    log("ffmpeg installed (%d bytes)" % os.path.getsize(FFMPEG_LOCAL))
                    return FFMPEG_LOCAL
        except Exception as exc:
            log("ffmpeg download failed from %s: %s" % (url, str(exc)[:200]))
    return None


def _generate(prompt, seed, destination, steps):
    job = video_gen.start_generation(
        SRC, prompt, duration=DURATION, steps=steps, quality=QUALITY, fps=FPS, seed=seed,
    )
    return video_gen.collect_generation(job, destination, timeout=1200)


def _quota_left(result):
    error = (result or {}).get("error") or ""
    match = re.search(r"vs\.\s*(\d+)s left", error)
    return int(match.group(1)) if match else None


def _run():
    os.makedirs(SEG_DIR, exist_ok=True)
    ffmpeg = ensure_ffmpeg()
    write_status(ffmpeg=bool(ffmpeg), segments=0)

    ready = []
    for index, (action, seed) in enumerate(ACTIONS, start=1):
        destination = os.path.join(SEG_DIR, "seg_%d.mp4" % index)
        if os.path.exists(destination) and os.path.getsize(destination) > 20000:
            ready.append(index)
            continue

        prompt = SCENE + " " + action
        result = _generate(prompt, seed, destination, BEST_STEPS)
        if result.get("status") != "done":
            left = _quota_left(result)
            if left is None:
                log("segment %d failed: %s" % (index, json.dumps(result)[:400]))
                continue
            affordable = int(left / (DURATION * COST_PER_UNIT))
            log("segment %d: quota left %ds -> affordable steps %d" % (index, left, affordable))
            if affordable < MIN_STEPS:
                log("waiting for more quota (%ds left)" % left)
                break
            result = _generate(prompt, seed, destination, min(BEST_STEPS, affordable))
        log("segment %d: %s" % (index, json.dumps(result)[:300]))
        if result.get("status") == "done":
            ready.append(index)

    write_status(ffmpeg=bool(ffmpeg), segments=len(ready),
                 segment_ids=ready, assembled=False)

    on_disk = sorted(name for name in os.listdir(SEG_DIR)
                     if name.startswith("seg_") and name.endswith(".mp4"))
    if ffmpeg and len(on_disk) >= 3:
        os.environ["FFMPEG_BIN"] = ffmpeg
        try:
            video_assemble.FFMPEG = ffmpeg
            result = video_assemble.main()
            write_status(ffmpeg=True, segments=len(on_disk),
                         segment_ids=on_disk, assembled=result == 0)
            log("assembly finished (result %s)" % result)
        except Exception:
            log("assembly failed:\n%s" % traceback.format_exc()[-800:])


def main():
    lock = os.path.join(CARE, ".gen.lock")
    if os.path.exists(lock):
        return
    try:
        with open(lock, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except Exception:
        pass
    log("=== job start (pid %s) ===" % os.getpid())
    try:
        _run()
    except Exception:
        log("job error:\n%s" % traceback.format_exc()[-800:])
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass
    log("=== job done ===")


if __name__ == "__main__":
    main()
