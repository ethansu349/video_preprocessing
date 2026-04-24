#!/usr/bin/env python3
"""Interactive camera synchronization pipeline.

Guides you through finding time offsets between 3 cameras, visually
verifying alignment with side-by-side comparison images, and extracting
synchronized frame triplets.

Reference camera: iPhone (offset = 0).

Usage:
    python sync_cameras.py \
        --iphone /path/to/iPhone14_FULL.MOV \
        --jvc /path/to/JVC_Recorder_FULL.m2ts \
        --mcu /path/to/MCU_Stationary_Camera_Full.m4v \
        --iphone-frames /path/to/iPhone_Frames \
        --mcu-frames /path/to/MCU_Stationary_Camera_Frames \
        --output ./synced_frames

    # Skip auto-computation and provide offsets manually:
    python sync_cameras.py \
        --iphone ... --jvc ... --mcu ... \
        --jvc-offset 12.5 --mcu-offset -45.0 \
        --output ./synced_frames
"""

import argparse
import base64
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def get_video_duration(video_path):
    cmd = ["ffprobe", "-v", "error",
           "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1",
           str(video_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def get_video_fps(video_path):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=r_frame_rate",
           "-of", "default=noprint_wrappers=1:nokey=1",
           str(video_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    num, den = r.stdout.strip().split("/")
    return int(num) / int(den)


def extract_single_frame(video_path, timestamp, output_path):
    """Extract one frame at *timestamp* seconds from *video_path*."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{timestamp:.3f}",
         "-i", str(video_path), "-frames:v", "1", "-q:v", "2",
         str(output_path)],
        capture_output=True, check=True,
    )


def extract_frame_batch(video_path, start_time, duration, fps, output_dir):
    """Extract multiple frames from video with one ffmpeg call.

    Returns list of (frame_path, timestamp) sorted by timestamp.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # Clean old frames
    for old in output_dir.glob("frame_*.jpg"):
        old.unlink()

    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{start_time:.3f}",
         "-i", str(video_path), "-t", f"{duration:.3f}",
         "-vf", f"fps={fps}", "-q:v", "2",
         str(output_dir / "frame_%04d.jpg")],
        capture_output=True, check=True,
    )
    frames = sorted(output_dir.glob("frame_*.jpg"))
    # frame_0001.jpg = start_time, frame_0002.jpg = start_time + 1/fps, ...
    return [(f, start_time + i / fps) for i, f in enumerate(frames)]


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Alignment: {ref_label} vs {tgt_label}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #111; color: #eee; font-family: system-ui, monospace;
         display: flex; flex-direction: column; align-items: center; padding: 16px; }}
  h1 {{ margin-bottom: 8px; font-size: 20px; }}
  .offset-display {{ font-size: 48px; color: #ffe100; margin: 12px 0; }}
  .hint {{ color: #aaa; font-size: 13px; margin: 4px 0; }}
  .pair {{ display: flex; gap: 12px; justify-content: center;
           align-items: start; margin-top: 12px; flex-wrap: wrap; }}
  .cam {{ text-align: center; }}
  .cam h3 {{ margin-bottom: 4px; font-size: 14px; }}
  .cam img {{ max-width: 620px; width: 100%; border: 2px solid #333; }}
  .cam.ref h3 {{ color: #0f0; }}
  .cam.ref img {{ border-color: #0f0; }}
  .cam.tgt h3 {{ color: #ffe100; }}
  .cam.tgt img {{ border-color: #ffe100; }}
  .controls {{ margin-top: 16px; display: flex; gap: 8px; align-items: center; }}
  .controls button {{ padding: 8px 16px; font-size: 16px; cursor: pointer;
                      background: #333; color: #eee; border: 1px solid #555;
                      border-radius: 4px; }}
  .controls button:hover {{ background: #555; }}
  .bar {{ width: 600px; margin-top: 12px; }}
  input[type=range] {{ width: 100%; }}
</style>
</head>
<body>
<h1>Alignment: {ref_label} vs {tgt_label}</h1>
<div class="hint">Reference time: {ref_label} t = {ref_time:.1f}s (fixed)</div>
<div class="offset-display">offset: <span id="val">?</span>s</div>
<div class="hint" id="meaning"></div>

<div class="pair">
  <div class="cam ref">
    <h3>{ref_label} (reference)</h3>
    <img id="refimg" src="{ref_img_b64}">
  </div>
  <div class="cam tgt">
    <h3>{tgt_label} &mdash; t = <span id="tgt_t">?</span>s</h3>
    <img id="tgtimg">
  </div>
</div>

<div class="controls">
  <button onclick="step(-5)">&laquo; -5s</button>
  <button onclick="step(-1)">&lsaquo; -1s</button>
  <button onclick="step(1)">+1s &rsaquo;</button>
  <button onclick="step(5)">+5s &raquo;</button>
</div>
<div class="bar">
  <input type="range" id="slider" min="0" max="0" value="0"
         oninput="goTo(parseInt(this.value))">
</div>
<div class="hint" style="margin-top:12px">
  Keyboard: &larr;/&rarr; = &plusmn;1s &nbsp; Shift+&larr;/&rarr; = &plusmn;5s
</div>
<div class="hint" style="margin-top:4px; color:#ffe100;">
  When frames match, note the offset above and type it in the terminal.
</div>

<script>
const frames = {frames_json};
let idx = {initial_idx};

function render() {{
  const f = frames[idx];
  document.getElementById('tgtimg').src = f.b64;
  document.getElementById('val').textContent = f.offset.toFixed(1);
  document.getElementById('tgt_t').textContent = f.tgt_time.toFixed(1);
  document.getElementById('slider').value = idx;
  const dir = f.offset >= 0
    ? '{tgt_label} started ' + f.offset.toFixed(1) + 's after {ref_label}'
    : '{tgt_label} started ' + Math.abs(f.offset).toFixed(1) + 's before {ref_label}';
  document.getElementById('meaning').textContent = dir;
}}

function step(d) {{ goTo(idx + d); }}
function goTo(i) {{ idx = Math.max(0, Math.min(frames.length - 1, i)); render(); }}

document.getElementById('slider').max = frames.length - 1;
document.addEventListener('keydown', e => {{
  if (e.key === 'ArrowRight') {{ step(e.shiftKey ? 5 : 1); e.preventDefault(); }}
  if (e.key === 'ArrowLeft')  {{ step(e.shiftKey ? -5 : -1); e.preventDefault(); }}
}});
render();
</script>
</body>
</html>
"""


def _img_to_b64(path):
    """Read an image file and return a data:image/jpeg;base64,... URI."""
    data = Path(path).read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


# ---------------------------------------------------------------------------
# Offset computation — audio
# ---------------------------------------------------------------------------

def compute_audio_offset(ref_video, target_video, sample_rate=8000):
    """Cross-correlate audio from two videos.  Returns (offset_seconds, confidence)."""
    from scipy.signal import fftconvolve

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        print("  Extracting audio from reference …", end=" ", flush=True)
        ref = _extract_audio(ref_video, tmp / "ref.raw", sample_rate)
        print(f"({len(ref)/sample_rate:.0f}s)")

        print("  Extracting audio from target …", end=" ", flush=True)
        tgt = _extract_audio(target_video, tmp / "tgt.raw", sample_rate)
        print(f"({len(tgt)/sample_rate:.0f}s)")

    print("  Cross-correlating …", end=" ", flush=True)
    ref_f = (ref - np.mean(ref)).astype(np.float64)
    tgt_f = (tgt - np.mean(tgt)).astype(np.float64)
    if (s := np.std(ref_f)) > 0: ref_f /= s
    if (s := np.std(tgt_f)) > 0: tgt_f /= s

    corr = fftconvolve(ref_f, tgt_f[::-1], mode="full")
    peak = np.argmax(np.abs(corr))
    lag = peak - (len(tgt) - 1)
    offset = lag / sample_rate
    conf = np.abs(corr[peak]) / min(len(ref), len(tgt))
    print("done")
    return offset, conf


def _extract_audio(video, out, sr):
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(video),
         "-vn", "-ac", "1", "-ar", str(sr),
         "-f", "f32le", "-acodec", "pcm_f32le", str(out)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed:\n{r.stderr}")
    return np.fromfile(str(out), dtype=np.float32)


# ---------------------------------------------------------------------------
# Offset computation — brightness
# ---------------------------------------------------------------------------

def compute_brightness_offset(ref_frames_dir, tgt_frames_dir,
                              ref_fps, tgt_fps):
    """Cross-correlate mean-brightness time series.  Returns (offset_seconds, confidence)."""
    from scipy.signal import fftconvolve
    from scipy.interpolate import interp1d

    print("  Loading reference frames …", end=" ", flush=True)
    ri, rb = _brightness_curve(ref_frames_dir)
    rt = ri / ref_fps
    print(f"({len(ri)} frames, {rt[-1]:.0f}s)")

    print("  Loading target frames …", end=" ", flush=True)
    ti, tb = _brightness_curve(tgt_frames_dir)
    tt = ti / tgt_fps
    print(f"({len(ti)} frames, {tt[-1]:.0f}s)")

    # Resample to 1 Hz
    ref_sig = interp1d(rt, rb, fill_value="extrapolate")(np.arange(rt[0], rt[-1]))
    tgt_sig = interp1d(tt, tb, fill_value="extrapolate")(np.arange(tt[0], tt[-1]))

    print("  Cross-correlating …", end=" ", flush=True)
    ref_n = (ref_sig - np.mean(ref_sig)).astype(np.float64)
    tgt_n = (tgt_sig - np.mean(tgt_sig)).astype(np.float64)
    if (s := np.std(ref_n)) > 0: ref_n /= s
    if (s := np.std(tgt_n)) > 0: tgt_n /= s

    corr = fftconvolve(ref_n, tgt_n[::-1], mode="full")
    peak = np.argmax(corr)                     # brightness corr is positive
    lag = peak - (len(tgt_n) - 1)
    offset = float(lag)                         # 1-Hz grid → seconds
    conf = corr[peak] / min(len(ref_n), len(tgt_n))
    print("done")
    return offset, conf


def _brightness_curve(frames_dir):
    pat = re.compile(r"frame_(\d+)\.\w+$", re.IGNORECASE)
    entries = sorted(
        ((int(m.group(1)), p)
         for p in Path(frames_dir).iterdir()
         if (m := pat.match(p.name))),
        key=lambda x: x[0],
    )
    idxs, vals = [], []
    for idx, fp in entries:
        img = cv2.imread(str(fp), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            idxs.append(idx)
            vals.append(float(np.mean(img)))
    return np.array(idxs), np.array(vals)


# ---------------------------------------------------------------------------
# Interactive verification — HTML frame scrubber
# ---------------------------------------------------------------------------

def _build_viewer(ref_video, tgt_video, candidate, ref_label, tgt_label,
                  review_dir, half_range=20):
    """Extract frames, embed as base64, and generate a self-contained HTML viewer.

    The HTML file contains all images inline — no external file dependencies.
    It can be opened directly in any browser, scp'd to a laptop, or previewed
    in VS Code.  Returns the path to the HTML file, or None if no overlap.
    """
    ref_dur = get_video_duration(ref_video)
    tgt_dur = get_video_duration(tgt_video)

    # Overlap in ref-video time
    lo = max(0.0, candidate)
    hi = min(ref_dur, candidate + tgt_dur)
    if hi <= lo:
        return None  # no overlap

    # Reference timestamp — pick the midpoint of the overlap
    ref_time = (lo + hi) / 2.0

    # Target centre = corresponding time in target video
    tgt_centre = ref_time - candidate

    # Clamp extraction window to valid target-video range
    extract_start = max(0.0, tgt_centre - half_range)
    extract_end = min(tgt_dur, tgt_centre + half_range)
    extract_dur = extract_end - extract_start

    rdir = Path(review_dir)
    rdir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Extract reference frame
        ref_img_path = tmp / "ref.jpg"
        print(f"  Extracting reference frame ({ref_label} t={ref_time:.1f}s) …")
        extract_single_frame(ref_video, ref_time, ref_img_path)
        ref_b64 = _img_to_b64(ref_img_path)

        # Extract target frames at 1 fps
        n_frames = int(extract_dur) + 1
        print(f"  Extracting {n_frames} target frames "
              f"({tgt_label} t={extract_start:.0f}–{extract_end:.0f}s) …")
        target_frames = extract_frame_batch(
            tgt_video, extract_start, extract_dur, fps=1,
            output_dir=tmp / "tgt",
        )

        # Build frame metadata for JS — embed each image as base64
        frames_data = []
        initial_idx = 0
        best_dist = float("inf")
        print(f"  Encoding {len(target_frames)} frames into HTML …", end=" ",
              flush=True)
        for i, (fpath, tgt_t) in enumerate(target_frames):
            offset_val = ref_time - tgt_t  # offset if this frame is the match
            frames_data.append({
                "b64": _img_to_b64(fpath),
                "offset": round(offset_val, 1),
                "tgt_time": round(tgt_t, 1),
            })
            dist = abs(offset_val - candidate)
            if dist < best_dist:
                best_dist = dist
                initial_idx = i
        print("done")

    # Write self-contained HTML
    html = _HTML_TEMPLATE.format(
        ref_label=ref_label,
        tgt_label=tgt_label,
        ref_time=ref_time,
        ref_img_b64=ref_b64,
        frames_json=json.dumps(frames_data),
        initial_idx=initial_idx,
    )
    html_path = rdir / "index.html"
    html_path.write_text(html)
    size_mb = html_path.stat().st_size / 1024 / 1024
    print(f"  Viewer saved: {html_path} ({size_mb:.1f} MB, self-contained)")
    return html_path


def verify_offset(ref_video, tgt_video, candidate,
                  ref_label, tgt_label, review_dir, half_range=20,
                  **_ignored):
    """Interactive loop: generate HTML scrubber, let user pick offset.

    Returns the confirmed offset (float).
    """
    offset = candidate

    while True:
        html_path = _build_viewer(
            ref_video, tgt_video, offset, ref_label, tgt_label,
            review_dir, half_range=half_range,
        )
        if html_path is None:
            print(f"\n  No overlap with offset={offset:.1f}s — try another value.")
            raw = input("  New offset: ").strip()
            offset = float(raw)
            continue

        print(f"\n  ▶  Open in browser:  {html_path}")
        print(f"     Use ← → (±1s) or Shift+← → (±5s) to scrub.")
        print(f"     When the two frames show the same moment,")
        print(f"     read the offset value and type it below.\n")
        print(f"  Candidate offset: {offset:+.1f}s")
        print( "  ───────────────────────────────────────────")
        print( "  [Enter]       accept candidate as-is")
        print( "  <number>      set offset from viewer (e.g. 14.0)")
        print( "  r             regenerate with wider range (±40s)")
        print( "  q             quit")
        print( "  ───────────────────────────────────────────")

        resp = input("\n  Your choice: ").strip()

        if resp == "":
            print(f"\n  ✓ Offset confirmed: {offset:+.3f}s")
            return offset
        if resp.lower() == "q":
            print("  Aborted.")
            sys.exit(0)
        if resp.lower() == "r":
            half_range = min(half_range * 2, 120)
            print(f"  Expanding range to ±{half_range}s …")
            continue
        try:
            offset = float(resp)
            # Re-run viewer centred on new offset so user can fine-tune
            print(f"  → offset set to {offset:+.3f}s, regenerating …")
        except ValueError:
            print(f"  Invalid input '{resp}'. Try a number, Enter, r, or q.")


# ---------------------------------------------------------------------------
# Full frame extraction
# ---------------------------------------------------------------------------

def extract_all(cameras, output_dir, fps):
    """Extract synchronized frames for all cameras into output_dir/."""
    overlap_start = max(c["offset"] for c in cameras.values())
    overlap_end = min(c["offset"] + c["duration"] for c in cameras.values())
    dur = overlap_end - overlap_start

    if dur <= 0:
        print("  ERROR: no overlap between all 3 cameras!")
        return None

    print(f"\n  Overlap: {overlap_start:.1f}s → {overlap_end:.1f}s  "
          f"({dur:.0f}s = {dur/60:.1f} min)")
    print(f"  Expected frames per camera: ~{int(dur * fps)}")

    out = Path(output_dir)
    counts = {}
    for name, cam in cameras.items():
        seek = overlap_start - cam["offset"]
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        print(f"  Extracting {name:8s} (seek {seek:.1f}s) …", end=" ", flush=True)
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{seek:.3f}",
             "-i", str(cam["video"]), "-t", f"{dur:.3f}",
             "-vf", f"fps={fps}", "-q:v", "2",
             str(d / "frame_%06d.jpg")],
            capture_output=True, check=True,
        )
        n = len(list(d.glob("frame_*.jpg")))
        counts[name] = n
        print(f"{n} frames")

    return {"overlap_start": overlap_start, "overlap_end": overlap_end,
            "duration": dur, "frame_counts": counts}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Interactive camera synchronization pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--iphone", required=True, help="iPhone video path")
    parser.add_argument("--jvc",    required=True, help="JVC video path")
    parser.add_argument("--mcu",    required=True, help="MCU video path")
    parser.add_argument("--iphone-frames", default=None,
                        help="Dir of already-extracted iPhone frames (for brightness sync)")
    parser.add_argument("--mcu-frames", default=None,
                        help="Dir of already-extracted MCU frames (for brightness sync)")
    parser.add_argument("--jvc-offset", type=float, default=None,
                        help="Skip audio auto-detect; use this JVC offset directly")
    parser.add_argument("--mcu-offset", type=float, default=None,
                        help="Skip brightness auto-detect; use this MCU offset directly")
    parser.add_argument("--output", default="./synced_frames",
                        help="Output directory (default: ./synced_frames)")
    parser.add_argument("--fps", type=float, default=1.0,
                        help="Output frame rate in Hz (default: 1)")
    parser.add_argument("--review-dir", default="./review",
                        help="Where comparison images are saved (default: ./review)")
    parser.add_argument("--half-range", type=int, default=20,
                        help="Verification scrubber range in seconds: ±N around candidate (default: 20)")
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  Interactive Camera Synchronization")
    print("  Reference camera: iPhone  (offset = 0)")
    print("=" * 60)

    # ---- Step 1: iPhone ↔ JVC offset ----------------------------------- #
    step = 1
    total_steps = 4
    print(f"\n[{step}/{total_steps}] iPhone ↔ JVC offset")

    if args.jvc_offset is not None:
        jvc_offset = args.jvc_offset
        print(f"  Using provided offset: {jvc_offset:+.3f}s")
    else:
        print("  Method: audio cross-correlation")
        jvc_offset, jvc_conf = compute_audio_offset(args.iphone, args.jvc)
        direction = "after" if jvc_offset >= 0 else "before"
        print(f"  Candidate: {jvc_offset:+.3f}s "
              f"(JVC started {abs(jvc_offset):.1f}s {direction} iPhone)")
        print(f"  Confidence: {jvc_conf:.6f}")

    # ---- Step 2: Verify iPhone ↔ JVC ----------------------------------- #
    step = 2
    print(f"\n[{step}/{total_steps}] Verify iPhone ↔ JVC alignment")
    jvc_offset = verify_offset(
        args.iphone, args.jvc, jvc_offset,
        "IPHONE", "JVC",
        Path(args.review_dir) / "jvc",
        half_range=args.half_range,
    )

    # ---- Step 3: iPhone ↔ MCU offset ----------------------------------- #
    step = 3
    print(f"\n[{step}/{total_steps}] iPhone ↔ MCU offset")

    if args.mcu_offset is not None:
        mcu_offset = args.mcu_offset
        print(f"  Using provided offset: {mcu_offset:+.3f}s")
    elif args.iphone_frames and args.mcu_frames:
        print("  Method: brightness cross-correlation (MCU has no audio)")
        iphone_fps = get_video_fps(args.iphone)
        mcu_fps = get_video_fps(args.mcu)
        mcu_offset, mcu_conf = compute_brightness_offset(
            args.iphone_frames, args.mcu_frames, iphone_fps, mcu_fps,
        )
        direction = "after" if mcu_offset >= 0 else "before"
        print(f"  Candidate: {mcu_offset:+.0f}s "
              f"(MCU started ~{abs(mcu_offset):.0f}s {direction} iPhone)")
        print(f"  Confidence: {mcu_conf:.6f}")
        print("  (brightness alignment: ~1-2s precision)")
    else:
        print("  MCU has no audio and no frame directories were provided")
        print("  for brightness sync (--iphone-frames / --mcu-frames).")
        print()
        print("  You can identify a common visual event manually:")
        print("    ffmpeg -ss <TIME> -i <VIDEO> -frames:v 1 -q:v 2 check.jpg")
        print()
        raw = input("  Enter MCU offset in seconds (relative to iPhone): ").strip()
        mcu_offset = float(raw)

    # ---- Step 4: Verify iPhone ↔ MCU ----------------------------------- #
    step = 4
    print(f"\n[{step}/{total_steps}] Verify iPhone ↔ MCU alignment")
    mcu_offset = verify_offset(
        args.iphone, args.mcu, mcu_offset,
        "IPHONE", "MCU",
        Path(args.review_dir) / "mcu",
        half_range=args.half_range,
    )

    # ---- Extract synced frames ------------------------------------------ #
    print()
    print("=" * 60)
    print("  Extracting Synchronized Frames")
    print("=" * 60)

    cameras = {
        "iphone": {"video": args.iphone, "offset": 0.0},
        "jvc":    {"video": args.jvc,    "offset": jvc_offset},
        "mcu":    {"video": args.mcu,    "offset": mcu_offset},
    }
    for cam in cameras.values():
        cam["duration"] = get_video_duration(cam["video"])

    print(f"\n  Final offsets (relative to iPhone):")
    print(f"    iPhone :   0.000s  (reference)")
    print(f"    JVC    : {jvc_offset:+.3f}s")
    print(f"    MCU    : {mcu_offset:+.3f}s")

    info = extract_all(cameras, args.output, args.fps)
    if info is None:
        return

    # Save metadata
    meta = {
        "offsets": {"iphone": 0.0, "jvc": jvc_offset, "mcu": mcu_offset},
        "overlap_start_iphone_time": info["overlap_start"],
        "overlap_end_iphone_time": info["overlap_end"],
        "overlap_duration_seconds": info["duration"],
        "fps": args.fps,
        "frame_counts": info["frame_counts"],
        "videos": {n: str(Path(c["video"]).resolve())
                   for n, c in cameras.items()},
    }
    meta_path = Path(args.output) / "sync_metadata.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\n  Metadata saved to {meta_path}")
    print(f"\n  ✓ Done!  Synced frames in {args.output}/")
    print(f"    Same index across iphone/ jvc/ mcu/ = same moment in time.")


if __name__ == "__main__":
    main()
