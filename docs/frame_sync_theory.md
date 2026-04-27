# Frame Synchronization: Timestamp-Based Extraction and Error Analysis

## Problem Statement

Three cameras recorded the same burn experiment at different native frame rates:

| Camera | Native FPS | Frame Interval |
|--------|-----------|---------------|
| iPhone 14 | 30.00 fps | 33.333 ms |
| JVC Recorder | 29.97 fps (30000/1001) | 33.367 ms |
| MCU Stationary | ~25.31 fps | 39.510 ms |

To reconstruct 3D flame geometry from these videos, we need synchronized frame triplets — three frames (one per camera) that correspond to the same real-world moment. The challenge is that no single frame rate allows all three cameras to land on real frames simultaneously, because their native rates are not integer multiples of each other.

## Two Approaches to Multi-Camera Frame Extraction

### Approach A: Frame-Index-Based (WRONG)

Extract every Nth frame from each video:

```
iPhone:  frame 0, frame 30, frame 60, ...   → t = 0.000s, 1.000s, 2.000s, ...
JVC:     frame 0, frame 30, frame 60, ...   → t = 0.000s, 1.001s, 2.002s, ...
MCU:     frame 0, frame 25, frame 50, ...   → t = 0.000s, 0.987s, 1.975s, ...
```

The problem: because the frame intervals differ slightly, the extracted timestamps **drift apart over time**. After T seconds:

- iPhone–JVC drift: T × (1/29.97 − 1/30) × 30 = T × 0.001s per second
- iPhone–MCU drift: T × (1/25.31 − 1/30) × 25.31 = T × 0.013s per second

Over the full 997-second recording:

| Camera Pair | Accumulated Drift |
|-------------|-------------------|
| iPhone–JVC | ~1.0 second |
| iPhone–MCU | ~13.0 seconds |

This drift would make the later frames completely misaligned — unusable for 3D reconstruction.

### Approach B: Timestamp-Based (CORRECT — what we use)

Extract frames at **fixed timestamps** from each video independently, applying the calibrated time offset:

```
For output frame N at desired real-world time t:
    iPhone: extract frame nearest to t_iphone = t
    JVC:    extract frame nearest to t_jvc    = t − offset_jvc
    MCU:    extract frame nearest to t_mcu    = t − offset_mcu
```

Each camera independently seeks to the requested timestamp and returns the nearest real frame. The extraction rate (e.g., 1 fps) is applied uniformly across all cameras, but the actual frame selected comes from each camera's own frame grid.

This approach has **zero drift** because:
- Each frame is independently sought by absolute timestamp
- The offsets are constant — they don't accumulate error
- There is no dependency between consecutive frames

## Per-Frame Timing Error

The only source of error is the **quantization** to the nearest real frame. When we request a frame at timestamp t, ffmpeg returns the frame whose presentation timestamp (PTS) is closest to t. The maximum error is half the native frame interval:

| Camera | Max Timing Error |
|--------|-----------------|
| iPhone 14 (30 fps) | ±16.67 ms |
| JVC (29.97 fps) | ±16.68 ms |
| MCU (25.31 fps) | ±19.75 ms |

### Properties of this error

1. **Bounded**: The error can never exceed half a frame interval (~17–20 ms for our cameras).
2. **Non-accumulating**: Each frame's error is independent. Frame 500 is not affected by any error in frame 499.
3. **Random**: The error direction (early or late) varies per frame depending on where the requested timestamp falls relative to the camera's frame grid.
4. **Imperceptible for fire dynamics**: Fire shape changes occur on timescales of ~100–500 ms. A ±20 ms alignment error is 4–10% of one puffing cycle, which is within the spatial resolution limits of 3-camera visual hull reconstruction anyway.

### Worst-case pairwise timing mismatch

The maximum timing mismatch between any two cameras for a given output frame is the sum of their individual maximum errors:

| Camera Pair | Worst-Case Mismatch |
|-------------|---------------------|
| iPhone–JVC | ±33.35 ms |
| iPhone–MCU | ±36.42 ms |
| JVC–MCU | ±36.43 ms |

In practice, the actual mismatch is typically much smaller (average: ~half the worst case).

## Offset Calibration

The time offsets between cameras were determined by identifying a common visual event (a person's distinctive posture change) across camera views:

| Pair | Offset (seconds) | Meaning | Anchor Event |
|------|-------------------|---------|--------------|
| iPhone–MCU | −314.999 s | MCU started 315.0s before iPhone | Green shirt person standing up |
| iPhone–JVC | −8.037 s | JVC started 8.0s before iPhone | Fire burning, visual match |

These offsets are stored in `sync_config.json` and applied during extraction.

## Extraction Pipeline

Given the offsets, the synchronized extraction proceeds as:

```
Common overlap window (in iPhone time):
    start = max(0, offset_jvc, offset_mcu) = 0.0s
    end   = min(dur_iphone, offset_jvc + dur_jvc, offset_mcu + dur_mcu) = 997.5s

For each output frame at iPhone time t = 0, 1, 2, ..., 997:
    iPhone: ffmpeg -ss t         -i iPhone.MOV → frame
    JVC:    ffmpeg -ss (t + 8.037)  -i JVC.m2ts  → frame
    MCU:    ffmpeg -ss (t + 314.999) -i MCU.m4v   → frame
```

The output frame numbering is sequential (frame_000001.jpg, frame_000002.jpg, ...) with the mapping recorded in `sync_metadata.json`:

```
frame_number = N
iphone_time  = overlap_start + (N − 1) / fps
```

## Summary

- **Timestamp-based extraction eliminates drift** — no accumulated error regardless of recording length or frame rate differences.
- **Per-frame error is bounded at ±17–20 ms** — determined solely by each camera's native frame rate, independent of extraction rate.
- **The error does not accumulate** — frame 1 and frame 1000 have the same maximum error.
- **For fire reconstruction at 1 fps output**, the ±20 ms timing uncertainty is negligible compared to the ~100–500 ms timescale of fire shape dynamics and the spatial resolution limits of 3-camera visual hull.
