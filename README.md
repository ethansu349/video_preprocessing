# video_preprocessing

Temporal synchronization and frame extraction for 3-camera burn footage.

## Problem

Three cameras (iPhone 14, JVC recorder, MCU stationary) recorded the same burn event but started/stopped independently and run at different native frame rates (30, 29.97, ~25.3 fps). This pipeline finds the time offsets between cameras and extracts synchronized frame triplets, with human-in-the-loop visual verification.

## Prerequisites

- `ffmpeg` / `ffprobe` (on HPC: `module load ffmpeg`)
- Python packages: `pip install numpy scipy opencv-python`
- Activate env: `module load mamba && mamba activate 3dDynamics_YOLO`

## Quick Start — Interactive Pipeline

```bash
VIDS=/storage/project/r-jtaylor357-0/ysu349/Datasets/test_burn/test_burn_footages/FULL_Videos
FRAMES=/storage/project/r-jtaylor357-0/ysu349/Datasets/test_burn/test_burn_footages/achive_frames

python sync_cameras.py \
    --iphone "$VIDS/iPhone14_FULL.MOV" \
    --jvc "$VIDS/JVC_Recorder_FULL.m2ts" \
    --mcu "$VIDS/MCU_Stationary_Camera_Full.m4v" \
    --iphone-frames "$FRAMES/iPhone_Frames" \
    --mcu-frames "$FRAMES/MCU_Stationary_Camera_Frames" \
    --output ./synced_frames
```

### What happens

1. **Auto-computes iPhone-JVC offset** via audio cross-correlation
2. **Generates a self-contained HTML viewer** (`review/jvc/index.html`, ~5 MB, all images embedded)
3. **You open it in a browser** — iPhone frame on the left (fixed), JVC frame on the right (navigable via arrow keys / slider). The current offset is shown at the top.
4. **You find the matching frame**, note the offset value, type it in the terminal (or press Enter to accept the candidate)
5. **Same process for MCU** (uses brightness cross-correlation since MCU has no audio)
6. **Extracts synchronized frame triplets** to `synced_frames/{iphone,jvc,mcu}/`

### Viewer controls

| Input | Action |
|-------|--------|
| `←` / `→` | Step ±1 second |
| `Shift + ←` / `→` | Step ±5 seconds |
| Slider | Jump to any position |
| Buttons | `« -5s` `‹ -1s` `+1s ›` `+5s »` |

### Terminal prompt

After reviewing the viewer:

| Input | Action |
|-------|--------|
| `Enter` | Accept the candidate offset |
| `14.0` | Set this as the new offset (regenerates viewer to fine-tune) |
| `r` | Regenerate with wider range (±40s, then ±80s, up to ±120s) |
| `q` | Quit |

### Skip auto-detection

If you already know the offsets:

```bash
python sync_cameras.py \
    --iphone "$VIDS/iPhone14_FULL.MOV" \
    --jvc "$VIDS/JVC_Recorder_FULL.m2ts" \
    --mcu "$VIDS/MCU_Stationary_Camera_Full.m4v" \
    --jvc-offset 12.5 --mcu-offset -45.0 \
    --output ./synced_frames
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

## Data Access

All video reads are **read-only**. No files are created in the Datasets directory.

- **Raw videos (READ-ONLY)**: `.../test_burn_footages/FULL_Videos/`
- **Archive frames (READ-ONLY)**: `.../test_burn_footages/achive_frames/` — used only for brightness cross-correlation (MCU offset)
- **All outputs**: written to `video_preprocessing/review/` and `video_preprocessing/synced_frames/`

## Camera Details

| Camera | Format | Resolution | Native FPS | Duration | Audio |
|--------|--------|-----------|-----------|----------|-------|
| iPhone 14 | HEVC / .MOV | 1920x1080 | 30 | ~997s | AAC 44.1kHz |
| JVC | MPEG2 / .m2ts | 1440x1080 | 29.97 | ~1091s | MP2 48kHz |
| MCU | H.264 / .m4v | 1920x1080 | ~25.3 | ~1802s | None |
