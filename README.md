# osu!relax V1 movement build (v1.3.0)

Windows-only osu!lazer input automation. It reads the current beatmap and live
clock from Tosu and sends cursor and key input. **Do not use in ranked or
multiplayer games; check osu!'s rules before using.**

## Setup

1. Install Python 3.8 or newer and start [Tosu](https://github.com/KotRikD/tosu).
2. Check and edit the settings at the top of `mainV1.py`, especially your
   display/playfield dimensions and timing values.
3. Install dependencies and launch:

   ```powershell
   python -m pip install -r requirements.txt
   python mainV1.py
   ```

Tosu's WebSocket should be available at `localhost:24050`. Tosu must be able to
locate the selected `.osu` file in your songs folder.

## Controls and timing

- Press **Q** when the music starts to arm and sync the run.
- Press **W** to cancel or stop.
- If timing is off, adjust `GAME_OFFSET_MS` for that song. Sync is not perfect;
  the 16 ms map-delay compensation is a starting point, not a guarantee.

The script uses `z` and `x` for note input by default. Cursor scale is fixed at
1.0. See `LICENSE` for license terms.
