#!/usr/bin/env python3
"""Generate a side-by-side video of two cameras using a known offset.

Reads offset from sync_config.json. Produces a single MP4 with both
cameras playing in sync, labeled. Useful for visually verifying alignment.

Usage:
    # Full overlap
    python side_by_side.py --cam1 iphone --cam2 mcu

    # Specific iPhone time window
    python side_by_side.py --cam1 iphone --cam2 mcu --start 5 --end 30

    # Custom output
    python side_by_side.py --cam1 iphone --cam2 mcu --start 5 --end 30 --output demo.mp4
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

CONFIG_FILE = Path("sync_config.json")


def load_config():
    if not CONFIG_FILE.exists():
        print(f"ERROR: {CONFIG_FILE} not found. Run pair-mcu or pair-jvc first.")
        sys.exit(1)
    return json.loads(CONFIG_FILE.read_text())


def get_video_duration(video_path):
    cmd = ["ffprobe", "-v", "error",
           "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1",
           str(video_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def main():
    parser = argparse.ArgumentParser(
        description="Generate a side-by-side sync verification video.",
    )
    parser.add_argument("--cam1", default="iphone", help="Left camera (default: iphone)")
    parser.add_argument("--cam2", default="mcu", help="Right camera (default: mcu)")
    parser.add_argument("--start", type=float, default=None, help="Start time in iPhone seconds (default: overlap start)")
    parser.add_argument("--end", type=float, default=None, help="End time in iPhone seconds (default: overlap end)")
    parser.add_argument("--output", default=None, help="Output MP4 path (overrides default naming)")
    parser.add_argument("--suffix", default=None, help="Suffix appended to default name (e.g. --suffix ignition → side_by_side_iphone_mcu_ignition.mp4)")
    parser.add_argument("--height", type=int, default=540, help="Height of each video in pixels (default: 540)")
    parser.add_argument("--fps", type=int, default=15, help="Output video fps (default: 15)")
    args = parser.parse_args()

    cfg = load_config()

    # Validate cameras
    for cam in [args.cam1, args.cam2]:
        if cam not in cfg.get("videos", {}):
            print(f"ERROR: '{cam}' not found in {CONFIG_FILE}. Available: {list(cfg['videos'].keys())}")
            sys.exit(1)
        if cam not in cfg.get("offsets", {}):
            print(f"ERROR: offset for '{cam}' not found. Run pair-{cam} first.")
            sys.exit(1)

    vid1 = cfg["videos"][args.cam1]
    vid2 = cfg["videos"][args.cam2]
    off1 = cfg["offsets"][args.cam1]
    off2 = cfg["offsets"][args.cam2]

    dur1 = get_video_duration(vid1)
    dur2 = get_video_duration(vid2)

    # Compute overlap in iPhone time
    overlap_start = max(off1, off2)
    overlap_end = min(off1 + dur1, off2 + dur2)

    if overlap_end <= overlap_start:
        print("ERROR: no overlap between these cameras!")
        sys.exit(1)

    # Apply user time window (in iPhone time)
    start = args.start if args.start is not None else overlap_start
    end = args.end if args.end is not None else overlap_end
    start = max(start, overlap_start)
    end = min(end, overlap_end)
    duration = end - start

    if duration <= 0:
        print(f"ERROR: invalid time window. Overlap is {overlap_start:.1f}s–{overlap_end:.1f}s (iPhone time).")
        sys.exit(1)

    # Seek positions in each camera's video
    seek1 = start - off1
    seek2 = start - off2

    if args.output:
        output = args.output
    elif args.suffix:
        output = f"side_by_side_{args.cam1}_{args.cam2}_{args.suffix}.mp4"
    else:
        output = f"side_by_side_{args.cam1}_{args.cam2}.mp4"
    h = args.height

    print(f"Camera 1    : {args.cam1} ({Path(vid1).name})")
    print(f"Camera 2    : {args.cam2} ({Path(vid2).name})")
    print(f"Offsets     : {args.cam1}={off1:+.3f}s, {args.cam2}={off2:+.3f}s")
    print(f"iPhone time : {start:.1f}s → {end:.1f}s ({duration:.1f}s)")
    print(f"Seek        : {args.cam1} t={seek1:.1f}s, {args.cam2} t={seek2:.1f}s")
    print(f"Output      : {output} ({args.fps}fps, {h}p per camera)")
    print()
    print("Rendering …", end=" ", flush=True)

    # ffmpeg command: two inputs, scale + pad + hstack, add labels
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{seek1:.3f}", "-i", vid1,
        "-ss", f"{seek2:.3f}", "-i", vid2,
        "-t", f"{duration:.3f}",
        "-filter_complex",
        f"[0:v]scale=-2:{h}[v0];[1:v]scale=-2:{h}[v1];[v0][v1]hstack=inputs=2[out]",
        "-map", "[out]",
        "-r", str(args.fps),
        "-c:v", "mpeg4", "-q:v", "5",
        "-an",
        output,
    ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("FAILED")
        print(r.stderr[-500:])
        sys.exit(1)

    size_mb = Path(output).stat().st_size / 1024 / 1024
    print(f"done ({size_mb:.1f} MB)")
    print(f"\nDownload and play: {output}")


if __name__ == "__main__":
    main()
