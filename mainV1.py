import sys
sys.stdout.reconfigure(encoding='utf-8')

import bisect
import ctypes
import ctypes.wintypes
import json
import math
import os
import threading
import time
import websocket
import keyboard

RELEASE_NAME = "osu!relax V1 movement build"
RELEASE_VERSION = "1.3.0"

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

################# START OF CONFIG ##############################################

# ============================= QUICK SETUP ===================================
# Press Q to arm before starting a map; W stops the bot.
SYNC_HOTKEY = "q"
STOP_HOTKEY = "w"
CLICK_KEYS = ["z", "x"]
AUTO_START = True

# Timing. Tune map compensation only from repeatable timing measurements.
GAME_OFFSET_MS = 0
HIT_BIAS_MS = -10
CLICK_VARIATION_MS = 9.0
MAP_DELAY_COMPENSATION_MS = 16.0  # externel delay (add this ONTOP of your game offset, only edit this after editing game offset)

# shouldnt have to worry about these
AUTO_START_MIN_LIVE_MS = 5.0
OSU_MIN_LEAD_IN_MS = 1800.0
AUTO_PRESTART_CLOCK_MAX_MS = 50.0

# Display / playfield
SCREEN_W = 2560
SCREEN_H = 1440

# shouldnt have to worry about these either
PF_HEIGHT_PCT = 0.80
PF_TOP_PCT = 0.095
PF_Y_OFFSET = 15
OSU_W, OSU_H = 512, 384
USE_VIRTUAL_DESK = False

# Cursor scale is fixed; no sensitivity compensation is applied. [DONT CHANGE THIS]
CURSOR_SENS = 1.0

# Tosu connection
TOSU_WS_URL = "ws://localhost:24050/websocket/v2"

# ============================ ADVANCED TUNING ================================
# Arc / movement
ARC_MODE           = True      # sine-wave arc between notes
ARC_AMPLITUDE      = 30        # pixels perpendicular oscillation
ARC_CYCLES         = 0.5       # sine cycles per inter-note segment
ARC_STREAM_THRESH_PX = 100     # osu-px distance below which arc is skipped

# Spinner
SPINNER_RPM    = 390
SPINNER_RADIUS = 110

# Stream detection
STREAM_MS_THRESH = 250         # max ms gap between notes to count as a stream
STREAM_MIN_NOTES = 4

# Movement features
PREDICT_NOTES  = 5             # notes ahead to look for direction prediction

# Fast-jump tweaks
FAST_JUMP_THRESHOLD_MS    = 50
DISABLE_ARC_ON_FAST_JUMPS = True
MAX_JUMP_SPEED_PX_MS      = 15

################# END OF CONFIG ##############################################

# Internal constants
FLOAT_EPSILON          = 1e-3
ARCLENGTH_EPSILON      = FLOAT_EPSILON
SEGMENT_MIN_DUR        = 0.005
SLIDER_STEP_FACTOR     = 1.6
SLIDER_BASE_STEP_ADD   = 80
SLIDER_MIN_STEPS       = 120
SLIDER_MAX_STEPS       = 6000
OSU_OOB_MARGIN         = 96.0
TIMING_SPIN_WINDOW     = 0.0005
CIRCLE_DET_THRESHOLD   = FLOAT_EPSILON

# ─────────────────────────── Playfield ────────────────────────────────────────

def _pf():
    h = SCREEN_H * PF_HEIGHT_PCT
    w = h * (OSU_W / OSU_H)
    l = (SCREEN_W - w) / 2
    t = SCREEN_H * PF_TOP_PCT + PF_Y_OFFSET
    return l, t, w, h

PF_LEFT, PF_TOP, PF_W, PF_H = _pf()


def osu_to_screen(ox, oy):
    ox = max(-OSU_OOB_MARGIN, min(float(OSU_W) + OSU_OOB_MARGIN, float(ox)))
    oy = max(-OSU_OOB_MARGIN, min(float(OSU_H) + OSU_OOB_MARGIN, float(oy)))
    tx = PF_LEFT + (ox / OSU_W) * PF_W
    ty = PF_TOP  + (oy / OSU_H) * PF_H
    return tx, ty


def screen_to_osu(sx, sy):
    return (sx - PF_LEFT) / PF_W * OSU_W, (sy - PF_TOP) / PF_H * OSU_H


# ─────────────────────────── Math helpers ─────────────────────────────────────

def lerp(a, b, t):
    return a + (b - a) * t


def smooth_easing(t):
    """Cubic ease-in-out (identical in both v1 and v2)."""
    t = max(0.0, min(1.0, t))
    return 3 * t * t - 2 * t * t * t


# ─────────────────────────── Arc / movement ───────────────────────────────────

def apply_arc(elapsed, dt, x1, y1, x2, y2):
    """
    V1-style arc: smooth-eased position + sine-wave perpendicular offset.
    Simple, reliable, consistent — this is what made v1 hit better.
    """
    if dt <= 0:
        return x1, y1
    phase = max(0.0, min(1.0, elapsed / dt))
    dx = x2 - x1
    dy = y2 - y1
    dist = math.sqrt(dx * dx + dy * dy)

    eased = smooth_easing(phase)
    x = x1 + dx * eased
    y = y1 + dy * eased

    if dist > 0:
        perp_x = -dy / dist
        perp_y =  dx / dist
        wave   = math.sin(phase * 2 * math.pi * ARC_CYCLES)
        offset = wave * ARC_AMPLITUDE
        x += perp_x * offset
        y += perp_y * offset

    return x, y


# ─────────────────────────── Curve sampling (v2 exact) ────────────────────────

def _clamp_osu(pt):
    x, y = pt
    x = max(-OSU_OOB_MARGIN, min(float(OSU_W) + OSU_OOB_MARGIN, float(x)))
    y = max(-OSU_OOB_MARGIN, min(float(OSU_H) + OSU_OOB_MARGIN, float(y)))
    return x, y


def evaluate_bezier(points, t):
    pts = [(float(x), float(y)) for x, y in points]
    while len(pts) > 1:
        pts = [(lerp(pts[i][0], pts[i+1][0], t),
                lerp(pts[i][1], pts[i+1][1], t))
               for i in range(len(pts) - 1)]
    return pts[0]


def sample_polyline_linear(points, steps):
    if not points:
        return []
    if len(points) == 1:
        return [points[0]] * max(2, steps)
    if steps < 2:
        return [points[0], points[-1]]
    total = len(points) - 1
    out   = []
    for i in range(steps):
        raw_t  = i / (steps - 1)
        scaled = raw_t * total
        idx    = min(int(scaled), total - 1)
        lt     = scaled - idx
        x1, y1 = points[idx]
        x2, y2 = points[idx + 1]
        out.append((lerp(x1, x2, lt), lerp(y1, y2, lt)))
    return out


def sample_curve(curve_type, points, steps=40, pixel_length=None):
    """Full v2 curve sampler: L / P (perfect circle) / C (catmull-rom) / B (bezier segments)."""
    if not points or len(points) < 2:
        return [_clamp_osu(p) for p in points[:1]]
    if steps < 2:
        return [_clamp_osu(p) for p in points]

    curve_type = (curve_type or "B").upper()

    # ── Linear ────────────────────────────────────────────────────────────────
    if curve_type == "L":
        out   = []
        total = len(points) - 1
        for i in range(steps):
            raw_t  = i / (steps - 1)
            scaled = raw_t * total
            idx    = min(int(scaled), total - 1)
            lt     = scaled - idx
            x1, y1 = _clamp_osu(points[idx])
            x2, y2 = _clamp_osu(points[idx + 1])
            out.append((lerp(x1, x2, lt), lerp(y1, y2, lt)))
        return out

    # ── Perfect circle (3-point arc) ──────────────────────────────────────────
    elif curve_type == "P" and len(points) == 3:
        p0 = _clamp_osu(points[0])
        p1 = _clamp_osu(points[1])
        p2 = _clamp_osu(points[2])
        a_x, a_y = p0
        b_x, b_y = p1
        c_x, c_y = p2

        det = (b_y - a_y) * (c_x - a_x) - (b_x - a_x) * (c_y - a_y)
        if abs(det) < 1e-6:
            return sample_curve("L", [p0, p2], steps)

        d    = 2.0 * (a_x*(b_y-c_y) + b_x*(c_y-a_y) + c_x*(a_y-b_y))
        a_sq = a_x*a_x + a_y*a_y
        b_sq = b_x*b_x + b_y*b_y
        c_sq = c_x*c_x + c_y*c_y
        cx   = (a_sq*(b_y-c_y) + b_sq*(c_y-a_y) + c_sq*(a_y-b_y)) / d
        cy   = (a_sq*(c_x-b_x) + b_sq*(a_x-c_x) + c_sq*(b_x-a_x)) / d

        dA_x = a_x - cx; dA_y = a_y - cy
        dC_x = c_x - cx; dC_y = c_y - cy
        r    = math.hypot(dA_x, dA_y)
        if r <= 1e-6:
            return sample_curve("L", [p0, p2], steps)

        theta_start = math.atan2(dA_y, dA_x)
        theta_end   = math.atan2(dC_y, dC_x)
        while theta_end < theta_start:
            theta_end += 2.0 * math.pi

        direction   = 1.0
        theta_range = theta_end - theta_start
        ortho_x     = c_y - a_y
        ortho_y     = -(c_x - a_x)
        dot         = ortho_x*(b_x-a_x) + ortho_y*(b_y-a_y)
        if dot < 0:
            direction    = -1.0
            theta_range  = 2.0 * math.pi - theta_range

        total_angle = direction * theta_range
        max_angle   = 2.5 * math.pi
        if abs(total_angle) > max_angle:
            total_angle = math.copysign(max_angle, total_angle)

        curved_steps = min(max(steps, int(abs(total_angle)*r*0.6)), SLIDER_MAX_STEPS)
        out = []
        for i in range(curved_steps):
            t = i / (curved_steps - 1)
            a = theta_start + t * total_angle
            out.append(_clamp_osu((cx + r*math.cos(a), cy + r*math.sin(a))))
        if curved_steps != steps:
            out = sample_polyline_linear(out, steps)
        return out

    # ── Catmull-Rom ────────────────────────────────────────────────────────────
    elif curve_type == "C":
        pts = [(float(x), float(y)) for x, y in points]
        if len(pts) < 2:
            return pts
        ext       = [pts[0]] + pts + [pts[-1]]
        seg_count = max(1, len(pts) - 1)

        def catmull_rom(t, p0, p1, p2, p3):
            t2 = t*t; t3 = t2*t
            x = 0.5*((2*p1[0]) + (-p0[0]+p2[0])*t + (2*p0[0]-5*p1[0]+4*p2[0]-p3[0])*t2 + (-p0[0]+3*p1[0]-3*p2[0]+p3[0])*t3)
            y = 0.5*((2*p1[1]) + (-p0[1]+p2[1])*t + (2*p0[1]-5*p1[1]+4*p2[1]-p3[1])*t2 + (-p0[1]+3*p1[1]-3*p2[1]+p3[1])*t3)
            return x, y

        out = []
        for i in range(steps):
            raw_t  = i / (steps - 1)
            s      = raw_t * seg_count
            seg    = min(int(s), seg_count - 1)
            lt     = s - seg if seg < seg_count else 1.0
            x, y   = catmull_rom(lt, ext[seg], ext[seg+1], ext[seg+2], ext[seg+3])
            out.append(_clamp_osu((x, y)))
        return out

    # ── Bezier (with red-anchor sub-segments) ──────────────────────────────────
    else:  # B or fallback
        segments  = []
        seg_start = 0
        for j in range(len(points) - 1):
            if points[j] == points[j + 1]:
                seg = points[seg_start : j + 1]
                if len(seg) >= 2:
                    segments.append(seg)
                seg_start = j
        tail = points[seg_start:]
        if len(tail) >= 2:
            segments.append(tail)
        if not segments:
            segments = [points]

        def _rough_len(sp):
            acc = 0.0
            for a in range(1, len(sp)):
                acc += math.hypot(float(sp[a][0])-float(sp[a-1][0]),
                                  float(sp[a][1])-float(sp[a-1][1]))
            return acc

        seg_lens = [_rough_len(s) for s in segments]
        total_len = sum(seg_lens)

        if total_len <= ARCLENGTH_EPSILON:
            seg_steps_list = [max(4, steps // max(1, len(segments)))] * len(segments)
        else:
            base = [max(4, int(round(steps * (l / total_len)))) for l in seg_lens]
            seg_steps_list = base
            diff  = steps - sum(seg_steps_list)
            order = sorted(range(len(seg_steps_list)), key=lambda i: seg_lens[i], reverse=True)
            oi    = 0
            while diff != 0 and order:
                idx = order[oi % len(order)]
                if diff > 0:
                    seg_steps_list[idx] += 1; diff -= 1
                elif seg_steps_list[idx] > 4:
                    seg_steps_list[idx] -= 1; diff += 1
                oi += 1

        out = []
        for seg_idx, seg_pts in enumerate(segments):
            seg_steps = seg_steps_list[seg_idx] if seg_idx < len(seg_steps_list) else max(4, steps // max(1, len(segments)))
            seg_out   = []
            for k in range(seg_steps):
                t     = k / (seg_steps - 1)
                px, py = evaluate_bezier(seg_pts, t)
                seg_out.append(_clamp_osu((px, py)))
            out.extend(seg_out if seg_idx == 0 else seg_out[1:])

        if len(out) < 2:
            return out
        if len(out) != steps:
            out = sample_polyline_linear(out, steps)
        return out


# ─────────────────────────── Arc-length helpers (v2 exact) ────────────────────

def polyline_arclengths(curve):
    if not curve:
        return [], 0.0
    cum = [0.0]
    for k in range(1, len(curve)):
        x0, y0 = curve[k-1]
        x1, y1 = curve[k]
        cum.append(cum[-1] + math.hypot(float(x1)-float(x0), float(y1)-float(y0)))
    return cum, cum[-1]


def sample_curve_by_arclength(curve, cumdists, total_len, u):
    if not curve:
        return 0.0, 0.0
    u = max(0.0, min(1.0, u))
    if len(curve) == 1 or total_len <= ARCLENGTH_EPSILON:
        x, y = curve[0]; return float(x), float(y)
    target = u * total_len
    if target >= total_len - ARCLENGTH_EPSILON:
        x, y = curve[-1]; return float(x), float(y)
    j = bisect.bisect_right(cumdists, target) - 1
    j = max(0, min(j, len(curve) - 2))
    d0, d1  = cumdists[j], cumdists[j+1]
    seg_len = d1 - d0
    if seg_len <= ARCLENGTH_EPSILON:
        x, y = curve[j]; return float(x), float(y)
    lt = (target - d0) / seg_len
    x0, y0 = curve[j]; x1, y1 = curve[j+1]
    return lerp(float(x0), float(x1), lt), lerp(float(y0), float(y1), lt)


def truncate_curve_to_length(curve, length_osu_px):
    if not curve:
        return [], [], 0.0
    cum_dist, total_len = polyline_arclengths(curve)
    if length_osu_px is None or length_osu_px <= 0 or total_len <= ARCLENGTH_EPSILON:
        return curve, cum_dist, total_len
    target = min(float(length_osu_px), total_len)
    if target >= total_len - ARCLENGTH_EPSILON:
        return curve, cum_dist, total_len
    j = bisect.bisect_right(cum_dist, target) - 1
    j = max(0, min(j, len(curve) - 2))
    d0, d1  = cum_dist[j], cum_dist[j+1]
    seg_len = d1 - d0
    if seg_len <= ARCLENGTH_EPSILON:
        trimmed = curve[:j+2]
        nc, nt  = polyline_arclengths(trimmed)
        return trimmed, nc, nt
    lt      = (target - d0) / seg_len
    x0, y0  = curve[j]; x1, y1 = curve[j+1]
    end_pt  = (lerp(float(x0), float(x1), lt), lerp(float(y0), float(y1), lt))
    trimmed  = list(curve[:j+1]) + [end_pt]
    new_cum  = list(cum_dist[:j+1]) + [target]
    return trimmed, new_cum, target


# ─────────────────────────── Precision sleep (v1 style) ───────────────────────

def sleep_until(wall, stop_event=None):
    """V1-style: sleep until 2ms before, then spin. Simple and accurate."""
    while True:
        if stop_event is not None and stop_event.is_set():
            return False
        remaining = wall - time.perf_counter()
        if remaining <= 0:
            return True
        if remaining > 0.005:
            time.sleep(remaining - 0.002)


# ─────────────────────────── .osu parsing (v2 exact) ─────────────────────────

def parse_timing_points(lines):
    pts, in_sec = [], False
    for line in lines:
        s = line.strip()
        if s == "[TimingPoints]":
            in_sec = True; continue
        if in_sec:
            if s.startswith("["):
                break
            if not s:
                continue
            p = s.split(",")
            if len(p) < 2:
                continue
            try:
                offset, beat_len = float(p[0]), float(p[1])
                if beat_len > 0:
                    pts.append({"offset": offset, "ms_per_beat": beat_len,
                                "sv_mult": 1.0, "inherited": False})
                else:
                    pts.append({"offset": offset, "ms_per_beat": None,
                                "sv_mult": -100.0 / beat_len, "inherited": True})
            except ValueError:
                continue
    pts.sort(key=lambda x: x["offset"])
    return pts


def get_timing_at(pts, t):
    mpb, sv = 500.0, 1.0
    for p in pts:
        if p["offset"] > t:
            break
        if not p["inherited"]:
            mpb = p["ms_per_beat"]
            sv  = 1.0
        else:
            sv  = p["sv_mult"]
    return mpb, sv


def slider_duration(t, length, smult, pts):
    mpb, sv = get_timing_at(pts, t)
    return (length / (smult * 100.0 * sv)) * mpb


def parse_osu_file(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    smult = 1.4
    for line in lines:
        if line.strip().startswith("SliderMultiplier"):
            try:
                smult = float(line.split(":")[1].strip())
            except (ValueError, IndexError):
                pass
            break

    pts  = parse_timing_points(lines)
    objs = []
    in_h = False

    for line in lines:
        s = line.strip()
        if s == "[HitObjects]":
            in_h = True; continue
        if not in_h:
            continue
        if s.startswith("["):
            break
        if not s:
            continue
        p = s.split(",")
        if len(p) < 5:
            continue
        try:
            ox, oy = int(p[0]), int(p[1])
            t      = int(p[2])
            otype  = int(p[3])
            is_sl  = bool(otype & 2)
            is_sp  = bool(otype & 8)
            end_t  = None
            end_xy = (ox, oy)

            slider_curve_type   = None
            slider_curve_points = None
            slider_repeats      = 1
            slider_length       = None

            cached_curve    = None
            cached_arc_cum  = None
            cached_arc_total= 0.0

            if is_sp:
                if len(p) >= 6:
                    end_t = int(p[5].split(":")[0])
                end_xy = (OSU_W // 2, OSU_H // 2)

            elif is_sl:
                slen           = float(p[7]) if len(p) >= 8 else 0.0
                slider_repeats = int(p[6])   if len(p) >= 7 else 1
                slider_length  = slen
                end_t          = int(t + slider_duration(t, slen, smult, pts) * slider_repeats)
                end_xy         = (ox, oy)

                if len(p) >= 6:
                    tokens = p[5].split("|")
                    if tokens:
                        slider_curve_type = tokens[0].upper()
                        pts_list = [(ox, oy)]
                        for token in tokens[1:]:
                            try:
                                px, py = map(int, token.split(":"))
                                pts_list.append((px, py))
                            except ValueError:
                                continue
                        if len(pts_list) > 1:
                            slider_curve_points = pts_list

                if slider_curve_points and slen > 0:
                    target_len = float(slen)
                    steps = max(SLIDER_MIN_STEPS,
                                min(SLIDER_MAX_STEPS,
                                    int(target_len * SLIDER_STEP_FACTOR) + SLIDER_BASE_STEP_ADD))
                    sampled  = sample_curve(slider_curve_type, slider_curve_points, steps)
                    trimmed, arc_cum, arc_total = truncate_curve_to_length(sampled, slen)
                    cached_curve      = trimmed
                    cached_arc_cum    = arc_cum
                    cached_arc_total  = arc_total
                    if trimmed:
                        ex, ey = trimmed[-1]
                        end_xy = (ox, oy) if slider_repeats % 2 == 0 else (int(round(ex)), int(round(ey)))

            objs.append({
                "time_ms":    t,
                "end_time_ms": end_t,
                "x": ox, "y": oy,
                "end_x": end_xy[0], "end_y": end_xy[1],
                "is_slider":  is_sl,
                "is_spinner": is_sp,
                "slider_curve_type":   slider_curve_type,
                "slider_curve_points": slider_curve_points,
                "slider_repeats":      slider_repeats,
                "slider_length":       slider_length,
                "cached_curve":    cached_curve,
                "cached_arc_cum":  cached_arc_cum,
                "cached_arc_total":cached_arc_total,
            })
        except (ValueError, IndexError):
            continue

    objs.sort(key=lambda o: o["time_ms"])
    return objs


def resolve_path(data):
    try:
        path = os.path.join(data["folders"]["songs"], data["files"]["beatmap"])
        return path if os.path.isfile(path) else None
    except (KeyError, TypeError):
        return None


# ─────────────────────────── Stream detection (v2) ────────────────────────────

def detect_streams(objects, start_idx, thresh_ms=None, min_notes=None, look_ahead=50):
    if thresh_ms  is None: thresh_ms  = STREAM_MS_THRESH
    if min_notes  is None: min_notes  = STREAM_MIN_NOTES
    end_idx     = min(start_idx + look_ahead, len(objects))
    stream_notes = []
    gaps         = []

    for i in range(start_idx, end_idx):
        o = objects[i]
        if o["is_slider"] or o["is_spinner"]:
            break
        if i > start_idx:
            gap = objects[i]["time_ms"] - objects[i-1]["time_ms"]
            if gap > thresh_ms:
                break
            gaps.append(gap)
        stream_notes.append(o)

    if len(stream_notes) < min_notes:
        return False, start_idx, (0, 0)

    if len(gaps) > 1:
        avg_gap = sum(gaps) / len(gaps)
        for gap in gaps:
            if abs(gap - avg_gap) > avg_gap * 0.25:
                return False, start_idx, (0, 0)

    dirs = []
    for j in range(1, len(stream_notes)):
        dx = stream_notes[j]["x"] - stream_notes[j-1]["x"]
        dy = stream_notes[j]["y"] - stream_notes[j-1]["y"]
        dist = math.sqrt(dx*dx + dy*dy)
        if dist > 1:
            dirs.append((dx/dist, dy/dist))

    if len(dirs) < 2:
        return False, start_idx, (0, 0)

    avg_dx = sum(d[0] for d in dirs) / len(dirs)
    avg_dy = sum(d[1] for d in dirs) / len(dirs)
    return True, start_idx + len(stream_notes) - 1, (avg_dx, avg_dy)


# ─────────────────────────── Click jitter (v2) ────────────────────────────────

_jitter_counter = 0

def next_click_jitter_ms(last_gap_ms=None):
    if last_gap_ms is not None and last_gap_ms < 90:
        return CLICK_VARIATION_MS * 0.4
    global _jitter_counter
    _jitter_counter += 1
    t = _jitter_counter
    v = math.sin(t * 1.2) * 0.6 + math.sin(t * 0.47 + 1.0) * 0.4
    if last_gap_ms is not None and last_gap_ms < 150:
        return v * (CLICK_VARIATION_MS * 0.6)
    return v * CLICK_VARIATION_MS


# ─────────────────────────── SendInput (v2 exact) ─────────────────────────────

MOUSEEVENTF_MOVE        = 0x0001
MOUSEEVENTF_ABSOLUTE    = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
KEYEVENTF_KEYUP         = 0x0002
KEYEVENTF_SCANCODE      = 0x0008

SC = {"z": 0x2C, "x": 0x2D}

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ULONG_PTR)]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ULONG_PTR)]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT_UNION)]

INPUT_MOUSE    = 0
INPUT_KEYBOARD = 1

VDESK_W  = ctypes.windll.user32.GetSystemMetrics(78)
VDESK_H  = ctypes.windll.user32.GetSystemMetrics(79)
VDESK_X  = ctypes.windll.user32.GetSystemMetrics(76)
VDESK_Y  = ctypes.windll.user32.GetSystemMetrics(77)
PRIMARY_W = ctypes.windll.user32.GetSystemMetrics(0)
PRIMARY_H = ctypes.windll.user32.GetSystemMetrics(1)


def _send(inputs):
    arr = (INPUT * len(inputs))(*inputs)
    ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def mouse_move(x, y):
    x, y = float(x), float(y)
    if USE_VIRTUAL_DESK:
        base_x, base_y = float(VDESK_X), float(VDESK_Y)
        span_w, span_h = float(VDESK_W), float(VDESK_H)
        flags          = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    else:
        base_x, base_y = 0.0, 0.0
        span_w, span_h = float(PRIMARY_W), float(PRIMARY_H)
        flags          = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
    x  = max(base_x, min(base_x + span_w - 1.0, x))
    y  = max(base_y, min(base_y + span_h - 1.0, y))
    nx = int(round((x - base_x) * 65535 / span_w))
    ny = int(round((y - base_y) * 65535 / span_h))
    _send([INPUT(type=INPUT_MOUSE,
                 _input=_INPUT_UNION(mi=MOUSEINPUT(
                     dx=nx, dy=ny, mouseData=0,
                     dwFlags=flags, time=0, dwExtraInfo=0)))])


def key_down(k):
    _send([INPUT(type=INPUT_KEYBOARD,
                 _input=_INPUT_UNION(ki=KEYBDINPUT(
                     wVk=0, wScan=SC[k],
                     dwFlags=KEYEVENTF_SCANCODE, time=0, dwExtraInfo=0)))])


def key_up(k):
    _send([INPUT(type=INPUT_KEYBOARD,
                 _input=_INPUT_UNION(ki=KEYBDINPUT(
                     wVk=0, wScan=SC[k],
                     dwFlags=KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP,
                     time=0, dwExtraInfo=0)))])


# ─────────────────────────── Spinner ─────────────────────────────────────────

def do_spinner(cx, cy, end_wall, rate=1.0, stop_event=None):
    """V1-style spinner: tight 1ms loop, parametric circle."""
    start_wall       = time.perf_counter()
    angular_velocity = (SPINNER_RPM / 60.0) * 2 * math.pi * rate
    while time.perf_counter() < end_wall:
        if stop_event and stop_event.is_set():
            return
        elapsed = time.perf_counter() - start_wall
        angle   = angular_velocity * elapsed
        mouse_move(cx + SPINNER_RADIUS * math.cos(angle),
                   cy + SPINNER_RADIUS * math.sin(angle))
        sleep_until(time.perf_counter() + 0.001)


# ─────────────────────────── Global state ────────────────────────────────────

state_lock = threading.Lock()
_state = {
    "current_path":  None,
    "hit_objects":   [],
    "play_state":    "menu",
    "live_ms":       None,
    "speed_change":  1.0,
    "stop_event":    threading.Event(),
    "relax_thread":  None,
    # v2 auto-start fields
    "_auto_started": False,
    "_manual_armed": False,
}


def get_state(key=None):
    with state_lock:
        return _state[key] if key else _state.copy()


def set_state(updates):
    with state_lock:
        _state.update(updates)


# ─────────────────────────── Core relax loop (v1 architecture) ───────────────
#
# V1's approach: build flat lists of moves + keys, then run two threads:
#   cursor_worker  — interpolates between successive (wall, x, y) waypoints
#   key_worker     — sleeps until each note's press_wall and taps
#
# What's new vs v1:
#   • HIT_BIAS_MS / click jitter (v2)
#   • Slider curves from v2 exact arc-length sampling (all curve types)
#   • Spinner periods stored for cursor skip (same as v1 but now from v2 data)
#   • bisect-based interval lookup instead of linear scan (v2 perf)
#   • Arc disabled when near slider/spinner, and for very fast jumps (v2 logic)
#   • Stream detection: back-to-back close notes are still treated as individual
#     waypoints so the cursor hits each one (v1 behaviour preserved), but the
#     arc is suppressed for short gaps (ARC_STREAM_THRESH_PX)
# ─────────────────────────────────────────────────────────────────────────────

def relax_loop(objects, sync_wall, first_note_ms, rate):
    stop_event = get_state("stop_event")
    stop_event.clear()

    moves           = []   # (wall_time, screen_x, screen_y)
    keys            = []   # (press_wall, end_wall, key, is_spinner, is_slider, sx, sy)
    spinner_periods = []   # (start_wall, end_wall)
    slider_periods  = []   # (start_wall, end_wall)

    key_idx   = 0
    prev_time = None

    for obj in objects:
        hit_wall = sync_wall + ((obj["time_ms"]     + GAME_OFFSET_MS) - first_note_ms) / 1000.0 / rate
        end_wall = (sync_wall + ((obj["end_time_ms"] + GAME_OFFSET_MS) - first_note_ms) / 1000.0 / rate
                    if obj["end_time_ms"] is not None else None)

        tx, ty = osu_to_screen(obj["x"], obj["y"])
        moves.append((hit_wall, tx, ty))

        # ── Spinner ──────────────────────────────────────────────────────────
        if obj["is_spinner"] and end_wall:
            spinner_periods.append((hit_wall, end_wall))

        # ── Slider: inject curve waypoints (v2 exact) ────────────────────────
        if obj["is_slider"] and end_wall:
            slider_periods.append((hit_wall, end_wall))

            curve     = obj["cached_curve"]
            arc_cum   = obj["cached_arc_cum"]
            arc_total = obj["cached_arc_total"]
            repeats   = int(obj.get("slider_repeats", 1) or 1)

            if curve and len(curve) > 1 and arc_total > ARCLENGTH_EPSILON:
                total_dur  = end_wall - hit_wall
                span_dur   = total_dur / repeats
                # sample at ~200 pts/sec, minimum 40
                num_pts    = max(40, int(total_dur * 200))
                for k in range(num_pts + 1):
                    u_global = k / num_pts           # 0..1 across entire slider
                    elapsed  = u_global * total_dur
                    if span_dur > ARCLENGTH_EPSILON:
                        span_idx  = max(0, min(repeats - 1, int(elapsed / span_dur)))
                        local     = max(0.0, min(1.0, (elapsed - span_idx * span_dur) / span_dur))
                        u         = local if (span_idx % 2 == 0) else (1.0 - local)
                    else:
                        u = 1.0
                    ox, oy = sample_curve_by_arclength(curve, arc_cum, arc_total, u)
                    sx, sy = osu_to_screen(ox, oy)
                    moves.append((hit_wall + elapsed, sx, sy))
            else:
                # fallback: straight line to endpoint
                ex, ey = osu_to_screen(obj["end_x"], obj["end_y"])
                moves.append((end_wall, ex, ey))

        # ── Key schedule ─────────────────────────────────────────────────────
        gap_ms = (obj["time_ms"] - prev_time) if prev_time is not None else None
        prev_time = obj["time_ms"]

        jitter = next_click_jitter_ms(gap_ms)
        key    = CLICK_KEYS[key_idx % 2]
        key_idx += 1
        keys.append((
            hit_wall + HIT_BIAS_MS / 1000.0 + jitter / 1000.0,
            end_wall, key,
            obj["is_spinner"], obj["is_slider"],
            tx, ty,
        ))

    # Sort moves by time (slider waypoints interleave with note waypoints)
    moves.sort(key=lambda m: m[0])

    # Build movement segments from consecutive waypoints
    segments = []
    for i in range(len(moves) - 1):
        sw, x1, y1 = moves[i]
        ew, x2, y2 = moves[i + 1]
        if ew > sw + 1e-6:
            segments.append((sw, ew, x1, y1, x2, y2))

    # bisect helpers for O(log n) interval lookup
    spinner_starts = [s for s, _ in spinner_periods]
    slider_starts  = [s for s, _ in slider_periods]

    def in_interval(wall, starts, periods):
        if not periods:
            return False
        i = bisect.bisect_right(starts, wall) - 1
        if i < 0:
            return False
        s, e = periods[i]
        return s <= wall < e

    def in_spinner(wall):
        return in_interval(wall, spinner_starts, spinner_periods)

    def in_slider_or_spinner(wall):
        return (in_interval(wall, spinner_starts, spinner_periods) or
                in_interval(wall, slider_starts,  slider_periods))

    # ── Cursor worker (v1 logic, v2 features) ─────────────────────────────────
    def cursor_worker():
        if not segments:
            for wall, x, y in moves:
                if stop_event.is_set(): return
                if not sleep_until(wall, stop_event):
                    return
                mouse_move(x, y)
            return

        seg_i = 0
        total = len(segments)

        while seg_i < total and not stop_event.is_set():
            start_wall, end_wall, x1, y1, x2, y2 = segments[seg_i]
            now = time.perf_counter()

            # Wait until segment starts
            if now < start_wall:
                if not sleep_until(start_wall, stop_event):
                    return
                now = time.perf_counter()

            # Skip this segment if we're inside a spinner (spinner handled in key_worker)
            if in_spinner(now):
                seg_i += 1
                continue

            dt   = end_wall - start_wall
            dist = math.sqrt((x2-x1)**2 + (y2-y1)**2)

            # Decide whether to use arc for this segment
            osu1x, osu1y = screen_to_osu(x1, y1)
            osu2x, osu2y = screen_to_osu(x2, y2)
            dist_osu     = math.hypot(osu2x-osu1x, osu2y-osu1y)

            jump_speed_px_ms = (dist / (dt * 1000.0)) if dt > 0 else 0
            use_arc = (ARC_MODE
                       and dist_osu >= ARC_STREAM_THRESH_PX
                       and not (DISABLE_ARC_ON_FAST_JUMPS and jump_speed_px_ms > MAX_JUMP_SPEED_PX_MS))

            # ── Per-frame interpolation loop ──────────────────────────────────
            while now < end_wall and not stop_event.is_set():
                if in_spinner(now):
                    break

                elapsed = now - start_wall

                if use_arc and not in_slider_or_spinner(now):
                    x, y = apply_arc(elapsed, dt, x1, y1, x2, y2)
                else:
                    eased = smooth_easing(elapsed / dt if dt > 0 else 1.0)
                    x     = x1 + (x2 - x1) * eased
                    y     = y1 + (y2 - y1) * eased

                mouse_move(x, y)
                if not sleep_until(now + 0.001, stop_event):
                    return
                now = time.perf_counter()

            # Snap to endpoint unless we're inside a spinner
            if now >= end_wall and not in_spinner(now):
                mouse_move(x2, y2)

            seg_i += 1

    # ── Key worker (v1 logic + v2 timing) ────────────────────────────────────
    def key_worker():
        cx_spin, cy_spin = osu_to_screen(OSU_W // 2, OSU_H // 2)
        for press_wall, end_wall, key, is_spinner, is_slider, sx, sy in keys:
            if stop_event.is_set():
                return
            if not sleep_until(press_wall, stop_event):
                return
            key_down(key)
            try:
                if is_spinner and end_wall:
                    do_spinner(cx_spin, cy_spin, end_wall, rate, stop_event)
                elif is_slider and end_wall:
                    sleep_until(end_wall, stop_event)
                else:
                    sleep_until(press_wall + 0.020, stop_event)
            finally:
                key_up(key)
        if not stop_event.is_set():
            print("[relax] Map finished.")
            set_state({"_auto_started": False})

    workers = [
        threading.Thread(target=cursor_worker, daemon=True),
        threading.Thread(target=key_worker, daemon=True),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()


# ─────────────────────────── Hotkeys ─────────────────────────────────────────

def _do_sync():
    """Shared sync logic: stop current run, start fresh from first note."""
    hit_objects = get_state("hit_objects")
    if not hit_objects:
        print("[relax] No map loaded yet.")
        return
    stop_event = get_state("stop_event")
    stop_event.set()
    relax_thread = get_state("relax_thread")
    if relax_thread and relax_thread.is_alive():
        relax_thread.join(timeout=0.5)
    stop_event.clear()
    first = hit_objects[0]["time_ms"]
    rate  = get_state("speed_change")
    print(f"[relax] Synced! {len(hit_objects)} objects, first note @ {first}ms, rate={rate}x")
    t = threading.Thread(target=relax_loop,
                         args=(hit_objects, time.perf_counter(), first, rate),
                         daemon=True)
    set_state({"relax_thread": t})
    t.start()


def on_sync():
    with state_lock:
        _state["_manual_armed"] = True
        _state["_auto_started"] = False
    print("[relax] Armed — waiting for a valid Tosu clock and beatmap...")


def on_stop():
    get_state("stop_event").set()
    set_state({"_auto_started": False, "_manual_armed": False})
    print("[relax] Stopped.")


def sync_wall_from_tosu_live(live_ms, first_note_ms, rate, received_at):
    """Translate a Tosu song-clock sample into the wall time of the first note."""
    extra_lead_ms = 0.0
    if live_ms <= AUTO_PRESTART_CLOCK_MAX_MS:
        extra_lead_ms = max(0.0, OSU_MIN_LEAD_IN_MS - first_note_ms)
    return (received_at
            - ((live_ms - first_note_ms) / 1000.0 / rate)
            - MAP_DELAY_COMPENSATION_MS / 1000.0
            + extra_lead_ms / 1000.0 / rate)


def start_from_tosu_live(live_ms, received_at, expected_path):
    """Start from the current map and the exact Tosu packet that claimed sync."""
    stop_event = get_state("stop_event")
    stop_event.set()
    relax_thread = get_state("relax_thread")
    if relax_thread and relax_thread.is_alive():
        relax_thread.join(timeout=1.0)
        if relax_thread.is_alive():
            print("[sync] Previous run did not stop; refusing to start a second run")
            with state_lock:
                _state["_auto_started"] = False
                _state["_manual_armed"] = True
            return

    with state_lock:
        if (_state["play_state"] != "play"
                or not _state["_auto_started"]
                or _state["current_path"] != expected_path):
            return
        hit_objects = _state["hit_objects"]
        if not hit_objects:
            _state["_auto_started"] = False
            _state["_manual_armed"] = True
            return
        first = hit_objects[0]["time_ms"]
        rate = _state["speed_change"]
        sync_wall = sync_wall_from_tosu_live(
            float(live_ms), first, rate, received_at
        )
        stop_event = _state["stop_event"]
        stop_event.clear()

    print(f"[sync] Started {os.path.basename(expected_path)} at live={float(live_ms):.1f}ms")
    thread = threading.Thread(
        target=relax_loop,
        args=(hit_objects, sync_wall, first, rate),
        daemon=True,
    )
    set_state({"relax_thread": thread})
    thread.start()


# ─────────────────────────── tosu WebSocket ──────────────────────────────────

def on_message(ws, message):
    received_at = time.perf_counter()
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return

    state_name = data.get("state", {}).get("name")
    live_ms = data.get("beatmap", {}).get("time", {}).get("live")
    set_state({"play_state": state_name or "menu", "live_ms": live_ms})
    if state_name != "play":
        get_state("stop_event").set()
        # Preserve a Q arm made in the menu until Tosu enters play.
        set_state({"_auto_started": False})

    # ── Speed mod detection (DT / HT) ────────────────────────────────────────
    new_rate = 1.0
    for mod in data.get("play", {}).get("mods", {}).get("array", []):
        sc = mod.get("settings", {}).get("speed_change")
        if sc is not None:
            new_rate = float(sc); break
    if new_rate != get_state("speed_change"):
        set_state({"speed_change": new_rate})
        print(f"[tosu] Rate: {new_rate}x")

    # ── Map change detection ──────────────────────────────────────────────────
    path = resolve_path(data)
    snap = get_state()
    if path and path != snap["current_path"]:
        try:
            hit_objects = parse_osu_file(path)
            get_state("stop_event").set()
            with state_lock:
                _state.update({"current_path": path, "hit_objects": hit_objects,
                               "_auto_started": False})
            bm = data.get("beatmap", {})
            print(f"[tosu] {bm.get('artist','?')} - {bm.get('title','?')} "
                  f"[{bm.get('version','?')}]  |  {len(hit_objects)} objects  |  {get_state('speed_change')}x")
        except Exception as e:
            print(f"[tosu] Parse error: {e}")

    # Start only after this packet identifies the loaded map and reports a
    # running clock. Claim the arm under the lock to prevent duplicate workers.
    snap = get_state()
    try:
        live_value = float(live_ms) if live_ms is not None else None
    except (TypeError, ValueError):
        live_value = None
    if (AUTO_START and state_name == "play"
            and path and path == snap["current_path"]
            and snap["hit_objects"] and snap["_manual_armed"]
            and not snap["_auto_started"] and live_value is not None
            and live_value >= AUTO_START_MIN_LIVE_MS):
        with state_lock:
            should_start = (
                _state["_manual_armed"]
                and not _state["_auto_started"]
                and _state["play_state"] == "play"
                and _state["current_path"] == path
                and bool(_state["hit_objects"])
            )
            if should_start:
                _state["_auto_started"] = True
                _state["_manual_armed"] = False
        if should_start:
            threading.Thread(
                target=start_from_tosu_live,
                args=(live_value, received_at, path),
                daemon=True,
            ).start()


def on_error(ws, e):
    print(f"[tosu] Error: {e}")


def on_close(ws, *_):
    print("[tosu] Disconnected, retrying in 3s...")
    time.sleep(3)
    start_ws()


def start_ws():
    ws = websocket.WebSocketApp(TOSU_WS_URL,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    threading.Thread(target=ws.run_forever, daemon=True).start()


# ─────────────────────────── Main ────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[startup] {RELEASE_NAME} v{RELEASE_VERSION} loaded.")
    print("[setup] First, check/edit the settings at the top of mainV1.py. "
          "Sync may not be exact; adjust GAME_OFFSET_MS per song if needed.")
    print(f"[relax] Press [{SYNC_HOTKEY.upper()}] when the music starts to start; "
          f"press [{STOP_HOTKEY.upper()}] to cancel or stop.")

    keyboard.add_hotkey(SYNC_HOTKEY, on_sync)
    keyboard.add_hotkey(STOP_HOTKEY, on_stop)
    start_ws()
    print("[relax] Waiting for tosu...")
    keyboard.wait()