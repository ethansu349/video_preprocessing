#!/usr/bin/env python3
"""Extract a small batch of frames around a given timestamp for visual review.

Two modes:
  1fps (default): 1 frame per second over a wide range — find the rough second
  native fps:     all frames at original rate over a narrow range — pinpoint exact frame

Filenames encode the exact PTS timestamp: t_0118.000s.jpg, t_0118.033s.jpg, ...
Output folder is overwritten each run to avoid storage buildup.

Usage:
    # Coarse: find rough second (±30s = ~60 frames)
    python peek_frames.py --video /path/to/iPhone14_FULL.MOV --time 120 --range 30

    # Fine: pinpoint exact frame (±2s at native 30fps = ~120 frames)
    python peek_frames.py --video /path/to/iPhone14_FULL.MOV --time 118 --range 2 --native-fps

    # Custom output directory
    python peek_frames.py --video /path/to/video.MOV --time 60 --range 5 --native-fps --output ./my_peek
"""

import argparse
import subprocess
import shutil
from pathlib import Path


def get_video_info(video_path):
    """Get fps and duration via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,nb_frames,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video_path),
    ]
    import json
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(r.stdout)

    # FPS
    rate_str = info["streams"][0]["r_frame_rate"]
    num, den = rate_str.split("/")
    fps = int(num) / int(den)

    # Duration — prefer stream, fall back to format
    dur = info["streams"][0].get("duration")
    if dur is None or dur == "N/A":
        dur = info["format"]["duration"]
    duration = float(dur)

    return fps, duration


def extract_frames(video_path, start, duration, fps, output_dir):
    """Extract frames and return list of output files with their PTS timestamps.

    Uses ffmpeg's showinfo filter to get exact PTS for each frame.
    """
    output_dir = Path(output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    # Extract frames — use fps filter for 1fps mode, no fps filter for native
    vf = f"fps={fps}" if fps != "native" else ""

    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(video_path), "-t", f"{duration:.3f}"]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-q:v", "2", "-frame_pts", "1", str(output_dir / "tmp_%06d.jpg")]

    subprocess.run(cmd, capture_output=True, check=True)

    # Now get actual PTS timestamps using ffprobe on the original video
    # We'll compute timestamps from the known start time and fps
    tmp_frames = sorted(output_dir.glob("tmp_*.jpg"))

    if not tmp_frames:
        return []

    # For native fps, compute timestamps from frame index and native fps
    # For 1fps mode, timestamps are at 1-second intervals
    if fps == "native":
        # Get native fps
        native_fps, _ = get_video_info(video_path)
        interval = 1.0 / native_fps
    else:
        interval = 1.0 / fps

    results = []
    for i, tmp_path in enumerate(tmp_frames):
        timestamp = start + i * interval
        # Rename with timestamp
        new_name = f"t_{timestamp:08.3f}s.jpg"
        new_path = output_dir / new_name
        tmp_path.rename(new_path)
        results.append((new_path, timestamp))

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Extract frames around a timestamp for visual review.",
    )
    parser.add_argument("--video", required=True, help="Video file path")
    parser.add_argument("--time", type=float, required=True, help="Center timestamp in seconds (e.g., 120 for 2:00)")
    parser.add_argument("--range", type=float, default=10, help="Half-range in seconds: extract [time-range, time+range] (default: 10)")
    parser.add_argument("--native-fps", action="store_true", help="Extract at native frame rate (default: 1fps)")
    parser.add_argument("--name", default=None, help="Camera name for subfolder (default: auto-detect from filename, e.g. 'iphone', 'jvc', 'mcu')")
    parser.add_argument("--output", default="./peek", help="Base output directory (default: ./peek)")
    args = parser.parse_args()

    video = Path(args.video)
    native_fps, duration = get_video_info(video)

    # Auto-detect camera name from filename if not provided
    if args.name:
        cam_name = args.name
    else:
        vname = video.stem.lower()
        if "iphone" in vname:
            cam_name = "iphone"
        elif "jvc" in vname:
            cam_name = "jvc"
        elif "mcu" in vname:
            cam_name = "mcu"
        else:
            cam_name = video.stem
    args.output = str(Path(args.output) / cam_name)

    # Compute extraction window
    center = args.time
    half = args.range
    start = max(0.0, center - half)
    end = min(duration, center + half)
    extract_dur = end - start

    fps_mode = "native" if args.native_fps else 1
    if args.native_fps:
        expected = int(extract_dur * native_fps)
        fps_label = f"{native_fps:.2f} (native)"
        interval = 1.0 / native_fps
    else:
        expected = int(extract_dur)
        fps_label = "1 (coarse)"
        interval = 1.0

    print(f"Video       : {video.name}")
    print(f"Native fps  : {native_fps:.2f}")
    print(f"Duration    : {duration:.1f}s")
    print(f"Extract mode: {fps_label}")
    print(f"Window      : {start:.3f}s → {end:.3f}s ({extract_dur:.1f}s)")
    print(f"Expected    : ~{expected} frames (interval: {interval:.3f}s)")
    print(f"Output      : {args.output}/")
    print()

    print("Extracting …", end=" ", flush=True)
    frames = extract_frames(video, start, extract_dur, fps_mode, args.output)
    print("done")

    if not frames:
        print("No frames extracted!")
        return

    first_t = frames[0][1]
    last_t = frames[-1][1]
    print(f"\nExtracted {len(frames)} frames")
    print(f"  First: {frames[0][0].name}  (t={first_t:.3f}s)")
    print(f"  Last:  {frames[-1][0].name}  (t={last_t:.3f}s)")
    print(f"\nBrowse in VS Code: click any .jpg in {args.output}/ to preview")
    print(f"Use the timestamp from the filename as --ref-time in sync_cameras.py")


if __name__ == "__main__":
    main()
