"""Find time offset between two videos using audio cross-correlation.

Extracts mono audio from both videos via ffmpeg, cross-correlates the
waveforms, and reports the time offset in seconds.

Positive offset = target started AFTER reference in real time.

Usage:
    python find_offset_audio.py \
        --ref /path/to/iPhone14_FULL.MOV \
        --target /path/to/JVC_Recorder_FULL.m2ts

    # With verification plot
    python find_offset_audio.py \
        --ref iPhone14_FULL.MOV --target JVC_Recorder_FULL.m2ts \
        --save-plot correlation.png
"""

import argparse
import subprocess
import tempfile
import numpy as np
from pathlib import Path


def extract_audio_mono(video_path, output_path, sample_rate=8000):
    """Extract mono audio from video as raw PCM float32 via ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",              # no video
        "-ac", "1",         # mono
        "-ar", str(sample_rate),
        "-f", "f32le",
        "-acodec", "pcm_f32le",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed:\n{result.stderr}")
    return np.fromfile(str(output_path), dtype=np.float32)


def find_offset(ref, target, sample_rate):
    """Cross-correlate two audio signals and return offset in seconds.

    Uses FFT-based cross-correlation. The offset convention:
      corr[k] = sum_n ref[n] * target[n - lag]   where lag = k - (len(target)-1)
    Peak at positive lag means the same event appears EARLIER in target's
    timeline, i.e., target started recording AFTER ref.

    Returns (offset_seconds, peak_correlation).
    """
    from scipy.signal import fftconvolve

    # Remove DC and convert to float64 for precision
    ref = (ref - np.mean(ref)).astype(np.float64)
    target = (target - np.mean(target)).astype(np.float64)

    # Normalize
    ref_std, target_std = np.std(ref), np.std(target)
    if ref_std > 0:
        ref /= ref_std
    if target_std > 0:
        target /= target_std

    corr = fftconvolve(ref, target[::-1], mode="full")

    peak_idx = np.argmax(np.abs(corr))
    peak_lag = peak_idx - (len(target) - 1)
    offset_seconds = peak_lag / sample_rate

    # Normalized peak value (rough confidence measure)
    peak_val = np.abs(corr[peak_idx]) / min(len(ref), len(target))

    return offset_seconds, peak_val, corr


def main():
    parser = argparse.ArgumentParser(
        description="Find time offset between two videos using audio cross-correlation."
    )
    parser.add_argument("--ref", required=True, help="Reference video path (e.g., iPhone)")
    parser.add_argument("--target", required=True, help="Target video path (e.g., JVC)")
    parser.add_argument(
        "--sample-rate", type=int, default=8000,
        help="Audio sample rate for analysis in Hz (default: 8000)",
    )
    parser.add_argument(
        "--save-plot", type=str, default=None,
        help="Save correlation plot to this path (requires matplotlib)",
    )
    args = parser.parse_args()

    ref_path = Path(args.ref)
    target_path = Path(args.target)
    sr = args.sample_rate

    print(f"Reference : {ref_path.name}")
    print(f"Target    : {target_path.name}")
    print(f"Sample rate: {sr} Hz\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        print("Extracting audio from reference...")
        ref_audio = extract_audio_mono(ref_path, tmp / "ref.raw", sr)
        print(f"  {len(ref_audio)/sr:.1f}s  ({len(ref_audio):,} samples)")

        print("Extracting audio from target...")
        target_audio = extract_audio_mono(target_path, tmp / "target.raw", sr)
        print(f"  {len(target_audio)/sr:.1f}s  ({len(target_audio):,} samples)")

    print("\nComputing cross-correlation (may take a moment)...")
    offset, confidence, corr = find_offset(ref_audio, target_audio, sr)

    print(f"\n{'='*50}")
    print(f"  Offset     : {offset:+.3f} seconds")
    print(f"  Confidence : {confidence:.6f}")
    if offset >= 0:
        print(f"  → {target_path.name} started {offset:.3f}s AFTER {ref_path.name}")
    else:
        print(f"  → {target_path.name} started {abs(offset):.3f}s BEFORE {ref_path.name}")
    print(f"{'='*50}")
    print(f"\nUse this value as --jvc-offset (or --mcu-offset) in extract_synced_frames.py")

    # Optional plot
    if args.save_plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            lags = (np.arange(len(corr)) - (len(target_audio) - 1)) / sr

            fig, axes = plt.subplots(3, 1, figsize=(14, 8))

            # Subsample waveforms for plotting
            step = max(1, len(ref_audio) // 8000)
            axes[0].plot(np.arange(0, len(ref_audio), step) / sr,
                         ref_audio[::step], linewidth=0.3)
            axes[0].set_title(f"Reference: {ref_path.name}")
            axes[0].set_ylabel("Amplitude")

            step = max(1, len(target_audio) // 8000)
            axes[1].plot(np.arange(0, len(target_audio), step) / sr,
                         target_audio[::step], linewidth=0.3, color="orange")
            axes[1].set_title(f"Target: {target_path.name}")
            axes[1].set_ylabel("Amplitude")

            step = max(1, len(corr) // 8000)
            axes[2].plot(lags[::step], corr[::step], linewidth=0.3, color="green")
            axes[2].axvline(x=offset, color="red", linestyle="--",
                            label=f"Peak: {offset:+.3f}s")
            axes[2].set_title("Cross-correlation")
            axes[2].set_xlabel("Lag (seconds)")
            axes[2].legend()

            plt.tight_layout()
            plt.savefig(args.save_plot, dpi=150)
            print(f"Plot saved to {args.save_plot}")
        except ImportError:
            print("matplotlib not available — skipping plot")


if __name__ == "__main__":
    main()
