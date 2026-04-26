#!/usr/bin/env python3
"""Interactive camera synchronization pipeline.

Three independent subcommands — run in any order:

    # 1. Find iPhone ↔ MCU offset (brightness correlation, no audio)
    python sync_cameras.py pair-mcu --iphone VIDEO --mcu VIDEO \
        --iphone-frames DIR --mcu-frames DIR [--ref-time T]

    # 2. Find iPhone ↔ JVC offset (audio correlation)
    python sync_cameras.py pair-jvc --iphone VIDEO --jvc VIDEO [--ref-time T]

    # 3. Extract synced frames (after both offsets are saved)
    python sync_cameras.py extract [--output DIR] [--fps N]

Each pair command saves its result to sync_config.json. The extract command
reads both offsets from that file. Reference camera: iPhone (offset = 0).
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

CONFIG_FILE = Path("sync_config.json")


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------

def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {"videos": {}, "offsets": {"iphone": 0.0}}


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    print(f"\n  Config saved to {CONFIG_FILE}")


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
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{timestamp:.3f}",
         "-i", str(video_path), "-frames:v", "1", "-q:v", "2",
         str(output_path)],
        capture_output=True, check=True,
    )


def extract_frame_batch(video_path, start_time, duration, fps, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
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
    return [(f, start_time + i / fps) for i, f in enumerate(frames)]


# ---------------------------------------------------------------------------
# HTML viewer template
# ---------------------------------------------------------------------------

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
  <button onclick="step(-1)">&lsaquo; -1 frame</button>
  <button onclick="step(1)">+1 frame &rsaquo;</button>
</div>
<div class="bar">
  <input type="range" id="slider" min="0" max="0" value="0"
         oninput="goTo(parseInt(this.value))">
</div>
<div class="hint" style="margin-top:12px">
  Keyboard: &larr;/&rarr; = &plusmn;1 frame
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
  document.getElementById('val').textContent = f.offset.toFixed(3);
  document.getElementById('tgt_t').textContent = f.tgt_time.toFixed(3);
  document.getElementById('slider').value = idx;
  const dir = f.offset >= 0
    ? '{tgt_label} started ' + f.offset.toFixed(3) + 's after {ref_label}'
    : '{tgt_label} started ' + Math.abs(f.offset).toFixed(3) + 's before {ref_label}';
  document.getElementById('meaning').textContent = dir;
}}
function step(d) {{ goTo(idx + d); }}
function goTo(i) {{ idx = Math.max(0, Math.min(frames.length - 1, i)); render(); }}
document.getElementById('slider').max = frames.length - 1;
document.addEventListener('keydown', e => {{
  if (e.key === 'ArrowRight') {{ step(1); e.preventDefault(); }}
  if (e.key === 'ArrowLeft')  {{ step(-1); e.preventDefault(); }}
}});
render();
</script>
</body>
</html>
"""


def _img_to_b64(path):
    data = Path(path).read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


# ---------------------------------------------------------------------------
# Offset computation — audio
# ---------------------------------------------------------------------------

def compute_audio_offset(ref_video, target_video, sample_rate=8000):
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

    ref_sig = interp1d(rt, rb, fill_value="extrapolate")(np.arange(rt[0], rt[-1]))
    tgt_sig = interp1d(tt, tb, fill_value="extrapolate")(np.arange(tt[0], tt[-1]))

    print("  Cross-correlating …", end=" ", flush=True)
    ref_n = (ref_sig - np.mean(ref_sig)).astype(np.float64)
    tgt_n = (tgt_sig - np.mean(tgt_sig)).astype(np.float64)
    if (s := np.std(ref_n)) > 0: ref_n /= s
    if (s := np.std(tgt_n)) > 0: tgt_n /= s

    corr = fftconvolve(ref_n, tgt_n[::-1], mode="full")
    peak = np.argmax(corr)
    lag = peak - (len(tgt_n) - 1)
    offset = float(lag)
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
                  review_dir, half_range=2, ref_time=None):
    ref_dur = get_video_duration(ref_video)
    tgt_dur = get_video_duration(tgt_video)

    lo = max(0.0, candidate)
    hi = min(ref_dur, candidate + tgt_dur)
    if hi <= lo:
        return None

    if ref_time is None:
        ref_time = (lo + hi) / 2.0
    ref_time = max(lo, min(hi, ref_time))

    tgt_centre = ref_time - candidate
    extract_start = max(0.0, tgt_centre - half_range)
    extract_end = min(tgt_dur, tgt_centre + half_range)
    extract_dur = extract_end - extract_start

    rdir = Path(review_dir)
    rdir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        ref_img_path = tmp / "ref.jpg"
        print(f"  Extracting reference frame ({ref_label} t={ref_time:.1f}s) …")
        extract_single_frame(ref_video, ref_time, ref_img_path)
        ref_b64 = _img_to_b64(ref_img_path)

        tgt_fps = get_video_fps(tgt_video)
        n_frames = int(extract_dur * tgt_fps)
        print(f"  Extracting ~{n_frames} target frames at native {tgt_fps:.1f}fps "
              f"({tgt_label} t={extract_start:.1f}–{extract_end:.1f}s) …")
        target_frames = extract_frame_batch(
            tgt_video, extract_start, extract_dur, fps=tgt_fps,
            output_dir=tmp / "tgt",
        )

        frames_data = []
        initial_idx = 0
        best_dist = float("inf")
        print(f"  Encoding {len(target_frames)} frames into HTML …", end=" ",
              flush=True)
        for i, (fpath, tgt_t) in enumerate(target_frames):
            offset_val = ref_time - tgt_t
            frames_data.append({
                "b64": _img_to_b64(fpath),
                "offset": round(offset_val, 3),
                "tgt_time": round(tgt_t, 3),
            })
            dist = abs(offset_val - candidate)
            if dist < best_dist:
                best_dist = dist
                initial_idx = i
        print("done")

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
                  ref_label, tgt_label, review_dir, half_range=2,
                  ref_time=None):
    offset = candidate

    while True:
        html_path = _build_viewer(
            ref_video, tgt_video, offset, ref_label, tgt_label,
            review_dir, half_range=half_range, ref_time=ref_time,
        )
        if html_path is None:
            print(f"\n  No overlap with offset={offset:.1f}s — try another value.")
            raw = input("  New offset: ").strip()
            offset = float(raw)
            continue

        print(f"\n  ▶  Download and open in browser:  {html_path}")
        print(f"     Use ← → (±1s) or Shift+← → (±5s) to scrub.")
        print(f"     When the two frames show the same moment,")
        print(f"     read the offset value and type it below.\n")
        print(f"  Candidate offset: {offset:+.1f}s")
        print( "  ───────────────────────────────────────────")
        print( "  [Enter]       accept candidate as-is")
        print( "  <number>      set offset from viewer (e.g. 14.0)")
        print( "  r             regenerate with wider range")
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
            print(f"  → offset set to {offset:+.3f}s, regenerating …")
        except ValueError:
            print(f"  Invalid input '{resp}'. Try a number, Enter, r, or q.")


# ---------------------------------------------------------------------------
# Full frame extraction
# ---------------------------------------------------------------------------

def extract_all(cameras, output_dir, fps):
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
# Subcommands
# ---------------------------------------------------------------------------

def cmd_pair_mcu(args):
    """Find and save iPhone ↔ MCU offset."""
    print()
    print("=" * 60)
    print("  Pairing: iPhone ↔ MCU")
    print("=" * 60)

    cfg = load_config()
    cfg["videos"]["iphone"] = str(Path(args.iphone).resolve())
    cfg["videos"]["mcu"] = str(Path(args.mcu).resolve())

    # Compute offset
    if args.mcu_offset is not None:
        mcu_offset = args.mcu_offset
        print(f"\n  Using provided offset: {mcu_offset:+.3f}s")
    elif args.iphone_frames and args.mcu_frames:
        print("\n  Method: brightness cross-correlation (MCU has no audio)")
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
        print("\n  No frame directories provided for brightness correlation.")
        print("  Provide --iphone-frames and --mcu-frames, or --mcu-offset directly.")
        print("  Use peek_frames.py to find the ignition moment manually.")
        raw = input("\n  Enter MCU offset in seconds (relative to iPhone): ").strip()
        mcu_offset = float(raw)

    # Convert --mcu-time to --ref-time using candidate offset
    ref_time = args.ref_time
    if args.mcu_time is not None:
        ref_time = args.mcu_time + mcu_offset
        iphone_dur = get_video_duration(args.iphone)
        print(f"\n  MCU anchor: t={args.mcu_time:.3f}s in MCU video")
        print(f"  → estimated iPhone time: {args.mcu_time:.3f} + ({mcu_offset:+.1f}) = {ref_time:.1f}s")
        if ref_time < 0 or ref_time > iphone_dur:
            print(f"\n  WARNING: estimated iPhone time {ref_time:.1f}s is outside "
                  f"iPhone duration (0–{iphone_dur:.0f}s).")
            print(f"  The brightness correlation likely failed (confidence was low).")
            print(f"\n  To find the offset manually:")
            print(f"    1. Run: python peek_frames.py --video <iPhone> --time <guess> --range 30")
            print(f"    2. Find the iPhone ignition frame timestamp (T)")
            print(f"    3. Offset = T - {args.mcu_time:.3f}")
            print(f"    4. Re-run with: --mcu-offset <offset>")
            print()
            raw = input("  Enter MCU offset manually, or q to quit: ").strip()
            if raw.lower() == "q":
                sys.exit(0)
            mcu_offset = float(raw)
            ref_time = args.mcu_time + mcu_offset
            print(f"  → revised iPhone time: {ref_time:.1f}s")

    # Verify
    print("\n  Verifying alignment …")
    mcu_offset = verify_offset(
        args.iphone, args.mcu, mcu_offset,
        "IPHONE", "MCU",
        Path(args.review_dir) / "mcu",
        half_range=args.half_range,
        ref_time=ref_time,
    )

    # Save
    cfg["offsets"]["mcu"] = mcu_offset
    save_config(cfg)

    print(f"\n  ✓ iPhone ↔ MCU offset: {mcu_offset:+.3f}s")
    if "jvc" in cfg["offsets"]:
        print(f"  ✓ JVC offset also saved ({cfg['offsets']['jvc']:+.3f}s)")
        print(f"  → Ready to run: python sync_cameras.py extract")
    else:
        print(f"  → Next: python sync_cameras.py pair-jvc ...")


def cmd_pair_jvc(args):
    """Find and save iPhone ↔ JVC offset."""
    print()
    print("=" * 60)
    print("  Pairing: iPhone ↔ JVC")
    print("=" * 60)

    cfg = load_config()
    cfg["videos"]["iphone"] = str(Path(args.iphone).resolve())
    cfg["videos"]["jvc"] = str(Path(args.jvc).resolve())

    # Compute offset
    if args.jvc_offset is not None:
        jvc_offset = args.jvc_offset
        print(f"\n  Using provided offset: {jvc_offset:+.3f}s")
    else:
        print("\n  Method: audio cross-correlation")
        jvc_offset, jvc_conf = compute_audio_offset(args.iphone, args.jvc)
        direction = "after" if jvc_offset >= 0 else "before"
        print(f"  Candidate: {jvc_offset:+.3f}s "
              f"(JVC started {abs(jvc_offset):.1f}s {direction} iPhone)")
        print(f"  Confidence: {jvc_conf:.6f}")

    # Verify
    print("\n  Verifying alignment …")
    jvc_offset = verify_offset(
        args.iphone, args.jvc, jvc_offset,
        "IPHONE", "JVC",
        Path(args.review_dir) / "jvc",
        half_range=args.half_range,
        ref_time=args.ref_time,
    )

    # Save
    cfg["offsets"]["jvc"] = jvc_offset
    save_config(cfg)

    print(f"\n  ✓ iPhone ↔ JVC offset: {jvc_offset:+.3f}s")
    if "mcu" in cfg["offsets"]:
        print(f"  ✓ MCU offset also saved ({cfg['offsets']['mcu']:+.3f}s)")
        print(f"  → Ready to run: python sync_cameras.py extract")
    else:
        print(f"  → Next: python sync_cameras.py pair-mcu ...")


def cmd_extract(args):
    """Extract synchronized frames using saved offsets."""
    cfg = load_config()

    # Validate config
    missing = []
    for cam in ["iphone", "jvc", "mcu"]:
        if cam not in cfg.get("offsets", {}):
            missing.append(cam)
        if cam not in cfg.get("videos", {}):
            missing.append(f"{cam} video path")
    if missing:
        print(f"  ERROR: Missing from {CONFIG_FILE}: {', '.join(missing)}")
        print(f"  Run pair-jvc and pair-mcu first.")
        return

    print()
    print("=" * 60)
    print("  Extracting Synchronized Frames")
    print("=" * 60)

    cameras = {}
    for name in ["iphone", "jvc", "mcu"]:
        cameras[name] = {
            "video": cfg["videos"][name],
            "offset": cfg["offsets"][name],
        }
        cameras[name]["duration"] = get_video_duration(cameras[name]["video"])

    fps = args.fps
    output = args.output

    print(f"\n  Offsets (relative to iPhone):")
    for name, cam in cameras.items():
        print(f"    {name:8s}: {cam['offset']:+.3f}s")

    info = extract_all(cameras, output, fps)
    if info is None:
        return

    # Save metadata
    meta = {
        "offsets": {n: c["offset"] for n, c in cameras.items()},
        "overlap_start_iphone_time": info["overlap_start"],
        "overlap_end_iphone_time": info["overlap_end"],
        "overlap_duration_seconds": info["duration"],
        "fps": fps,
        "frame_counts": info["frame_counts"],
        "videos": cfg["videos"],
    }
    meta_path = Path(output) / "sync_metadata.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\n  Metadata saved to {meta_path}")
    print(f"\n  ✓ Done!  Synced frames in {output}/")
    print(f"    Same index across iphone/ jvc/ mcu/ = same moment in time.")


def cmd_status(args):
    """Show current sync_config.json status."""
    cfg = load_config()
    print(f"\n  Config file: {CONFIG_FILE}")
    if not CONFIG_FILE.exists():
        print("  (not yet created — run pair-mcu or pair-jvc first)")
        return

    print(f"\n  Videos:")
    for cam in ["iphone", "jvc", "mcu"]:
        v = cfg.get("videos", {}).get(cam, "—")
        print(f"    {cam:8s}: {v}")

    print(f"\n  Offsets:")
    offsets = cfg.get("offsets", {})
    print(f"    iphone  :   0.000s  (reference)")
    for cam in ["jvc", "mcu"]:
        if cam in offsets:
            print(f"    {cam:8s}: {offsets[cam]:+.3f}s")
        else:
            print(f"    {cam:8s}: NOT YET DETERMINED")

    if "jvc" in offsets and "mcu" in offsets:
        print(f"\n  ✓ Both offsets determined. Ready to: python sync_cameras.py extract")
    else:
        needed = [c for c in ["jvc", "mcu"] if c not in offsets]
        print(f"\n  Still needed: {', '.join(f'pair-{c}' for c in needed)}")


# ---------------------------------------------------------------------------
# Main — subcommand dispatch
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Interactive camera synchronization — independent subcommands.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Subcommand")

    # ---- pair-mcu ----
    p_mcu = sub.add_parser("pair-mcu", help="Find iPhone ↔ MCU offset (brightness)")
    p_mcu.add_argument("--iphone", required=True, help="iPhone video path")
    p_mcu.add_argument("--mcu", required=True, help="MCU video path")
    p_mcu.add_argument("--iphone-frames", default=None, help="iPhone extracted frames dir")
    p_mcu.add_argument("--mcu-frames", default=None, help="MCU extracted frames dir")
    p_mcu.add_argument("--mcu-offset", type=float, default=None, help="Skip auto-detect, use this offset")
    p_mcu.add_argument("--ref-time", type=float, default=None, help="Anchor timestamp in iPhone video (e.g. ignition)")
    p_mcu.add_argument("--mcu-time", type=float, default=None, help="Anchor timestamp in MCU video (auto-converts to --ref-time using candidate offset)")
    p_mcu.add_argument("--half-range", type=int, default=2, help="Viewer range ±N seconds (default: 2)")
    p_mcu.add_argument("--review-dir", default="./review", help="Review output dir (default: ./review)")

    # ---- pair-jvc ----
    p_jvc = sub.add_parser("pair-jvc", help="Find iPhone ↔ JVC offset (audio)")
    p_jvc.add_argument("--iphone", required=True, help="iPhone video path")
    p_jvc.add_argument("--jvc", required=True, help="JVC video path")
    p_jvc.add_argument("--jvc-offset", type=float, default=None, help="Skip auto-detect, use this offset")
    p_jvc.add_argument("--ref-time", type=float, default=None, help="Anchor timestamp in iPhone video (e.g. ignition)")
    p_jvc.add_argument("--half-range", type=int, default=2, help="Viewer range ±N seconds (default: 2)")
    p_jvc.add_argument("--review-dir", default="./review", help="Review output dir (default: ./review)")

    # ---- extract ----
    p_ext = sub.add_parser("extract", help="Extract synced frames (needs both offsets saved)")
    p_ext.add_argument("--output", default="./synced_frames", help="Output directory (default: ./synced_frames)")
    p_ext.add_argument("--fps", type=float, default=1.0, help="Output frame rate (default: 1)")

    # ---- status ----
    sub.add_parser("status", help="Show current pairing status")

    args = parser.parse_args()

    if args.command == "pair-mcu":
        cmd_pair_mcu(args)
    elif args.command == "pair-jvc":
        cmd_pair_jvc(args)
    elif args.command == "extract":
        cmd_extract(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
