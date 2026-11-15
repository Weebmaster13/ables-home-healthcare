"""Assemble the hero loop on the hosting server (needs ffmpeg on PATH).

Takes the generated segments in static/img/care/segments/ and writes the
final looping clip into static/img/care/:

    hero-family-room-loop.mp4
    hero-family-room-loop.webm
    hero-family-room-loop-poster.jpg

Recipe (matches the approved look):
  * every segment plays FORWARD at real speed
  * each pass gets its own framing (crop/zoom) so a repeated take never
    reads as the same action
  * soft cross-fades between segments
  * the clip returns to its opening only in the final dissolve

Shared hosting limits how many processes ffmpeg may open, so the cross-fade
chain is built in small groups instead of one big command.
"""

import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
CARE = os.path.join(BASE, "static", "img", "care")
SEG_DIR = os.path.join(CARE, "segments")
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")

WIDTH = 960
FPS = 24
FADE = 0.5
TAIL = 1.3
GROUP = 2          # max inputs open in one ffmpeg command
MIN_SECONDS = 9.0  # never replace the live clip with something shorter

# (segment file, crop zoom, horizontal bias -1..1, vertical bias -1..1)
# Each shot is framed differently so the clip reads as a sequence of distinct
# moments rather than one action repeating.
PLAN = [
    ("seg_1.mp4", 1.00, 0.00, 0.00),
    ("seg_2.mp4", 1.28, -0.55, 0.00),
    ("seg_3.mp4", 1.26, 0.55, 0.00),
    ("seg_4.mp4", 1.14, -0.30, 0.10),
    ("seg_5.mp4", 1.00, 0.00, 0.00),
    ("seg_6.mp4", 1.30, 0.60, -0.05),
    ("seg_7.mp4", 1.18, 0.35, 0.05),
    ("seg_1.mp4", 1.22, 0.00, -0.05),
    ("seg_3.mp4", 1.02, 0.00, 0.00),
]


def run(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "")[-1200:])
    return result


def probe(path):
    out = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True).stderr
    width, height = 1280, 720
    for line in out.splitlines():
        if " Video:" in line:
            for part in line.split(","):
                token = part.strip().split(" ")[0]
                if "x" in token and token.split("x")[0].isdigit():
                    width, height = (int(v) for v in token.split("x"))
    return width, height


def duration(path):
    out = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True).stderr
    for line in out.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


def merge_group(paths, out):
    """Cross-fade 2 or 3 clips into one file."""
    inputs = []
    for path in paths:
        inputs += ["-threads", "1", "-i", path]
    chains = []
    prev = "v0"
    total = duration(paths[0])
    for i in range(1, len(paths)):
        offset = total - FADE
        label = "x%d" % i
        chains.append("[%s][v%d]xfade=transition=fade:duration=%s:offset=%.3f[%s]"
                      % (prev, i, FADE, offset, label))
        prev = label
        total += duration(paths[i]) - FADE
    # normalise every input so ffmpeg never has to insert an auto-scaler
    pre = []
    for i in range(len(paths)):
        pre.append("[%d:v]format=yuv420p,setsar=1,fps=%d,settb=1/%d[v%d]"
                   % (i, FPS, FPS, i))
    run([FFMPEG, "-y"] + inputs + ["-filter_complex", ";".join(pre + chains),
         "-map", "[%s]" % prev, "-an", "-threads", "1", "-filter_threads", "1",
         "-filter_complex_threads", "1", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", "-pix_fmt", "yuv420p", out])
    return out


def merge_paths(paths, out):
    """Cross-fade a list of clips into one, in small groups."""
    current = list(paths)
    round_index = 0
    while len(current) > 1:
        nxt = []
        index = 0
        while index < len(current):
            chunk = current[index:index + GROUP]
            if len(chunk) == 1:
                nxt.append(chunk[0])
                index += 1
                continue
            temp = "%s.r%d_%d.mp4" % (out, round_index, index)
            merge_group(chunk, temp)
            nxt.append(temp)
            index += GROUP
        current = nxt
        round_index += 1
    if os.path.abspath(current[0]) != os.path.abspath(out):
        shutil.copy(current[0], out)
    return out


def main():
    segments = [(os.path.join(SEG_DIR, name), zoom, bx, by) for name, zoom, bx, by in PLAN]
    segments = [item for item in segments if os.path.exists(item[0])]
    if len(segments) < 3:
        print("not enough segments yet: %d" % len(segments))
        return 1

    tmp_dir = os.path.join(SEG_DIR, "_work")
    os.makedirs(tmp_dir, exist_ok=True)
    for stale in os.listdir(tmp_dir):
        try:
            os.remove(os.path.join(tmp_dir, stale))
        except OSError:
            pass
    prepared = []
    for index, (path, zoom, bx, by) in enumerate(segments):
        src_w, src_h = probe(path)
        out_h = int(round(WIDTH * src_h / float(src_w))) // 2 * 2
        crop_w = int(src_w / zoom) // 2 * 2
        crop_h = int(src_h / zoom) // 2 * 2
        x = int((src_w - crop_w) / 2 + bx * (src_w - crop_w) / 2)
        x = max(0, min(src_w - crop_w, x))
        y = int((src_h - crop_h) / 2 + by * (src_h - crop_h) / 2)
        y = max(0, min(src_h - crop_h, y))
        out = os.path.join(tmp_dir, "p%02d.mp4" % index)
        run([FFMPEG, "-y", "-threads", "1", "-i", path,
             "-vf", "crop=%d:%d:%d:%d,scale=%d:%d,setsar=1,fps=%d" % (crop_w, crop_h, x, y, WIDTH, out_h, FPS),
             "-an", "-threads", "2", "-filter_threads", "1", "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", out])
        prepared.append(out)

    merged = merge_paths(prepared, os.path.join(tmp_dir, "merged.mp4"))
    total = duration(merged)
    if total < MIN_SECONDS:
        print("merged clip is only %.2fs - keeping the current one" % total)
        return 1

    tail = min(TAIL, max(total / 3.0, 0.4))
    graph = ("[0:v]trim=0:%.3f,setpts=PTS-STARTPTS[body];"
             "[0:v]trim=%.3f:%.3f,setpts=PTS-STARTPTS[tailv];"
             "[0:v]trim=0:%.3f,setpts=PTS-STARTPTS[headv];"
             "[tailv][headv]xfade=transition=fade:duration=%.3f:offset=0[blend];"
             "[body][blend]concat=n=2:v=1[v]") % (total - tail, total - tail, total, tail, tail)
    staged = os.path.join(tmp_dir, "final.mp4")
    run([FFMPEG, "-y", "-threads", "1", "-i", merged, "-filter_complex", graph, "-map", "[v]",
         "-an", "-threads", "2", "-filter_threads", "1", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", staged])

    final = os.path.join(CARE, "hero-family-room-loop.mp4")
    run([FFMPEG, "-y", "-threads", "1", "-i", staged, "-an", "-threads", "2",
         "-filter_threads", "1", "-c:v", "libx264", "-preset", "slow", "-crf", "27",
         "-profile:v", "main", "-level", "3.1", "-pix_fmt", "yuv420p", "-movflags", "+faststart", final])

    webm = os.path.join(CARE, "hero-family-room-loop.webm")
    run([FFMPEG, "-y", "-threads", "1", "-i", final, "-an", "-threads", "2",
         "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "37",
         "-row-mt", "1", "-deadline", "good", "-cpu-used", "3", webm])

    poster = os.path.join(CARE, "hero-family-room-loop-poster.jpg")
    run([FFMPEG, "-y", "-threads", "1", "-i", final, "-frames:v", "1", "-q:v", "5", poster])

    print("assembled %d segments -> %s (%.2fs)" % (len(prepared), final, duration(final)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
