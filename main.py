import sys
sys.stdout.reconfigure(encoding='utf-8')

import ctypes
import ctypes.wintypes
import bisect
import json
import math
import os
import threading
import time
import websocket
import keyboard

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

################# START OF CONFIG ##############################################

# Hotkeys & Input
SYNC_HOTKEY    = "q"    # press this key before any notes play inside the map to start
STOP_HOTKEY    = "w"    # press this to stop
CLICK_KEYS     = ["z", "x"]
GAME_OFFSET_MS = 0    # dont touch this (it doesnt do much)
HIT_BIAS_MS    = -10   # reverse sign (positive ingame offset = negative one here)
CURSOR_SENS    = 1# breaks things, script has been changed to work with any sens so keep this at one even if your sensitivity is 2.0 for example

# Click accuracy guard (helps rare misses on large jumps)
ENABLE_CLICK_POS_GATE   = True   # only click once cursor is close enough to target
CLICK_POS_WINDOW_PX     = 48.0   # allowed radius (screen pixels) around note position
CLICK_POS_MAX_LATE_MS   = 18.0   # how long after scheduled time we may wait to be in-window
CLICK_POS_POLL_SLEEP_S  = 0.0002 # small sleep while waiting (seconds)
ENABLE_JUMP_EARLY_LEAD  = True   # schedule clicks a bit earlier on big jumps (gate prevents premature click)
JUMP_EARLY_LEAD_PX_PER_MS = 90.0 # higher = less lead; tuned for typical Windows input latency
JUMP_EARLY_LEAD_MAX_MS    = 10.0 # clamp (ms)

# Movement Tuning
MOVEMENT_MODE  = "predictive"   # linear / arc / predictive
ARC_MODE          = True      # for arcs in between notes
ARC_MAX_AMPLITUDE  = 60    # arc size
ARC_MIN_AMPLITUDE  = 15
ARC_MAX_DISTANCE   = 400
ARC_CYCLES        = 0.5        # keep this the same for a single arc
ARC_STREAM_THRESH_PX = 100       # any distance closer than this will not have an arc
ARC_EXP_BASE       = 0.12      # diminishing arc (best to leave this)
SPINNER_RPM    = 350         # spinner speed (does not correlate to ingame so play with it)
SPINNER_RADIUS = 90

# Features
RELAX_MODE        = False      # only tapping (this is fun)
AUTO_START        = True       # keep this on
PREDICT_NOTES     = 5         # best to keep this at 5 unless you have a low end device
STREAM_MS_THRESH  = 250        # smallest time between notes to detect a stream
DEBUG_MOVEMENT    = False     # didnt work :(
STREAM_SMOOTH     = True       # smooth streams
STREAM_SMOOTH_PASSES = 2
STREAM_SMOOTH_ALPHA  = 0.28
STREAM_SAMPLES_PER_OSUPX = 0.4

# Fast jump tuning (new)
FAST_JUMP_THRESHOLD_MS   = 50      # notes faster than this are "fast jumps"
DISABLE_ARC_ON_FAST_JUMPS = True   # disable arc for very fast jumps
DISABLE_JITTER_ON_FAST = True      # no jitter on fast jumps
MAX_JUMP_SPEED_PX_MS     = 15      # arc disable threshold (pixels per ms)
USE_BUSY_WAIT_FOR_FAST   = True    # busy-wait for precise timing on fast segments

# Display Setup
SCREEN_W = 2560
SCREEN_H = 1440
PF_HEIGHT_PCT = 0.80
PF_TOP_PCT    = 0.095
PF_Y_OFFSET   = 15        # if notes are hit too high/low change this
OSU_W, OSU_H  = 512, 384
USE_VIRTUAL_DESK = False

# Tosu
TOSU_WS_URL = "ws://localhost:24050/websocket/v2"

################# END OF CONFIG ##############################################

# Numeric thresholds
FLOAT_EPSILON = 1e-3
DOUBLE_EPSILON = 1e-7
OSU_OOB_MARGIN         = 96.0
SLIDER_STEP_FACTOR     = 1.6
SLIDER_BASE_STEP_ADD   = 80
SLIDER_MIN_STEPS       = 120
SLIDER_MAX_STEPS       = 6000
SEGMENT_MIN_DUR        = 0.005
ARCLENGTH_EPSILON      = FLOAT_EPSILON
TIMING_SPIN_WINDOW     = 0.0005
CIRCLE_DET_THRESHOLD   = FLOAT_EPSILON
TINY_ARC_THRESHOLD     = FLOAT_EPSILON * 10
MAX_CURSOR_DELTA_PX    = 999999.0   # effectively disabled - was causing missed fast jumps
CLICK_VARIATION_MS     = 9.0        # half-width of click timing window

# Playfield

def _pf():
    h = SCREEN_H * PF_HEIGHT_PCT
    w = h * (OSU_W / OSU_H)
    l = (SCREEN_W - w) / 2
    t = SCREEN_H * PF_TOP_PCT + PF_Y_OFFSET
    return l, t, w, h

PF_LEFT, PF_TOP, PF_W, PF_H = _pf()


def now():
    return time.perf_counter()

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

def get_cursor_pos():
    p = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return float(p.x), float(p.y)


def osu_to_screen(ox, oy):
    ox = float(ox)
    oy = float(oy)
    ox = max(-OSU_OOB_MARGIN, min(float(OSU_W) + OSU_OOB_MARGIN, ox))
    oy = max(-OSU_OOB_MARGIN, min(float(OSU_H) + OSU_OOB_MARGIN, oy))
    tx = PF_LEFT + (ox / OSU_W) * PF_W
    ty = PF_TOP  + (oy / OSU_H) * PF_H
    if CURSOR_SENS and CURSOR_SENS != 1.0:
        cx = PF_LEFT + PF_W * 0.5
        cy = PF_TOP  + PF_H * 0.5
        tx = cx + (tx - cx) / float(CURSOR_SENS)
        ty = cy + (ty - cy) / float(CURSOR_SENS)
    return tx, ty

def screen_to_osu(sx, sy):
    if CURSOR_SENS and CURSOR_SENS != 1.0:
        cx = PF_LEFT + PF_W * 0.5
        cy = PF_TOP  + PF_H * 0.5
        sx = cx + (sx - cx) * float(CURSOR_SENS)
        sy = cy + (sy - cy) * float(CURSOR_SENS)
    ox = (sx - PF_LEFT) / PF_W * OSU_W
    oy = (sy - PF_TOP)  / PF_H * OSU_H
    return ox, oy

def lerp(start, end, amount):
    return start + (end - start) * amount

def ease(t):
    t = max(0.0, min(1.0, t))
    return 3 * t * t - 2 * t * t * t

smooth_easing = ease


# Circle / Arc helpers

def circle_from_3pts(p0, p1, p2):
    x0, y0 = p0
    x1, y1 = p1
    x2, y2 = p2
    mx01, my01 = (x0 + x1) / 2, (y0 + y1) / 2
    dx01, dy01 = y1 - y0, x0 - x1
    mx12, my12 = (x1 + x2) / 2, (y1 + y2) / 2
    dx12, dy12 = y2 - y1, x1 - x2
    a1, b1, c1 = dy01, -dx01, dx01 * mx01 + dy01 * my01
    a2, b2, c2 = dy12, -dx12, dx12 * mx12 + dy12 * my12
    det = a1 * b2 - a2 * b1
    if abs(det) < CIRCLE_DET_THRESHOLD:
        return None
    cx = (b2 * c1 - b1 * c2) / det
    cy = (a1 * c2 - a2 * c1) / det
    r  = math.hypot(cx - x0, cy - y0)
    start_angle = math.atan2(y0 - cy, x0 - cx)
    return cx, cy, r, start_angle


def _cross2d(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


# Arc movement

def apply_predictive_arc(elapsed, dt, x1, y1, x2, y2, dist_osu_px,
                          pred_dir_x=None, pred_dir_y=None):
    if dt <= 0:
        return x1, y1
    
    # SIMPLE FALLBACK: For very large jumps, use the first script's method
    # This ensures reliability on big jumps
    if dist_osu_px > 250:  # Very large jumps
        # Use the first script's simpler arc method
        phase = max(0.0, min(1.0, elapsed / dt))
        dx = x2 - x1
        dy = y2 - y1
        dist = math.sqrt(dx*dx + dy*dy)
        
        eased_phase = smooth_easing(phase)
        x = x1 + dx * eased_phase
        y = y1 + dy * eased_phase
        
        if dist > 0:
            perp_x = -dy / dist
            perp_y = dx / dist
            # Use consistent 60px arc like first script
            wave = math.sin(phase * 2 * math.pi * ARC_CYCLES)
            offset = wave * 60  # Fixed amplitude like first script
            x += perp_x * offset
            y += perp_y * offset
        
        return int(x), int(y)

    dist_factor = min(dist_osu_px / ARC_MAX_DISTANCE, 1.0)
    amplitude = ARC_MIN_AMPLITUDE + (ARC_MAX_AMPLITUDE - ARC_MIN_AMPLITUDE) * dist_factor

    # Fast jump detection: skip arc and use strong easing instead
    osu_dist = dist_osu_px  # Already calculated above
    jump_speed_osu_px_ms = osu_dist / (dt * 1000.0) if dt > 0 else 0
    # Don't disable arc for fast jumps, just reduce amplitude slightly
    if DISABLE_ARC_ON_FAST_JUMPS and jump_speed_osu_px_ms > 5.0:  # 5 osu pixels/ms is very fast
        # Still use arc but with reduced amplitude for very fast jumps
        amplitude *= 0.6
        phase = max(0.0, min(1.0, elapsed / dt))
        eased_phase = smooth_easing(phase)
        return x1 + (x2 - x1) * eased_phase, y1 + (y2 - y1) * eased_phase

    phase = max(0.0, min(1.0, elapsed / dt))
    dx, dy = x2 - x1, y2 - y1
    eased_phase = smooth_easing(phase)
    bx = x1 + dx * eased_phase
    by = y1 + dy * eased_phase

    if dist_osu_px < ARC_STREAM_THRESH_PX * 0.8:
        return bx, by

    dist_factor = dist_osu_px / ARC_STREAM_THRESH_PX
    amplitude = min(ARC_MAX_AMPLITUDE * math.exp(-ARC_EXP_BASE * dist_factor),
                    ARC_MAX_AMPLITUDE)

    if pred_dir_x is not None and pred_dir_y is not None:
        dir_x, dir_y = pred_dir_x, pred_dir_y
    else:
        dist_screen = math.hypot(dx, dy)
        if dist_screen > 0:
            dir_x, dir_y = -dy / dist_screen, dx / dist_screen
        else:
            dir_x, dir_y = 1.0, 0.0

    wave = math.sin(phase * 2 * math.pi * ARC_CYCLES)
    return bx + dir_x * wave * amplitude, by + dir_y * wave * amplitude


def apply_arc(elapsed, dt, x1, y1, x2, y2):
    dx_osu = math.hypot(x2 - x1, y2 - y1)
    return apply_predictive_arc(elapsed, dt, x1, y1, x2, y2, dx_osu)


# Curve sampling

def evaluate_bezier(points, t):
    pts = [(float(x), float(y)) for x, y in points]
    while len(pts) > 1:
        pts = [(lerp(pts[i][0], pts[i+1][0], t),
                lerp(pts[i][1], pts[i+1][1], t))
               for i in range(len(pts) - 1)]
    return pts[0]


def _clamp_osu(pt):
    x, y = pt
    x = max(-OSU_OOB_MARGIN, min(float(OSU_W) + OSU_OOB_MARGIN, float(x)))
    y = max(-OSU_OOB_MARGIN, min(float(OSU_H) + OSU_OOB_MARGIN, float(y)))
    return x, y


def sample_curve(curve_type, points, steps=40, pixel_length=None):
    if not points or len(points) < 2:
        return [_clamp_osu(p) for p in points[:1]]
    if steps < 2:
        return [_clamp_osu(p) for p in points]

    curve_type = curve_type.upper()

    if curve_type == "L":
        out = []
        total = len(points) - 1
        for i in range(steps):
            raw_t = i / (steps - 1)
            scaled = raw_t * total
            idx = min(int(scaled), total - 1)
            local_t = scaled - idx
            x1, y1 = _clamp_osu(points[idx])
            x2, y2 = _clamp_osu(points[idx + 1])
            out.append((lerp(x1, x2, local_t), lerp(y1, y2, local_t)))
        return out

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
        d = 2.0 * (a_x * (b_y - c_y) + b_x * (c_y - a_y) + c_x * (a_y - b_y))
        a_sq = a_x * a_x + a_y * a_y
        b_sq = b_x * b_x + b_y * b_y
        c_sq = c_x * c_x + c_y * c_y
        cx = (a_sq * (b_y - c_y) + b_sq * (c_y - a_y) + c_sq * (a_y - b_y)) / d
        cy = (a_sq * (c_x - b_x) + b_sq * (a_x - c_x) + c_sq * (b_x - a_x)) / d
        dA_x = a_x - cx
        dA_y = a_y - cy
        dC_x = c_x - cx
        dC_y = c_y - cy
        r = math.hypot(dA_x, dA_y)
        if r <= 1e-6:
            return sample_curve("L", [p0, p2], steps)
        theta_start = math.atan2(dA_y, dA_x)
        theta_end = math.atan2(dC_y, dC_x)
        while theta_end < theta_start:
            theta_end += 2.0 * math.pi
        direction = 1.0
        theta_range = theta_end - theta_start
        ortho_AC_x = c_y - a_y
        ortho_AC_y = -(c_x - a_x)
        dot = ortho_AC_x * (b_x - a_x) + ortho_AC_y * (b_y - a_y)
        if dot < 0:
            direction = -1.0
            theta_range = 2.0 * math.pi - theta_range
        total_angle = direction * theta_range
        max_angle = 2.5 * math.pi
        if abs(total_angle) > max_angle:
            total_angle = math.copysign(max_angle, total_angle)
        curved_steps = min(
            max(steps, int(abs(total_angle) * r * 0.6)),
            SLIDER_MAX_STEPS
        )
        out = []
        for i in range(curved_steps):
            t = i / (curved_steps - 1)
            a = theta_start + t * total_angle
            out.append(_clamp_osu((cx + r * math.cos(a), cy + r * math.sin(a))))
        if curved_steps != steps:
            out = sample_polyline_linear(out, steps)
        return out

    elif curve_type == "C":
        pts = [(float(x), float(y)) for x, y in points]
        if len(pts) < 2:
            return pts
        ext = [pts[0]] + pts + [pts[-1]]
        seg_count = max(1, len(pts) - 1)

        def catmull_rom(t, p0, p1, p2, p3):
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2*p1[0]) + (-p0[0]+p2[0])*t +
                       (2*p0[0]-5*p1[0]+4*p2[0]-p3[0])*t2 +
                       (-p0[0]+3*p1[0]-3*p2[0]+p3[0])*t3)
            y = 0.5 * ((2*p1[1]) + (-p0[1]+p2[1])*t +
                       (2*p0[1]-5*p1[1]+4*p2[1]-p3[1])*t2 +
                       (-p0[1]+3*p1[1]-3*p2[1]+p3[1])*t3)
            return x, y

        out = []
        for i in range(steps):
            raw_t = i / (steps - 1)
            s = raw_t * seg_count
            seg = min(int(s), seg_count - 1)
            local_t = s - seg if seg < seg_count else 1.0
            x, y = catmull_rom(local_t, ext[seg], ext[seg+1], ext[seg+2], ext[seg+3])
            out.append(_clamp_osu((x, y)))
        return out

    elif curve_type == "B":
        segments = []
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

        def _rough_seg_len(sp):
            acc = 0.0
            for a in range(1, len(sp)):
                acc += math.hypot(float(sp[a][0]) - float(sp[a-1][0]),
                                  float(sp[a][1]) - float(sp[a-1][1]))
            return acc

        seg_lens = [_rough_seg_len(s) for s in segments]
        total_len = sum(seg_lens)
        if total_len <= ARCLENGTH_EPSILON:
            seg_steps_list = [max(4, steps // max(1, len(segments)))] * len(segments)
        else:
            base = [max(4, int(round(steps * (l / total_len)))) for l in seg_lens]
            seg_steps_list = base
            diff = steps - sum(seg_steps_list)
            order = sorted(range(len(seg_steps_list)), key=lambda i: seg_lens[i], reverse=True)
            oi = 0
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
            seg_out = []
            for k in range(seg_steps):
                t = k / (seg_steps - 1)
                px, py = evaluate_bezier(seg_pts, t)
                seg_out.append(_clamp_osu((px, py)))
            if seg_idx == 0:
                out.extend(seg_out)
            else:
                out.extend(seg_out[1:])

        if len(out) < 2:
            return out
        if len(out) != steps:
            out = sample_polyline_linear(out, steps)
        return out

    out = []
    for i in range(steps):
        t = i / (steps - 1)
        px, py = evaluate_bezier(points, t)
        out.append(_clamp_osu((px, py)))
    return out


# Arc-length helpers

def stream_polyline_arclengths(curve):
    if not curve:
        return [], 0.0
    cum = [0.0]
    for k in range(1, len(curve)):
        x0, y0 = curve[k-1]
        x1, y1 = curve[k]
        cum.append(cum[-1] + math.hypot(float(x1)-float(x0), float(y1)-float(y0)))
    return cum, cum[-1]


def sample_stream_curve_by_arclength(curve, cumdists, total_len, u):
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
    d0, d1 = cumdists[j], cumdists[j+1]
    seg_len = d1 - d0
    if seg_len <= ARCLENGTH_EPSILON:
        x, y = curve[j]; return float(x), float(y)
    local_t = (target - d0) / seg_len
    x0, y0 = curve[j]; x1, y1 = curve[j+1]
    return lerp(float(x0), float(x1), local_t), lerp(float(y0), float(y1), local_t)


def truncate_curve_to_length(curve, length_osu_px):
    if not curve:
        return [], [], 0.0
    cum_dist, total_len = stream_polyline_arclengths(curve)
    if length_osu_px is None or length_osu_px <= 0 or total_len <= ARCLENGTH_EPSILON:
        return curve, cum_dist, total_len
    target = min(float(length_osu_px), total_len)
    if target >= total_len - ARCLENGTH_EPSILON:
        return curve, cum_dist, total_len
    j = bisect.bisect_right(cum_dist, target) - 1
    j = max(0, min(j, len(curve) - 2))
    d0, d1 = cum_dist[j], cum_dist[j+1]
    seg_len = d1 - d0
    if seg_len <= ARCLENGTH_EPSILON:
        trimmed = curve[:j+2]
        nc, nt = stream_polyline_arclengths(trimmed)
        return trimmed, nc, nt
    local_t = (target - d0) / seg_len
    x0, y0 = curve[j]; x1, y1 = curve[j+1]
    end_pt = (lerp(float(x0), float(x1), local_t), lerp(float(y0), float(y1), local_t))
    trimmed = list(curve[:j+1]) + [end_pt]
    new_cum = list(cum_dist[:j+1]) + [target]
    return trimmed, new_cum, target


def sample_polyline_linear(points, steps):
    if not points:
        return []
    if len(points) == 1:
        return [points[0]] * max(2, steps)
    if steps < 2:
        return [points[0], points[-1]]
    out = []
    total = len(points) - 1
    for i in range(steps):
        raw_t = i / (steps - 1)
        scaled = raw_t * total
        idx = min(int(scaled), total - 1)
        local_t = scaled - idx
        x1, y1 = points[idx]; x2, y2 = points[idx+1]
        out.append((lerp(x1, x2, local_t), lerp(y1, y2, local_t)))
    return out


# Timing helpers

def sleep_until(wall):
    while True:
        remaining = wall - now()
        if remaining <= 0:
            break
        if remaining > TIMING_SPIN_WINDOW:
            time.sleep(remaining - TIMING_SPIN_WINDOW)
        # spin final window


def smart_sleep_until(wall):
    while True:
        remaining = wall - now()
        if remaining <= 0:
            break
        if remaining > 0.002:  # Changed from 0.005
            time.sleep(max(0.0001, remaining - 0.001))
        else:
            # Busy wait for final 2ms
            while now() < wall:
                pass
            break

# .osu parsing

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


def parse_slider_multiplier(lines, default=1.4):
    for line in lines:
        if line.strip().startswith("SliderMultiplier"):
            try:
                return float(line.split(":")[1].strip())
            except (ValueError, IndexError):
                return default
    return default


def get_timing_at(pts, t):
    mpb = 500.0
    sv  = 1.0
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


def parse_hit_objects(lines, smult, pts):
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
                end_t = int(t + slider_duration(t, slen, smult, pts) * slider_repeats)
                end_xy = (ox, oy)

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
                    if slider_curve_type == "P":
                        sampled = sample_curve(slider_curve_type, slider_curve_points, steps)
                    else:
                        sampled = sample_curve(slider_curve_type, slider_curve_points, steps, pixel_length=target_len)
                    trimmed, arc_cum, arc_total = truncate_curve_to_length(sampled, slen)
                    cached_curve     = trimmed
                    cached_arc_cum   = arc_cum
                    cached_arc_total = arc_total
                    if trimmed:
                        ex, ey = trimmed[-1]
                        if slider_repeats % 2 == 0:
                            end_xy = (ox, oy)
                        else:
                            end_xy = (int(round(ex)), int(round(ey)))

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
                "cached_curve":     cached_curve,
                "cached_arc_cum":   cached_arc_cum,
                "cached_arc_total": cached_arc_total,
            })
        except (ValueError, IndexError):
            continue

    objs.sort(key=lambda o: o["time_ms"])
    return objs


def parse_osu_file(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    smult = parse_slider_multiplier(lines)
    pts   = parse_timing_points(lines)
    return parse_hit_objects(lines, smult, pts)


def resolve_path(data):
    try:
        path = os.path.join(data["folders"]["songs"], data["files"]["beatmap"])
        return path if os.path.isfile(path) else None
    except (KeyError, TypeError):
        return None


# Stream detection & path helpers

def detect_streams(objects, start_idx, thresh_ms=STREAM_MS_THRESH,
                   min_notes=4, look_ahead=50):
    """Stream detection with fast-path for very rapid streams (<50ms gaps)."""
    end_idx = min(start_idx + look_ahead, len(objects))
    stream_objs = objects[start_idx : end_idx + 1]
    max_stream_jump = 0

    for j in range(1, len(stream_objs)):
        dx = stream_objs[j]["x"] - stream_objs[j-1]["x"]
        dy = stream_objs[j]["y"] - stream_objs[j-1]["y"]
        jump_dist = math.hypot(dx, dy)
        max_stream_jump = max(max_stream_jump, jump_dist)

    if max_stream_jump > 150:
        return False, start_idx, (0, 0)

    # Fast-path: simplified detection for very fast streams
    if start_idx + 1 < len(objects):
        first_gap = objects[start_idx + 1]["time_ms"] - objects[start_idx]["time_ms"]
        if 0 < first_gap < 50:
            count = 1
            for i in range(start_idx + 1, min(start_idx + look_ahead, len(objects))):
                gap = objects[i]["time_ms"] - objects[i-1]["time_ms"]
                if gap > first_gap * 1.5:
                    break
                if objects[i]["is_slider"] or objects[i]["is_spinner"]:
                    break
                count += 1
            if count >= min_notes:
                dirs = []
                for j in range(start_idx + 1, start_idx + min(4, count)):
                    dx = objects[j]["x"] - objects[j-1]["x"]
                    dy = objects[j]["y"] - objects[j-1]["y"]
                    dist = math.hypot(dx, dy)
                    if dist > 0:
                        dirs.append((dx/dist, dy/dist))
                if dirs:
                    avg_dx = sum(d[0] for d in dirs) / len(dirs)
                    avg_dy = sum(d[1] for d in dirs) / len(dirs)
                    return True, start_idx + count - 1, (avg_dx, avg_dy)

    # Normal detection
    stream_notes = []
    gaps = []
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


def get_predicted_direction(curr_idx, objects, n=3):
    if curr_idx + 1 >= len(objects):
        return 1.0, 0.0

    # Fast-path: for very fast next notes just use immediate direction
    next_gap = objects[curr_idx + 1]["time_ms"] - objects[curr_idx]["time_ms"]
    if next_gap < 50:
        nx, ny = objects[curr_idx + 1]["x"], objects[curr_idx + 1]["y"]
        dx, dy = nx - objects[curr_idx]["x"], ny - objects[curr_idx]["y"]
        dist = math.hypot(dx, dy)
        if dist > 0:
            return dx/dist, dy/dist

    is_stream, end_idx, _ = detect_streams(objects, curr_idx + 1)
    look = min(n, PREDICT_NOTES // 2)
    path = []
    if is_stream:
        path = [(o["x"], o["y"]) for o in objects[curr_idx+1 : end_idx+2]]
    else:
        for i in range(curr_idx+1, min(curr_idx+1+look+1, len(objects))):
            path.append((objects[i]["x"], objects[i]["y"]))
    if len(path) < 2:
        nx, ny = objects[curr_idx+1]["x"], objects[curr_idx+1]["y"]
        dx, dy = nx - objects[curr_idx]["x"], ny - objects[curr_idx]["y"]
    else:
        fx, fy = path[-1]; sx, sy = path[0]
        dx, dy = fx - sx, fy - sy
    dist = math.sqrt(dx*dx + dy*dy)
    return (dx/dist, dy/dist) if dist > 0 else (1.0, 0.0)


def smooth_stream_polyline_corner(points, passes, alpha):
    if len(points) < 3 or passes <= 0 or alpha <= 0:
        return [(float(x), float(y)) for x, y in points]

    # Reduce smoothing for fast/large movements
    if len(points) > 1:
        total_dist = sum(
            math.hypot(points[i][0]-points[i-1][0], points[i][1]-points[i-1][1])
            for i in range(1, len(points))
        )
        avg_dist = total_dist / (len(points) - 1)
        if avg_dist > 50:
            passes = min(passes, 1)
            alpha *= 0.5

    pts = [(float(x), float(y)) for x, y in points]
    for _ in range(passes):
        new_pts = [pts[0]]
        for i in range(1, len(pts)-1):
            mx = 0.5 * (pts[i-1][0] + pts[i+1][0])
            my = 0.5 * (pts[i-1][1] + pts[i+1][1])
            new_pts.append(((1.0-alpha)*pts[i][0] + alpha*mx,
                             (1.0-alpha)*pts[i][1] + alpha*my))
        new_pts.append(pts[-1])
        pts = new_pts
    return pts


# Deterministic click jitter

_jitter_counter = 0

def next_click_jitter_ms(last_gap_ms=None):
    """
    Structured jitter. .3 for notes under 90ms gap (fast jumps/streams).
    Reduced to .5 for notes under 150ms gap.
    """
    if last_gap_ms is not None and last_gap_ms < 90:
        return (CLICK_VARIATION_MS * 0.4)

    global _jitter_counter
    _jitter_counter += 1
    t = _jitter_counter
    v = (math.sin(t * 1.2) * 0.6 + math.sin(t * 0.47 + 1.0) * 0.4)

    if last_gap_ms is not None and last_gap_ms < 150:
        return v * (CLICK_VARIATION_MS * 0.6)
    return v * CLICK_VARIATION_MS


# Per-frame velocity cap (effectively unlimited, kept for structure)

_last_cursor_pos = None
_last_cursor_time = None

# Performance monitoring
_perf_stats = {"cursor_updates": 0, "avg_update_ms": 0.0}

def mouse_move_capped(x, y, is_fast_segment=False):
    """Move mouse. Velocity cap is disabled (MAX_CURSOR_DELTA_PX = 999999).
    is_fast_segment flag bypasses the cap check entirely for clarity."""
    global _last_cursor_pos, _last_cursor_time
    x, y = float(x), float(y)
    now_t = now()

    if _last_cursor_pos is not None and not is_fast_segment:
        lx, ly = _last_cursor_pos
        dx, dy = x - lx, y - ly
        dist = math.hypot(dx, dy)
        if dist > MAX_CURSOR_DELTA_PX:
            scale = MAX_CURSOR_DELTA_PX / dist
            x = lx + dx * scale
            y = ly + dy * scale

    _last_cursor_pos  = (x, y)
    _last_cursor_time = now_t

    _perf_stats["cursor_updates"] += 1
    mouse_move(x, y)


# Segment builder

def build_movement_segments(objects, sync_wall, first_note_ms, rate):
    """Build movement segments. Sliders use pre-baked curves; streams treated as
    individual normal segments with arc allowed; fast jump flag added per segment."""
    segments = []
    n = len(objects)
    i = 0
    prev_wall = None
    prev_scr  = None

    while i < n:
        is_stream, end_idx, _ = detect_streams(objects, i)
        if is_stream:
            stream_objs = objects[i : end_idx + 1]
            for j, obj in enumerate(stream_objs):
                hit_wall = sync_wall + ((obj["time_ms"] + GAME_OFFSET_MS) - first_note_ms) / 1000.0 / rate
                tx, ty = osu_to_screen(obj["x"], obj["y"])
                if prev_wall is not None and hit_wall > prev_wall:
                    dt = hit_wall - prev_wall
                    dist = math.hypot(tx - prev_scr[0], ty - prev_scr[1])
                    is_fast = dt < (FAST_JUMP_THRESHOLD_MS / 1000.0)
                    segments.append({
                        "type": "normal",
                        "start": prev_wall, "end": hit_wall,
                        "x1": prev_scr[0], "y1": prev_scr[1],
                        "x2": tx, "y2": ty,
                        "no_arc": False,
                        "is_fast": is_fast,
                        "jump_distance": dist,
                        "obj_idx": i + j - 1,
                    })
                prev_wall = hit_wall
                prev_scr = (tx, ty)
            i = end_idx + 1
            continue

        obj     = objects[i]
        hit_wall = sync_wall + ((obj["time_ms"] + GAME_OFFSET_MS) - first_note_ms) / 1000.0 / rate
        end_wall = (sync_wall + ((obj["end_time_ms"] + GAME_OFFSET_MS) - first_note_ms) / 1000.0 / rate
                    if obj["end_time_ms"] is not None else None)
        tx, ty  = osu_to_screen(obj["x"], obj["y"])

        if obj["is_slider"] and end_wall:
            if prev_wall is not None and hit_wall > prev_wall:
                dt = hit_wall - prev_wall
                dist = math.hypot(tx - prev_scr[0], ty - prev_scr[1])
                is_fast = dt < (FAST_JUMP_THRESHOLD_MS / 1000.0)
                segments.append({
                    "type": "normal", "start": prev_wall, "end": hit_wall,
                    "x1": prev_scr[0], "y1": prev_scr[1], "x2": tx, "y2": ty,
                    "is_fast": is_fast, "jump_distance": dist,
                    "obj_idx": i - 1,
                })

            base_curve  = obj["cached_curve"]
            arc_cum     = obj["cached_arc_cum"]
            arc_total   = obj["cached_arc_total"]

            if base_curve is None:
                curve_points = obj.get("slider_curve_points") or [(obj["x"], obj["y"]), (obj["end_x"], obj["end_y"])]
                curve_type   = obj.get("slider_curve_type") or "L"
                pixel_length = obj.get("slider_length")
                target_len   = float(pixel_length) if pixel_length is not None else 0.0
                steps = max(180, min(7000, int(target_len * 1.8) + 120))
                base_curve  = sample_curve(curve_type, curve_points, steps)
                base_curve, arc_cum, arc_total = truncate_curve_to_length(base_curve, pixel_length)

            segments.append({
                "type": "slider", "start": hit_wall, "end": end_wall,
                "curve": base_curve, "arc_cum": arc_cum, "arc_total": arc_total,
                "repeats": int(obj.get("slider_repeats", 1) or 1),
                "no_arc": True, "locked": True, "is_fast": False,
                "obj_idx": i,
            })
            prev_wall = end_wall
            prev_scr  = osu_to_screen(obj["end_x"], obj["end_y"])

        elif obj["is_spinner"] and end_wall:
            if prev_wall is not None and hit_wall > prev_wall:
                dt = hit_wall - prev_wall
                dist = math.hypot(tx - prev_scr[0], ty - prev_scr[1])
                is_fast = dt < (FAST_JUMP_THRESHOLD_MS / 1000.0)
                segments.append({
                    "type": "normal", "start": prev_wall, "end": hit_wall,
                    "x1": prev_scr[0], "y1": prev_scr[1], "x2": tx, "y2": ty,
                    "is_fast": is_fast, "jump_distance": dist,
                    "obj_idx": i - 1,
                })
            prev_wall = end_wall
            prev_scr  = osu_to_screen(OSU_W // 2, OSU_H // 2)

        else:
            if prev_wall is not None and hit_wall > prev_wall:
                dt = hit_wall - prev_wall
                dist = math.hypot(tx - prev_scr[0], ty - prev_scr[1])
                is_fast = dt < (FAST_JUMP_THRESHOLD_MS / 1000.0)
                segments.append({
                    "type": "normal", "start": prev_wall, "end": hit_wall,
                    "x1": prev_scr[0], "y1": prev_scr[1], "x2": tx, "y2": ty,
                    "is_fast": is_fast, "jump_distance": dist,
                    "obj_idx": i - 1,
                })
            prev_wall = hit_wall
            prev_scr  = (tx, ty)

        i += 1

    return segments


# State machine

STATE_IDLE    = "IDLE"
STATE_ARMED   = "ARMED"
STATE_RUNNING = "RUNNING"
VALID_STATES  = {STATE_IDLE, STATE_ARMED, STATE_RUNNING}
ALLOWED_STATE_TRANSITIONS = {
    STATE_IDLE:    {STATE_ARMED, STATE_RUNNING},
    STATE_ARMED:   {STATE_RUNNING, STATE_IDLE},
    STATE_RUNNING: {STATE_IDLE, STATE_ARMED},
}

state_lock = threading.Lock()
_state = {
    "current_path":   None,
    "hit_objects":    [],
    "speed_change":   1.0,
    "stop_event":     threading.Event(),
    "relax_thread":   None,
    "status":         STATE_IDLE,
    "_auto_started":  False,
    "_manual_armed":  False,
    "_sync_anchor":   None,
    "cursor_target":  (0, 0),
}


def get_state(key=None):
    with state_lock:
        return _state[key] if key else _state.copy()


def set_state(updates):
    with state_lock:
        _state.update(updates)


def set_status(new_status):
    if new_status not in VALID_STATES:
        return
    with state_lock:
        old = _state["status"]
        if old == new_status:
            return
        if new_status not in ALLOWED_STATE_TRANSITIONS.get(old, set()):
            return
        _state["status"] = new_status
    print(f"[state] {old} -> {new_status}")


# SendInput / ctypes

MOUSEEVENTF_MOVE        = 0x0001
MOUSEEVENTF_ABSOLUTE    = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
KEYEVENTF_KEYDOWN       = 0x0000
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

VDESK_W = ctypes.windll.user32.GetSystemMetrics(78)
VDESK_H = ctypes.windll.user32.GetSystemMetrics(79)
VDESK_X = ctypes.windll.user32.GetSystemMetrics(76)
VDESK_Y = ctypes.windll.user32.GetSystemMetrics(77)
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
        x = max(base_x, min(base_x + span_w - 1.0, x))
        y = max(base_y, min(base_y + span_h - 1.0, y))
        flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    else:
        base_x, base_y = 0.0, 0.0
        span_w, span_h = float(PRIMARY_W), float(PRIMARY_H)
        x = max(0.0, min(span_w - 1.0, x))
        y = max(0.0, min(span_h - 1.0, y))
        flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
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


# Relax / click loop

def relax_loop(objects, sync_wall, first_note_ms, rate):
    stop_event = get_state("stop_event")
    stop_event.clear()

    moves   = []
    keys    = []
    spinner_periods = []
    slider_periods  = []

    key_idx = 0
    prev_time = None
    prev_key_xy = None

    for obj in objects:
        hit_wall = sync_wall + ((obj["time_ms"] + GAME_OFFSET_MS) - first_note_ms) / 1000.0 / rate
        end_wall = (sync_wall + ((obj["end_time_ms"] + GAME_OFFSET_MS) - first_note_ms) / 1000.0 / rate
                    if obj["end_time_ms"] is not None else None)
        tx, ty = osu_to_screen(obj["x"], obj["y"])
        moves.append((hit_wall, tx, ty))

        if obj["is_spinner"] and end_wall:
            spinner_periods.append((hit_wall, end_wall))
        if obj["is_slider"] and end_wall:
            slider_periods.append((hit_wall, end_wall))

        key = CLICK_KEYS[key_idx % 2]
        key_idx += 1

        gap_ms = None
        if prev_time is not None:
            gap_ms = obj["time_ms"] - prev_time
        prev_time = obj["time_ms"]

        jitter = next_click_jitter_ms(gap_ms)
        lead_ms = 0.0
        if ENABLE_CLICK_POS_GATE and ENABLE_JUMP_EARLY_LEAD and prev_key_xy is not None:
            dist = math.hypot(tx - prev_key_xy[0], ty - prev_key_xy[1])
            lead_ms = min(JUMP_EARLY_LEAD_MAX_MS, dist / max(1e-6, JUMP_EARLY_LEAD_PX_PER_MS))

        press_wall = hit_wall + HIT_BIAS_MS / 1000.0 + jitter / 1000.0 - (lead_ms / 1000.0)
        keys.append((press_wall, end_wall, key,
                      obj["is_spinner"], obj["is_slider"], tx, ty))
        prev_key_xy = (tx, ty)

    segments = build_movement_segments(objects, sync_wall, first_note_ms, rate)

    spinner_starts = [s for s, _ in spinner_periods]
    slider_starts  = [s for s, _ in slider_periods]

    def is_in_interval(wall, starts, intervals):
        if not intervals: return False
        i = bisect.bisect_right(starts, wall) - 1
        if i < 0: return False
        start, end = intervals[i]
        return start <= wall < end

    def is_in_slider_or_spinner(wall):
        return (is_in_interval(wall, spinner_starts, spinner_periods) or
                is_in_interval(wall, slider_starts,  slider_periods))

    angular_velocity = (SPINNER_RPM / 60.0) * 2 * math.pi * rate
    cx_spin, cy_spin = osu_to_screen(OSU_W * 0.5, OSU_H * 0.5)

    def cursor_worker():
        stop_evt = get_state("stop_event")

        if not segments:
            for wall, x, y in moves:
                if stop_evt.is_set(): return
                sleep_until(wall)
                mouse_move_capped(x, y)
            return

        first_seg = segments[0]
        if first_seg.get("type") == "normal":
            mouse_move_capped(first_seg["x1"], first_seg["y1"])
        else:
            curve = first_seg.get("curve") or []
            if curve:
                ox, oy = curve[0]
                mouse_move_capped(*osu_to_screen(ox, oy))

        seg_i = 0
        total = len(segments)
        while seg_i < total and not stop_evt.is_set():
            seg = segments[seg_i]
            now_wall = now()

            # Spinner handling
            if is_in_interval(now_wall, spinner_starts, spinner_periods):
                i_spin = max(0, bisect.bisect_right(spinner_starts, now_wall) - 1)
                i_spin = min(i_spin, len(spinner_periods) - 1)
                spin_start, spin_end = spinner_periods[i_spin]
                while now_wall < spin_end and not stop_evt.is_set():
                    elapsed = now_wall - spin_start
                    angle = angular_velocity * elapsed
                    mouse_move_capped(cx_spin + SPINNER_RADIUS * math.cos(angle),
                                        cy_spin + SPINNER_RADIUS * math.sin(angle))
                    if (spin_end - spin_start) < 0.05:
                        smart_sleep_until(now_wall + 0.0001)
                    else:
                        smart_sleep_until(now_wall + 0.001)
                    now_wall = now()
                continue

            if seg["type"] in ("stream", "slider"):
                start_wall = seg["start"]
                end_wall = seg["end"]
                curve = seg["curve"]
                arc_cum = seg.get("arc_cum") or []
                arc_total = seg.get("arc_total", 0.0)
                repeats = int(seg.get("repeats", 1) or 1) if seg["type"] == "slider" else 1
                nc = len(curve)

                if nc < 2:
                    seg_i += 1
                    continue

                if now_wall < start_wall:
                    sleep_until(start_wall)
                    now_wall = now()

                total_span = end_wall - start_wall
                if total_span <= ARCLENGTH_EPSILON:
                    ox, oy = curve[-1]
                    mouse_move_capped(*osu_to_screen(ox, oy))
                    seg_i += 1
                    continue

                while now_wall < end_wall and not stop_evt.is_set():
                    if is_in_interval(now_wall, spinner_starts, spinner_periods):
                        break
                    elapsed = max(0.0, min(total_span, now_wall - start_wall))
                    if seg["type"] == "stream":
                        u = elapsed / total_span
                    else:
                        if repeats <= 1:
                            u = elapsed / total_span
                        else:
                            span_dur = total_span / repeats
                            if span_dur <= ARCLENGTH_EPSILON:
                                u = 1.0
                            else:
                                span_idx = max(0, min(repeats-1, int(elapsed / span_dur)))
                                local = max(0.0, min(1.0, (elapsed - span_idx*span_dur) / span_dur))
                                u = local if (span_idx % 2 == 0) else (1.0 - local)

                    if arc_cum and arc_total > ARCLENGTH_EPSILON:
                        ox, oy = sample_stream_curve_by_arclength(curve, arc_cum, arc_total, u)
                    else:
                        ox, oy = curve[int(u * (nc - 1))]

                    mouse_move_capped(*osu_to_screen(ox, oy))
                    if total_span < 0.05:
                        smart_sleep_until(now_wall + 0.0001)
                    else:
                        smart_sleep_until(now_wall + 0.001)
                    now_wall = now()

                if now_wall >= end_wall:
                    if not is_in_interval(now_wall, spinner_starts, spinner_periods):
                        if seg["type"] == "slider" and repeats % 2 == 0:
                            ox, oy = curve[0]
                        else:
                            ox, oy = curve[-1]
                        mouse_move_capped(*osu_to_screen(ox, oy))

            else:  # normal segment
                start_wall = seg["start"]
                end_wall = seg["end"]
                x1, y1 = seg["x1"], seg["y1"]
                x2, y2 = seg["x2"], seg["y2"]
                is_fast = seg.get("is_fast", False)

                if now_wall < start_wall:
                    sleep_until(start_wall)
                    now_wall = now()

                if seg.get("locked", False):
                    mouse_move_capped(x2, y2)
                    sleep_until(end_wall)
                    seg_i += 1
                    continue

                if end_wall - start_wall < SEGMENT_MIN_DUR:
                    mouse_move_capped(x2, y2, is_fast_segment=True)
                    seg_i += 1
                    continue

                segment_duration = end_wall - start_wall

                # Fast jump handling - simplified and correct
                if is_fast and USE_BUSY_WAIT_FOR_FAST:
                    # Pre-calculate values for fast jumps
                    dt = end_wall - start_wall
                    dx = x2 - x1
                    dy = y2 - y1
                    
                    # Determine if arc should be used
                    use_arc = False
                    if ARC_MODE and not seg.get("no_arc", False):
                        jump_speed = seg.get("jump_distance", 0.0) / (dt * 1000.0) if dt > 0 else 0
                        if not (DISABLE_ARC_ON_FAST_JUMPS and jump_speed > MAX_JUMP_SPEED_PX_MS):
                            use_arc = True
                    
                    # Busy-wait loop for fast jumps
                    while now_wall < end_wall and not stop_evt.is_set():
                        if is_in_interval(now_wall, spinner_starts, spinner_periods):
                            break
                        
                        elapsed = now_wall - start_wall
                        if use_arc:
                            obj_idx = max(0, min(seg.get("obj_idx", 0), len(objects)-1))
                            osu1x, osu1y = screen_to_osu(x1, y1)
                            osu2x, osu2y = screen_to_osu(x2, y2)
                            dist_osu_px = math.hypot(osu2x-osu1x, osu2y-osu1y)
                            
                            # For very large jumps (>200 osu px), don't use prediction
                            # Just use perpendicular arc like the first script
                            if dist_osu_px > 200:
                                pred_dir_x, pred_dir_y = None, None
                            else:
                                pred_dir_x, pred_dir_y = get_predicted_direction(obj_idx, objects)
                            x, y = apply_predictive_arc(elapsed, dt, x1, y1, x2, y2,
                                                        dist_osu_px, pred_dir_x, pred_dir_y)
                        else:
                            eased = smooth_easing(max(0.0, min(1.0, elapsed / dt)))
                            x = x1 + dx * eased
                            y = y1 + dy * eased
                        
                        mouse_move_capped(x, y, is_fast_segment=True)
                        now_wall = now()
                        # No sleep - busy wait for next frame
                    
                    # Final snap
                    mouse_move_capped(x2, y2, is_fast_segment=True)
                
                else:
                    # Normal speed handling with sleep
                    while now_wall < end_wall and not stop_evt.is_set():
                        if is_in_interval(now_wall, spinner_starts, spinner_periods):
                            break

                        dt = end_wall - start_wall
                        if dt <= SEGMENT_MIN_DUR:
                            mouse_move_capped(x2, y2)
                            break
                        
                        elapsed = now_wall - start_wall
                        in_special = is_in_slider_or_spinner(now_wall)
                        no_arc = seg.get("no_arc", False)
                        use_arc = ARC_MODE and not in_special and not no_arc

                        if use_arc:
                            # Disable arc for high-speed jumps
                            jump_speed = seg.get("jump_distance", 0.0) / (dt * 1000.0) if dt > 0 else 0
                            if DISABLE_ARC_ON_FAST_JUMPS and jump_speed > MAX_JUMP_SPEED_PX_MS:
                                use_arc = False

                        if use_arc:
                            obj_idx = max(0, min(seg.get("obj_idx", 0), len(objects)-1))
                            osu1x, osu1y = screen_to_osu(x1, y1)
                            osu2x, osu2y = screen_to_osu(x2, y2)
                            dist_osu_px = math.hypot(osu2x-osu1x, osu2y-osu1y)
                            pred_dir_x, pred_dir_y = get_predicted_direction(obj_idx, objects)
                            x, y = apply_predictive_arc(elapsed, dt, x1, y1, x2, y2,
                                                        dist_osu_px, pred_dir_x, pred_dir_y)
                        else:
                            eased = smooth_easing(elapsed / dt)
                            x = x1 + (x2 - x1) * eased
                            y = y1 + (y2 - y1) * eased

                        mouse_move_capped(x, y)
                        
                        # Adaptive sleep based on remaining time
                        remaining = end_wall - now_wall
                        if remaining > 0.002:
                            smart_sleep_until(now_wall + 0.001)
                        else:
                            # Busy wait for final 2ms
                            while now() < end_wall:
                                mouse_move_capped(x2, y2)
                            break
                        
                        now_wall = now()

                    if now_wall >= end_wall:
                        if not is_in_interval(now_wall, spinner_starts, spinner_periods):
                            mouse_move_capped(x2, y2)

            seg_i += 1

    def key_worker():
        stop_evt = get_state("stop_event")
        for press_wall, end_wall, key, is_spinner, is_slider, sx, sy in keys:
            if stop_evt.is_set():
                return
            sleep_until(press_wall)

            if ENABLE_CLICK_POS_GATE and not stop_evt.is_set():
                deadline = press_wall + (CLICK_POS_MAX_LATE_MS / 1000.0)
                while True:
                    cx, cy = get_cursor_pos()
                    if (cx - sx) * (cx - sx) + (cy - sy) * (cy - sy) <= (CLICK_POS_WINDOW_PX * CLICK_POS_WINDOW_PX):
                        break
                    if now() >= deadline or stop_evt.is_set():
                        break
                    time.sleep(CLICK_POS_POLL_SLEEP_S)

            key_down(key)
            if is_spinner and end_wall:
                sleep_until(end_wall)
            elif is_slider and end_wall:
                sleep_until(end_wall)
            else:
                sleep_until(press_wall + 0.020)
            key_up(key)
        set_status(STATE_IDLE)

    if not RELAX_MODE:
        threading.Thread(target=cursor_worker, daemon=True).start()
    threading.Thread(target=key_worker, daemon=True).start()


# Hotkeys & tosu WebSocket

def on_f1():
    with state_lock:
        _state["_manual_armed"] = True
    set_status(STATE_ARMED)


def on_f2():
    get_state("stop_event").set()
    set_status(STATE_IDLE)


def start_from_tosu_live(live_ms):
    hit_objects = get_state("hit_objects")
    if not hit_objects:
        return
    stop_event = get_state("stop_event")
    stop_event.set()
    relax_thread = get_state("relax_thread")
    if relax_thread and relax_thread.is_alive():
        relax_thread.join(timeout=0.5)
    first = hit_objects[0]["time_ms"]
    rate  = get_state("speed_change")
    live_ms = float(live_ms)
    sync_wall = now() - (((live_ms + GAME_OFFSET_MS) - first) / 1000.0 / rate)
    set_state({"_sync_anchor": None})
    set_status(STATE_RUNNING)
    new_thread = threading.Thread(target=relax_loop,
                                  args=(hit_objects, sync_wall, first, rate),
                                  daemon=True)
    set_state({"relax_thread": new_thread})
    new_thread.start()


def on_message(ws, message):
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return

    state_name = data.get("state", {}).get("name")
    if state_name != "play":
        set_state({"_auto_started": False, "_manual_armed": False, "_sync_anchor": None})
        set_status(STATE_IDLE)

    snap = get_state()
    new_rate = 1.0
    for mod in data.get("play", {}).get("mods", {}).get("array", []):
        sc = mod.get("settings", {}).get("speed_change")
        if sc is not None:
            new_rate = float(sc); break
    if new_rate != snap["speed_change"]:
        set_state({"speed_change": new_rate})
        print(f"[tosu] Rate: {new_rate}x")
        snap = get_state()

    if (AUTO_START and state_name == "play" and snap["hit_objects"]
            and snap["_manual_armed"] and not snap["_auto_started"]):
        live_ms = data.get("beatmap", {}).get("time", {}).get("live")
        if live_ms is not None:
            set_state({"_auto_started": True, "_manual_armed": False})
            threading.Thread(target=start_from_tosu_live, args=(live_ms,), daemon=True).start()

    path = resolve_path(data)
    if path and path != snap["current_path"]:
        try:
            hit_objects = parse_osu_file(path)
            updated = get_state("speed_change")
            set_state({"current_path": path, "hit_objects": hit_objects})
            bm = data.get("beatmap", {})
            print(f"[tosu] {bm.get('artist','?')} - {bm.get('title','?')} "
                  f"[{bm.get('version','?')}]  |  {len(hit_objects)} objects  |  {updated}x")
        except Exception as e:
            print(f"[tosu] Parse error: {e}")


def on_error(ws, e):
    print(f"[tosu] Error: {e}")


def on_close(ws, *_):
    time.sleep(3)
    start_ws()


def start_ws():
    ws = websocket.WebSocketApp(TOSU_WS_URL,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    threading.Thread(target=ws.run_forever, daemon=True).start()


# Main

if __name__ == "__main__":
    keyboard.add_hotkey(SYNC_HOTKEY, on_f1)
    keyboard.add_hotkey(STOP_HOTKEY, on_f2)
    keyboard.add_hotkey("r", lambda: globals().update(RELAX_MODE=not RELAX_MODE))
    start_ws()
    keyboard.wait()
