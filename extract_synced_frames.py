"""Extract synchronized frame triplets from all 3 cameras.

Given time offsets (from find_offset_audio.py / find_offset_brightness.py),
computes the overlap window where all cameras are recording, then uses ffmpeg
to extract frames at matched timestamps.

Output: synced_frames/{iphone,jvc,mcu}/frame_000001.jpg
        Same index across directories = same moment in time.

Usage:
    python extract_synced_frames.py \
        --iphone /path/to/iPhone14_FULL.MOV \
        --jvc /path/to/JVC_Recorder_FULL.m2ts \
        --mcu /path/to/MCU_Stationary_Camera_Full.m4v \
        --jvc-offset 12.5 \
        --mcu-offset -45.0 \
        --output ./synced_frames

    # Verify alignment with side-by-side comparison image
    python extract_synced_frames.py ... --verify 10
"""

import argparse
import subprocess
import json
from pathlib import Path


def get_video_duration(video_path):
    """Get video duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def extract_frames(video_path, start_time, duration, fps, output_dir):
    """Extract frames from video using ffmpeg fps filter.

    ffmpeg -ss START -i VIDEO -t DUR -vf fps=FPS -q:v 2 output/frame_%06d.jpg
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_time:.3f}",
        "-i", str(video_path),
        "-t", f"{duration:.3f}",
        "-vf", f"fps={fps}",
        "-q:v", "2",
        str(output_dir / "frame_%06d.jpg"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {video_path}:\n{result.stderr}")

    frames = sorted(output_dir.glob("frame_*.jpg"))
    return len(frames)


def create_verification_image(output_dir, frame_idx):
    """Create a side-by-side comparison of one frame from all 3 cameras."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        print("opencv-python required for --verify")
        return

    output_dir = Path(output_dir)
    cameras = ["iphone", "jvc", "mcu"]
    images = []

    for cam in cameras:
        fpath = output_dir / cam / f"frame_{frame_idx:06d}.jpg"
        if not fpath.exists():
            print(f"  Frame not found: {fpath}")
            return
        img = cv2.imread(str(fpath))
        # Add camera label
        cv2.putText(img, cam.upper(), (20, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 3)
        images.append(img)

    # Resize all to same height
    target_h = min(img.shape[0] for img in images)
    resized = []
    for img in images:
        scale = target_h / img.shape[0]
        new_w = int(img.shape[1] * scale)
        resized.append(cv2.resize(img, (new_w, target_h)))

    combined = np.concatenate(resized, axis=1)
    out_path = output_dir / f"verify_frame_{frame_idx:06d}.jpg"
    cv2.imwrite(str(out_path), combined)
    print(f"  Verification image saved: {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract synchronized frame triplets from 3 cameras."
    )
    parser.add_argument("--iphone", required=True, help="iPhone video path")
    parser.add_argument("--jvc", required=True, help="JVC video path")
    parser.add_argument("--mcu", required=True, help="MCU video path")
    parser.add_argument(
        "--jvc-offset", type=float, required=True,
        help="JVC offset in seconds relative to iPhone "
             "(positive = JVC started AFTER iPhone)",
    )
    parser.add_argument(
        "--mcu-offset", type=float, required=True,
        help="MCU offset in seconds relative to iPhone "
             "(positive = MCU started AFTER iPhone)",
    )
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--fps", type=float, default=1.0,
                        help="Output frame rate in Hz (default: 1)")
    parser.add_argument(
        "--verify", type=int, nargs="*", default=None,
        help="After extraction, create side-by-side comparison for these "
             "frame indices (e.g., --verify 1 50 100)",
    )
    args = parser.parse_args()

    cameras = {
        "iphone": {"video": args.iphone, "offset": 0.0},
        "jvc":    {"video": args.jvc,    "offset": args.jvc_offset},
        "mcu":    {"video": args.mcu,    "offset": args.mcu_offset},
    }

    # Get durations
    print("Probing video durations...")
    for name, cam in cameras.items():
        cam["duration"] = get_video_duration(cam["video"])
        print(f"  {name:8s}: {cam['duration']:8.1f}s  offset={cam['offset']:+.1f}s")

    # Overlap window in "iPhone time" (time since iPhone started recording)
    # Each camera covers iPhone-time range [offset, offset + duration]
    overlap_start = max(cam["offset"] for cam in cameras.values())
    overlap_end = min(cam["offset"] + cam["duration"] for cam in cameras.values())
    overlap_duration = overlap_end - overlap_start

    if overlap_duration <= 0:
        print("\nERROR: No time overlap between all 3 cameras!")
        for name, cam in cameras.items():
            start = cam["offset"]
            end = cam["offset"] + cam["duration"]
            print(f"  {name}: iPhone-time [{start:.1f}, {end:.1f}]")
        return

    expected_frames = int(overlap_duration * args.fps)
    print(f"\nOverlap window (iPhone time): {overlap_start:.1f}s → {overlap_end:.1f}s")
    print(f"Overlap duration: {overlap_duration:.1f}s ({overlap_duration/60:.1f} min)")
    print(f"Output fps: {args.fps}")
    print(f"Expected frames per camera: ~{expected_frames}")

    # Extract frames
    output = Path(args.output)
    frame_counts = {}
    for name, cam in cameras.items():
        # Seek position in THIS camera's video
        cam_seek = overlap_start - cam["offset"]
        print(f"\nExtracting {name} (seeking to {cam_seek:.1f}s in {Path(cam['video']).name})...")
        n = extract_frames(cam["video"], cam_seek, overlap_duration, args.fps,
                           output / name)
        frame_counts[name] = n
        print(f"  → {n} frames extracted to {output / name}/")

    # Verify frame counts match
    counts = list(frame_counts.values())
    if len(set(counts)) > 1:
        print(f"\nWARNING: Frame counts differ: {frame_counts}")
        print("This can happen due to frame-rate rounding. The last frame(s) may not match.")
        min_count = min(counts)
        print(f"Use frames 1 through {min_count} for guaranteed alignment.")

    # Save metadata
    meta = {
        "offsets": {name: cam["offset"] for name, cam in cameras.items()},
        "overlap_start_iphone_time": overlap_start,
        "overlap_end_iphone_time": overlap_end,
        "overlap_duration": overlap_duration,
        "fps": args.fps,
        "frame_counts": frame_counts,
        "videos": {name: str(Path(cam["video"]).resolve())
                   for name, cam in cameras.items()},
    }
    meta_path = output / "sync_metadata.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"\nMetadata saved to {meta_path}")

    # Verification images
    if args.verify is not None:
        indices = args.verify if args.verify else [1, expected_frames // 2, expected_frames]
        print(f"\nCreating verification images for frames: {indices}")
        for idx in indices:
            create_verification_image(output, idx)

    print(f"\nDone! Synchronized frames in {output}/")
    print(f"  frame_000001.jpg across iphone/jvc/mcu = same moment in time")


if __name__ == "__main__":
    main()
