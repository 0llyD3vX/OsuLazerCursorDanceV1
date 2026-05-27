# osu!relax v1.1.0

Advanced relax/assist bot for osu!lazer on Windows. Features predictive movement, stream detection, a dynamic arc system, and configurable click jitter for human-like patterns.

## IMPORTANT NOTE:
Leave CURSOR_SENS at value 1, i will remove this as an option in futuure updates but for now it is broken, the script will work no matter what sensitivity your playing at, have fun. :)


---

## Showcase (v0.1.0)

https://www.youtube.com/watch?v=qWbWlwr2LFs

---

## What's New in v1.0.0

- **Predictive movement**: anticipates note patterns for smoother cursor flow
- **Intelligent stream detection**: identifies stream patterns with a fast-path for very rapid streams (<50ms gaps)
- **Adaptive arc system**: dynamic arc amplitude based on note distance, with a simplified fallback for large jumps (>250 osu px)
- **Configurable click jitter**: structured timing variation (±9ms default) reduced automatically for fast notes (<90ms gap)
- **Stream smoothing**: configurable passes and alpha, with automatic reduction for large average movements
- **Relax mode toggle**: press `R` to switch between cursor-only and full auto mid-session
- **Auto-start**: automatically syncs when the map begins playing
- **Predictive direction calculation**: looks ahead up to 5 notes for better pathing
- **Fast jump handling**: dedicated busy-wait loop for segments under the fast jump threshold, with optional arc disabling
- **Improved slider handling**: better accuracy for all curve types (L, P, B, C) with pre-baked arc-length curves
- **Adaptive sleep**: hybrid smart sleep / busy-wait for sub-millisecond timing precision
- **Velocity cap disabled by default**: `MAX_CURSOR_DELTA_PX` set to 999999 to prevent missed fast jumps
- **Hit bias adjustment**: fine-tune tap timing with a configurable offset

---

## Requirements

- Windows (uses `SendInput` + `GetSystemMetrics`)
- Python 3.8+
- [tosu](https://github.com/KotRikD/tosu) running on `localhost:24050`

### Python dependencies

```bash
pip install -r requirements.txt
```

```
websocket-client
keyboard
```

tosu is a separate application. Grab it from its own repo and have it running before starting the bot.

Your folder should contain these 4 files:

```
relax.py
requirements.txt
README.md
(tosu running separately)
```

---

## Setup

**IMPORTANT: Set your screen resolution at the top of the script:**

```python
SCREEN_W = 2560  # your monitor width
SCREEN_H = 1440  # your monitor height
```

Optional adjustments:

- `PF_Y_OFFSET`: tweak if notes feel too high or too low
- `CURSOR_SENS`: adjust if cursor position feels off (1.52 is default)

Run `tosu.exe` and leave it running in the background (you can close the browser tab it opens), then:

```bash
python relax.py
```

---

## Hotkeys

| Key | Action |
|-----|--------|
| `Q` | Arm the bot (ready to auto-start) |
| `W` | Emergency stop, kills all movement immediately |
| `R` | Toggle RELAX\_MODE (cursor-only vs full auto) |

With `AUTO_START = True` (default), pressing `Q` arms the bot and it starts automatically when the map begins.

---

## Configuration

### Display Settings - Set These First

| Setting | Default | Description |
|---------|---------|-------------|
| `SCREEN_W` / `SCREEN_H` | `2560` / `1440` | **Must match your actual resolution, set to 2k by default** |
| `PF_HEIGHT_PCT` | `0.80` | Playfield height as fraction of screen |
| `PF_TOP_PCT` | `0.095` | Playfield top position |
| `PF_Y_OFFSET` | `15` | Fine-tune vertical alignment |
| `CURSOR_SENS` | `1.0` | Do not touch this, it will work no matter your sensitivity |

### Movement Tuning

| Setting | Default | Description |
|---------|---------|-------------|
| `MOVEMENT_MODE` | `"predictive"` | `linear` / `arc` / `predictive` |
| `ARC_MODE` | `True` | Enable arcs between notes |
| `ARC_MAX_AMPLITUDE` | `60` | Maximum arc offset in pixels |
| `ARC_MIN_AMPLITUDE` | `15` | Minimum arc offset in pixels |
| `ARC_MAX_DISTANCE` | `400` | Distance at which arc reaches max amplitude |
| `ARC_CYCLES` | `0.5` | Sine cycles per movement |
| `ARC_STREAM_THRESH_PX` | `100` | Distances below this suppress the arc |
| `ARC_EXP_BASE` | `0.12` | Arc falloff rate (leave as is) |
| `SPINNER_RPM` | `350` | Spinner angular speed |
| `SPINNER_RADIUS` | `90` | Spinner circle radius in pixels |

### Fast Jump Tuning

| Setting | Default | Description |
|---------|---------|-------------|
| `FAST_JUMP_THRESHOLD_MS` | `50` | Segments shorter than this are treated as fast jumps |
| `DISABLE_ARC_ON_FAST_JUMPS` | `True` | Suppress arc on very high speed segments |
| `DISABLE_JITTER_ON_FAST` | `True` | No click jitter on fast jumps |
| `MAX_JUMP_SPEED_PX_MS` | `15` | Arc disable threshold in pixels/ms |
| `USE_BUSY_WAIT_FOR_FAST` | `True` | Busy-wait loop for precise timing on fast segments |

### Features

| Setting | Default | Description |
|---------|---------|-------------|
| `RELAX_MODE` | `False` | `True` = cursor only, `False` = full auto |
| `AUTO_START` | `True` | Auto-sync when the map starts |
| `PREDICT_NOTES` | `5` | Notes ahead to predict (2-5 recommended) |
| `STREAM_MS_THRESH` | `250` | Timing threshold for stream detection (ms) |
| `STREAM_SMOOTH` | `True` | Enable stream path smoothing |
| `STREAM_SMOOTH_PASSES` | `2` | Smoothing iterations (1-3) |
| `STREAM_SMOOTH_ALPHA` | `0.28` | Smoothing strength (0-1) |
| `STREAM_SAMPLES_PER_OSUPX` | `0.4` | Stream sampling density |

### Timing & Input

| Setting | Default | Description |
|---------|---------|-------------|
| `SYNC_HOTKEY` | `q` | Arm the bot |
| `STOP_HOTKEY` | `w` | Emergency stop |
| `CLICK_KEYS` | `["z", "x"]` | Keys used for alternating taps |
| `HIT_BIAS_MS` | `-10` | Tap timing offset (negative = earlier) |
| `CLICK_VARIATION_MS` | `9.0` | Tap timing variation half-width (ms) |
| `GAME_OFFSET_MS` | `0` | Universal offset, leave at 0 |

### Performance Tuning

| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_CURSOR_DELTA_PX` | `999999` | Velocity cap per frame, effectively disabled to avoid missed fast jumps |
| `TIMING_SPIN_WINDOW` | `0.0005` | Final busy-wait window for precision (seconds) |
| `SEGMENT_MIN_DUR` | `0.005` | Minimum movement segment duration (seconds) |

---

## Recommended Presets

**Maximum accuracy:**
```python
MAX_CURSOR_DELTA_PX    = 999999
CLICK_VARIATION_MS     = 0
PREDICT_NOTES          = 3
STREAM_SMOOTH          = False
ARC_MODE               = False
USE_BUSY_WAIT_FOR_FAST = True
```

**Human-like / relaxed play:**
```python
ARC_MODE               = True
ARC_MAX_AMPLITUDE      = 60
CLICK_VARIATION_MS     = 9.0
STREAM_SMOOTH          = True
PREDICT_NOTES          = 5
```

**Low-end devices:**
```python
PREDICT_NOTES          = 2
STREAM_SMOOTH          = False
ARC_MODE               = False
USE_BUSY_WAIT_FOR_FAST = False
```

---

## How It Works

**tosu integration**: connects to the tosu WebSocket at `localhost:24050` for real-time map data.

**Beatmap parsing**: reads `.osu` files directly to extract hit objects, slider curves (L/P/B/C), and timing points.

**Timeline building**: pre-computes cursor positions and key events for the entire map before playback begins. Slider curves are baked into arc-length-parameterised point lists at parse time.

**Stream detection**: analyses note timing and spacing to identify streams. Has a fast-path for very rapid streams (<50ms gap) and rejects streams with large jumps (>150 osu px) between notes.

**Predictive movement**: looks ahead `PREDICT_NOTES` notes to anticipate direction changes. For very fast next notes (<50ms) the immediate direction is used instead.

**Fast jump handling**: segments shorter than `FAST_JUMP_THRESHOLD_MS` use a dedicated busy-wait loop for precise timing, with arc optionally disabled based on pixels-per-ms speed.

**Dual-thread execution**: cursor movement and key presses run on separate threads to avoid blocking each other.

**Precision timing**: hybrid `smart_sleep_until` / busy-wait for sub-millisecond accuracy. The final 2ms of every segment always busy-waits.

**Direct input**: uses `SendInput` for the lowest-latency mouse and keyboard events possible.

### Movement Types

| Mode | Behaviour |
|------|-----------|
| `linear` | Straight line with smooth cubic easing |
| `arc` | Sine-wave perpendicular oscillation |
| `predictive` | Arc with look-ahead direction prediction, falls back to simple arc for jumps >250 osu px |

### Supported Curve Types

| Type | Description |
|------|-------------|
| `L` | Linear, straight line segments |
| `P` | Perfect circle, circular arc through 3 points |
| `B` | Bezier, single or compound curves with repeat-point splitting |
| `C` | Catmull-Rom splines |

---

## Troubleshooting

**Cursor feels laggy or slow on fast jumps**
- Ensure `MAX_CURSOR_DELTA_PX` is `999999` (default)
- Enable `USE_BUSY_WAIT_FOR_FAST`
- Disable `ARC_MODE` for direct movement
- Reduce `PREDICT_NOTES` to 2-3

**Tapping feels early or late**
- Adjust `HIT_BIAS_MS` (more negative = earlier taps)
- Lower `CLICK_VARIATION_MS` to reduce jitter

**Notes being hit too high or too low**
- Adjust `PF_Y_OFFSET` (increase = lower, decrease = higher)
- Check `PF_TOP_PCT` and `PF_HEIGHT_PCT`

**Bot doesn't start automatically**
- Make sure `AUTO_START = True`
- Press `Q` to arm before the map starts
- Verify tosu is running and connected

**Streams feel choppy**
- Enable `STREAM_SMOOTH`
- Increase `STREAM_SMOOTH_PASSES` to 2-3
- Lower `STREAM_SMOOTH_ALPHA` to 0.2-0.3

**High CPU usage**
- Reduce `PREDICT_NOTES` to 2
- Disable `STREAM_SMOOTH`
- Set `USE_BUSY_WAIT_FOR_FAST = False`

---

## Notes

- **osu!lazer only**, does not work with osu!stable
- Don't use this on ranked or multiplayer
- I'm not responsible for any bans or consequences
- Leave `GAME_OFFSET_MS` at 0 unless you know what you're doing
- `EARLY_HIT_MS` is deprecated, use `HIT_BIAS_MS` instead

---

## License

Do whatever you want with it, just don't use it to cheat on leaderboards. This is for personal education and experimentation only.
