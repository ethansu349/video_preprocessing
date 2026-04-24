# video_preprocessing

Temporal synchronization and frame extraction for 3-camera burn footage.

## Problem

Three cameras (iPhone 14, JVC recorder, MCU stationary) recorded the same burn event but started/stopped independently and run at different native frame rates (30, 29.97, ~25.3 fps). This pipeline finds the time offsets between cameras and extracts synchronized frame triplets, with human-in-the-loop visual verification.

## Prerequisites

- `ffmpeg` / `ffprobe` (on HPC: `module load ffmpeg`)
- Python packages: `pip install numpy scipy opencv-python matplotlib`
- Activate env: `module load mamba && mamba activate 3dDynamics_YOLO`

## Quick Start — Interactive Pipeline

`sync_cameras.py` guides you through the full workflow: auto-compute offsets, visually verify with side-by-side images, adjust if needed, then extract synced frames.

```bash
BASE=/storage/project/r-jtaylor357-0/ysu349/Datasets/test_burn/test_burn_footages

python sync_cameras.py \
    --iphone "$BASE/iPhone14_FULL.MOV" \
    --jvc "$BASE/JVC_Recorder_FULL.m2ts" \
    --mcu "$BASE/MCU_Stationary_Camera_Full.m4v" \
    --iphone-frames "$BASE/iPhone_Frames" \
    --mcu-frames "$BASE/MCU_Stationary_Camera_Frames" \
    --output ./synced_frames
```

The script will:

1. **Auto-compute iPhone-JVC offset** via audio cross-correlation
2. **Save 5 side-by-side comparison images** to `./review/jvc/` — open them to check alignment
3. **Wait for your input**: Enter to accept, type a number to adjust, `+N`/`-N` for relative adjustment
4. **Auto-compute iPhone-MCU offset** via brightness cross-correlation (MCU has no audio)
5. **Same verification loop** for MCU
6. **Extract synchronized frame triplets** to `./synced_frames/{iphone,jvc,mcu}/`

If you already know the offsets (from a previous run or manual identification):

```bash
python sync_cameras.py \
    --iphone "$BASE/iPhone14_FULL.MOV" \
    --jvc "$BASE/JVC_Recorder_FULL.m2ts" \
    --mcu "$BASE/MCU_Stationary_Camera_Full.m4v" \
    --jvc-offset 12.5 --mcu-offset -45.0 \
    --output ./synced_frames
```

## Standalone Scripts

Individual steps can also be run independently:

```bash
# Audio offset only (iPhone ↔ JVC)
python find_offset_audio.py \
    --ref "$BASE/iPhone14_FULL.MOV" \
    --target "$BASE/JVC_Recorder_FULL.m2ts" \
    --save-plot audio_corr.png

# Brightness offset only (for MCU or verification)
python find_offset_brightness.py \
    --ref-frames "$BASE/iPhone_Frames" \
    --target-frames "$BASE/MCU_Stationary_Camera_Frames" \
    --ref-video "$BASE/iPhone14_FULL.MOV" \
    --target-video "$BASE/MCU_Stationary_Camera_Full.m4v" \
    --save-plot brightness_corr.png

# Extract frames with known offsets (non-interactive)
python extract_synced_frames.py \
    --iphone "$BASE/iPhone14_FULL.MOV" \
    --jvc "$BASE/JVC_Recorder_FULL.m2ts" \
    --mcu "$BASE/MCU_Stationary_Camera_Full.m4v" \
    --jvc-offset 12.5 --mcu-offset -45.0 \
    --output ./synced_frames --verify 1 50 100
```

## Manual Visual Event Identification

If brightness correlation gives a weak result for MCU, extract single frames at specific timestamps to find a common event (e.g., ignition):

```bash
ffmpeg -ss 120 -i "$BASE/MCU_Stationary_Camera_Full.m4v" -frames:v 1 -q:v 2 mcu_120s.jpg
ffmpeg -ss 60  -i "$BASE/iPhone14_FULL.MOV" -frames:v 1 -q:v 2 iphone_60s.jpg
```

## Output

```
synced_frames/
  iphone/frame_000001.jpg  frame_000002.jpg  ...
  jvc/frame_000001.jpg     frame_000002.jpg  ...
  mcu/frame_000001.jpg     frame_000002.jpg  ...
  sync_metadata.json       # offsets, overlap window, frame counts
```

Same frame index across all 3 directories = same moment in time.

## Camera Details

| Camera | Format | Resolution | Native FPS | Duration | Audio |
|--------|--------|-----------|-----------|----------|-------|
| iPhone 14 | HEVC / .MOV | 1920x1080 | 30 | ~997s | AAC 44.1kHz |
| JVC | MPEG2 / .m2ts | 1440x1080 | 29.97 | ~1091s | MP2 48kHz |
| MCU | H.264 / .m4v | 1920x1080 | ~25.3 | ~1802s | None |
