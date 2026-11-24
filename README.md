# osu!relax v1.2.0

Windows-only osu!lazer input automation experiment. It reads live beatmap data
from Tosu and schedules cursor movement and key presses. **Do not use it in
ranked or multiplayer games.** Check osu!'s rules before using it; this project
may result in account penalties.

The current release is [`mainV120.py`](mainV120.py).

## Requirements

- Windows
- Python 3.8+
- [Tosu](https://github.com/KotRikD/tosu), running with its WebSocket on
  `localhost:24050`

Install the Python dependencies and start the script:

```powershell
python -m pip install -r requirements.txt
python mainV120.py
```

Start Tosu before the script. The script reads `.osu` files from the songs
folder Tosu reports; it does not include Tosu or a beatmap.

## Controls and modes

| Key | Action |
| --- | --- |
| `Q` | Arm; the script starts when Tosu reports a live map |
| `W` | Emergency stop |
| `R` | Toggle tap-only mode for the next run |

At the top of `mainV120.py`, choose a mode:

- `MODE_RELAX = False`, `MODE_MAGNET = False`: full automation (default).
- `MODE_RELAX = True`: taps notes without moving the cursor.
- `MODE_MAGNET = True`: cursor-only near-note assistance; does not tap.

Do not enable both special modes. `R` is ignored in magnet mode.

## Configuration

Set `SCREEN_W` and `SCREEN_H` to your display resolution. The defaults are
2560×1440. Adjust `PF_HEIGHT_PCT`, `PF_TOP_PCT`, and `PF_Y_OFFSET` if the
playfield mapping is misaligned. Timing options include `HIT_BIAS_MS`,
`MAP_DELAY_COMPENSATION_MS`, and `TIMING_WINDOW_MS`.

Tosu can provide the current mouse sensitivity and mod-adjusted circle size.
Magnet mode requires osu!'s **High precision mouse** (raw input) to be off so
the Windows cursor position can be used.

## Features

- Parses standard `.osu` hit objects, timing points, and slider paths.
- Supports linear, perfect-circle, Bezier, and Catmull-Rom slider curves.
- Synchronizes to Tosu's live map clock and speed-changing mods.
- Provides predictive movement, slider tracking, spinner movement, and
  configurable timing and movement behavior.

## Troubleshooting

- **It does not start:** confirm Tosu is running, the WebSocket is available
  on port `24050`, and press `Q` before or during a map.
- **Playfield alignment is off:** verify `SCREEN_W`/`SCREEN_H`, then adjust
  the playfield settings.
- **Magnet mode does not follow the cursor:** disable High precision mouse.
- **Timing feels early or late:** adjust `HIT_BIAS_MS` and
  `MAP_DELAY_COMPENSATION_MS` in small increments.

## License

MIT. See [LICENSE](LICENSE).
