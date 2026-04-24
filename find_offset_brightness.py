"""Find time offset between two cameras using brightness cross-correlation.

Uses existing extracted frames to compute a mean-brightness time series per
camera, resamples both to a uniform 1 Hz grid, cross-correlates, and reports
the offset.  Useful when one camera has no audio (e.g., MCU).

Positive offset = target started AFTER reference in real time.

Usage:
    python find_offset_brightness.py \
        --ref-frames /path/to/iPhone_Frames \
        --target-frames /path/to/MCU_Stationary_Camera_Frames \
        --ref-fps 30.0 \
        --target-fps 25.31

    # Auto-detect fps from raw video files
    python find_offset_brightness.py \
        --ref-frames /path/to/iPhone_Frames \
        --target-frames /path/to/MCU_Stationary_Camera_Frames \
        --ref-video /path/to/iPhone14_FULL.MOV \
        --target-video /path/to/MCU_Stationary_Camera_Full.m4v
"""

import argparse
import re
import subprocess
import numpy as np
from pathlib import Path


def get_video_fps(video_path):
    """Get native frame rate from video file via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    num, den = result.stdout.strip().split("/")
    return int(num) / int(den)


def compute_brightness_curve(frames_dir):
    """Compute mean brightness for each frame; return (frame_indices, brightnesses).

    Frame index is parsed from the filename (e.g., frame_000030.jpg → 30).
    """
    import cv2

    pattern = re.compile(r"frame_(\d+)\.(jpg|jpeg|png|bmp|tif|tiff)$", re.IGNORECASE)
    frames_dir = Path(frames_dir)
    entries = []
    for f in sorted(frames_dir.iterdir()):
        m = pattern.match(f.name)
        if m:
            entries.append((int(m.group(1)), f))

    entries.sort(key=lambda x: x[0])

    indices = []
    brightnesses = []
    for idx, fpath in entries:
        img = cv2.imread(str(fpath), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        indices.append(idx)
        brightnesses.append(float(np.mean(img)))

    return np.array(indices), np.array(brightnesses)


def resample_to_uniform(times, values, dt=1.0):
    """Resample an irregularly-sampled signal to a uniform grid via linear interpolation."""
    from scipy.interpolate import interp1d

    t_uniform = np.arange(times[0], times[-1], dt)
    interp = interp1d(times, values, kind="linear", fill_value="extrapolate")
    return t_uniform, interp(t_uniform)


def find_offset(ref_signal, target_signal):
    """Cross-correlate two 1-Hz signals and return offset in seconds."""
    from scipy.signal import fftconvolve

    ref = (ref_signal - np.mean(ref_signal)).astype(np.float64)
    target = (target_signal - np.mean(target_signal)).astype(np.float64)

    ref_std, target_std = np.std(ref), np.std(target)
    if ref_std > 0:
        ref /= ref_std
    if target_std > 0:
        target /= target_std

    corr = fftconvolve(ref, target[::-1], mode="full")

    peak_idx = np.argmax(corr)          # brightness correlation is always positive
    peak_lag = peak_idx - (len(target) - 1)
    offset_seconds = float(peak_lag)    # 1-Hz grid → lag in seconds directly

    peak_val = corr[peak_idx] / min(len(ref), len(target))

    return offset_seconds, peak_val, corr


def main():
    parser = argparse.ArgumentParser(
        description="Find time offset between two cameras using brightness cross-correlation."
    )
    parser.add_argument("--ref-frames", required=True, help="Directory of reference camera frames")
    parser.add_argument("--target-frames", required=True, help="Directory of target camera frames")

    fps_group = parser.add_argument_group("frame rate (provide fps OR video for auto-detect)")
    fps_group.add_argument("--ref-fps", type=float, default=None,
                           help="Native fps of reference camera")
    fps_group.add_argument("--target-fps", type=float, default=None,
                           help="Native fps of target camera")
    fps_group.add_argument("--ref-video", type=str, default=None,
                           help="Reference video file (to auto-detect fps)")
    fps_group.add_argument("--target-video", type=str, default=None,
                           help="Target video file (to auto-detect fps)")

    parser.add_argument("--save-plot", type=str, default=None,
                        help="Save brightness + correlation plot (requires matplotlib)")
    args = parser.parse_args()

    # Resolve fps
    if args.ref_fps is None:
        if args.ref_video is None:
            parser.error("Provide either --ref-fps or --ref-video")
        args.ref_fps = get_video_fps(args.ref_video)
        print(f"Auto-detected ref fps: {args.ref_fps:.2f}")

    if args.target_fps is None:
        if args.target_video is None:
            parser.error("Provide either --target-fps or --target-video")
        args.target_fps = get_video_fps(args.target_video)
        print(f"Auto-detected target fps: {args.target_fps:.2f}")

    # Compute brightness curves
    print(f"\nLoading reference frames from {Path(args.ref_frames).name}/ ...")
    ref_indices, ref_brightness = compute_brightness_curve(args.ref_frames)
    ref_times = ref_indices / args.ref_fps
    print(f"  {len(ref_indices)} frames, {ref_times[-1]:.1f}s span")

    print(f"Loading target frames from {Path(args.target_frames).name}/ ...")
    tgt_indices, tgt_brightness = compute_brightness_curve(args.target_frames)
    tgt_times = tgt_indices / args.target_fps
    print(f"  {len(tgt_indices)} frames, {tgt_times[-1]:.1f}s span")

    # Resample to uniform 1 Hz
    ref_t, ref_signal = resample_to_uniform(ref_times, ref_brightness)
    tgt_t, tgt_signal = resample_to_uniform(tgt_times, tgt_brightness)

    # Cross-correlate
    print("\nCross-correlating brightness curves...")
    offset, confidence, corr = find_offset(ref_signal, tgt_signal)

    ref_name = Path(args.ref_frames).name
    tgt_name = Path(args.target_frames).name

    print(f"\n{'='*50}")
    print(f"  Offset     : {offset:+.0f} seconds")
    print(f"  Confidence : {confidence:.6f}")
    if offset >= 0:
        print(f"  → {tgt_name} started ~{offset:.0f}s AFTER {ref_name}")
    else:
        print(f"  → {tgt_name} started ~{abs(offset):.0f}s BEFORE {ref_name}")
    print(f"{'='*50}")
    print(f"\nNote: brightness-based alignment has ~1-2s precision.")
    print(f"Use this value as --mcu-offset in extract_synced_frames.py")
    print(f"Verify visually with: extract_synced_frames.py --verify 10")

    # Optional plot
    if args.save_plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, axes = plt.subplots(3, 1, figsize=(14, 8))

            axes[0].plot(ref_t, ref_signal, linewidth=0.5)
            axes[0].set_title(f"Reference brightness: {ref_name}")
            axes[0].set_ylabel("Mean brightness")

            axes[1].plot(tgt_t, tgt_signal, linewidth=0.5, color="orange")
            axes[1].set_title(f"Target brightness: {tgt_name}")
            axes[1].set_ylabel("Mean brightness")

            lags = np.arange(len(corr)) - (len(tgt_signal) - 1)
            axes[2].plot(lags, corr, linewidth=0.5, color="green")
            axes[2].axvline(x=offset, color="red", linestyle="--",
                            label=f"Peak: {offset:+.0f}s")
            axes[2].set_title("Cross-correlation")
            axes[2].set_xlabel("Lag (seconds)")
            axes[2].legend()

            plt.tight_layout()
            plt.savefig(args.save_plot, dpi=150)
            print(f"\nPlot saved to {args.save_plot}")
        except ImportError:
            print("matplotlib not available — skipping plot")


if __name__ == "__main__":
    main()
