# osu!relax V1 movement build (v1.3.0)

Windows-only osu!lazer input automation. It reads the current beatmap and live
clock from Tosu and sends cursor and key input. **Do not use in ranked or
multiplayer games; check osu!'s rules before using.**

## Setup

Install Python 3.8 or newer. Before starting, check and edit the settings at
the top of `mainV1.py`, especially your display/playfield dimensions and timing
values. Install the Python dependencies once:

```powershell
python -m pip install -r requirements.txt
```

Start each run in this order:

1. Open osu!lazer.
2. Open `tosu.exe` and make sure its WebSocket is available at
   `localhost:24050` (it should do this automatically!)
3. From this folder, run `python mainV1.py`.
4. When the music starts, press **Q** to arm and sync the run.
5. If it isnt synced, adjust in game song offset as the sync isn't and can't be made perfect.

Tosu must be able to locate the selected `.osu` file in your songs folder.

## Controls and timing

- Press **W** to cancel or stop.
- If timing is off, adjust `GAME_OFFSET_MS` for that song. Sync is not perfect;
  the 16 ms map-delay compensation is a starting point, not a guarantee.

The script uses `z` and `x` for note input by default. Cursor scale is fixed at
1.0. See `LICENSE` for license terms.
