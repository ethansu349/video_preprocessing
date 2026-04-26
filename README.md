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

## Finding the Anchor Frame — `peek_frames.py`

To verify alignment accurately, you need a distinctive visual event (e.g., the ignition moment) rather than generic fire frames. `peek_frames.py` lets you browse video frames to find that moment using a two-pass approach. Output goes to `peek/{iphone,jvc,mcu}/` automatically based on filename.

### Environment setup

```bash
module load mamba && mamba activate 3dDynamics_YOLO && module load ffmpeg
```

### Pass 1: Coarse scan (1fps) — find the rough second

Scan a wide range at 1 frame per second. Run all three:

```bash
python peek_frames.py --video /storage/project/r-jtaylor357-0/ysu349/Datasets/test_burn/test_burn_footages/FULL_Videos/iPhone14_FULL.MOV --time 30 --range 30
```

```bash
python peek_frames.py --video /storage/project/r-jtaylor357-0/ysu349/Datasets/test_burn/test_burn_footages/FULL_Videos/JVC_Recorder_FULL.m2ts --time 30 --range 30
```

```bash
python peek_frames.py --video /storage/project/r-jtaylor357-0/ysu349/Datasets/test_burn/test_burn_footages/FULL_Videos/MCU_Stationary_Camera_Full.m4v --time 30 --range 30
```

- `--time 30` — your best guess of when ignition happens (30s into the video)
- `--range 30` — look ±30 seconds around that guess (t=0s to t=60s)
- No `--native-fps` — extracts 1 frame per second (default)

Produces ~60 frames per camera in `peek/iphone/`, `peek/jvc/`, `peek/mcu/`. Click through them in VS Code's file explorer to find the rough second where ignition occurs.

### Pass 2: Fine scan (native fps) — pinpoint the exact frame

Replace `<TIME>` with the rough second found in pass 1 for each camera:

```bash
python peek_frames.py --video /storage/project/r-jtaylor357-0/ysu349/Datasets/test_burn/test_burn_footages/FULL_Videos/iPhone14_FULL.MOV --time <TIME> --range 2 --native-fps
```

```bash
python peek_frames.py --video /storage/project/r-jtaylor357-0/ysu349/Datasets/test_burn/test_burn_footages/FULL_Videos/JVC_Recorder_FULL.m2ts --time <TIME> --range 2 --native-fps
```

```bash
python peek_frames.py --video /storage/project/r-jtaylor357-0/ysu349/Datasets/test_burn/test_burn_footages/FULL_Videos/MCU_Stationary_Camera_Full.m4v --time <TIME> --range 2 --native-fps
```

- `--time <TIME>` — the rough second from pass 1
- `--range 2` — narrow window: ±2 seconds
- `--native-fps` — all frames at native rate (30fps iPhone, 29.97fps JVC, 25.3fps MCU)

Produces ~120 frames per camera at ~0.033s intervals. Find the exact ignition frame (e.g., `t_0117.933s.jpg`).

### Using the result

The timestamp from the iPhone filename feeds directly into the sync pipeline:

```bash
python sync_cameras.py --ref-time 117.933 --iphone ... --jvc ... --mcu ... --output ./synced_frames
```

This anchors the HTML verification viewer at the ignition moment, making it easy to match across cameras.

### Parameters

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--video` | str | required | Path to video file |
| `--time` | float | required | Center timestamp in seconds |
| `--range` | float | 10 | Half-range: extracts [time-range, time+range] |
| `--native-fps` | flag | off | Extract at native frame rate instead of 1fps |
| `--name` | str | auto | Camera subfolder name (auto-detected from filename) |
| `--output` | str | `./peek` | Base output directory (default: ./peek) |

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
