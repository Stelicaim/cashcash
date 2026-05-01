#!/usr/bin/env python3
"""
inpaint.py — Șterge captions arse (hardcoded) dintr-un videoclip.

Mod de utilizare (apelat automat de server.js):
    python3 inpaint.py <input_path> <output_path> [options]

Opțiuni JSON (al 3-lea argument, opțional):
    {
      "zone_top_pct": 0.72,     # de unde începe zona de căutare (72% de sus)
      "zone_bot_pct": 0.92,     # unde se termină zona (92% de sus)
      "white_threshold": 200,   # prag alb pentru detectare text
      "min_blob_area": 400,     # aria minimă blob pentru a fi considerat text
      "inpaint_radius": 6,      # raza inpainting OpenCV
      "dilate_iters": 2         # iterații dilatare mască
    }
"""

import cv2
import numpy as np
import sys
import os
import json
import time
import subprocess
import tempfile

# ── Config ──────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "zone_top_pct":      0.72,
    "zone_bot_pct":      0.92,
    "zone_left_pct":     0.0,
    "zone_right_pct":    1.0,
    # ── Detecție culoare ──────────────────────────────────────────
    # mode: "white"  → prag RGB (compatibil cu versiunile vechi)
    #        "hsv"   → interval HSV precis (galben, roșu, verde etc.)
    #        "both"  → încearcă white + hsv și unește măștile
    "color_mode":        "white",
    "white_threshold":   200,    # folosit când mode="white" sau "both"
    "text_hue":          30,     # centrul nuanței în HSV OpenCV (0–180)
    "text_hue_range":    15,     # ±toleranță de nuanță
    "text_sat_min":      80,     # saturație minimă (0–255)
    "text_val_min":      100,    # luminozitate minimă (0–255)
    # ── Blob & inpainting ─────────────────────────────────────────
    "min_blob_area":     400,
    "inpaint_radius":    6,
    "dilate_iters":      2,
    "dilate_kernel":     11,
}

# Mapare culori comune → HSV (valorile OpenCV: H 0-180, S/V 0-255)
COLOR_PRESETS = {
    "white":  {"color_mode": "white"},
    "yellow": {"color_mode": "hsv", "text_hue": 30,  "text_hue_range": 15, "text_sat_min": 100, "text_val_min": 120},
    "orange": {"color_mode": "hsv", "text_hue": 15,  "text_hue_range": 12, "text_sat_min": 120, "text_val_min": 120},
    "red":    {"color_mode": "hsv", "text_hue": 0,   "text_hue_range": 10, "text_sat_min": 120, "text_val_min": 100},
    "green":  {"color_mode": "hsv", "text_hue": 60,  "text_hue_range": 15, "text_sat_min": 80,  "text_val_min": 80},
    "blue":   {"color_mode": "hsv", "text_hue": 110, "text_hue_range": 15, "text_sat_min": 80,  "text_val_min": 80},
    "cyan":   {"color_mode": "hsv", "text_hue": 90,  "text_hue_range": 15, "text_sat_min": 80,  "text_val_min": 80},
}

def log(msg):
    """Trimite mesaj JSON pe stdout — server.js îl parsează."""
    print(json.dumps(msg), flush=True)

def progress(pct, message=""):
    log({"type": "progress", "percent": pct, "message": message})

def info(message):
    log({"type": "info", "message": message})

def error(message):
    log({"type": "error", "message": message})

# ── Detecție mască text ──────────────────────────────────────────────────────

def detect_white(zone, threshold):
    """Detectează pixeli aproape de alb pur (RGB)."""
    return (
        (zone[:, :, 0] > threshold) &
        (zone[:, :, 1] > threshold) &
        (zone[:, :, 2] > threshold)
    ).astype(np.uint8) * 255

def detect_hsv(zone, hue, hue_range, sat_min, val_min):
    """
    Detectează pixeli după culoare în spațiul HSV.
    Gestionează și wrap-around-ul nuanței (ex: roșu care trece prin 0/180).
    """
    hsv = cv2.cvtColor(zone, cv2.COLOR_BGR2HSV)
    h1 = int(hue - hue_range)
    h2 = int(hue + hue_range)
    lo_sv = np.array([0,       sat_min, val_min], dtype=np.uint8)
    hi_sv = np.array([180, 255, 255], dtype=np.uint8)

    if h1 < 0:
        # Wrap la stânga (ex: roșu)
        m1 = cv2.inRange(hsv, np.array([0,       sat_min, val_min]), np.array([h2,   255, 255]))
        m2 = cv2.inRange(hsv, np.array([180+h1,  sat_min, val_min]), np.array([180,  255, 255]))
        return cv2.bitwise_or(m1, m2)
    elif h2 > 180:
        # Wrap la dreapta
        m1 = cv2.inRange(hsv, np.array([h1,    sat_min, val_min]), np.array([180,      255, 255]))
        m2 = cv2.inRange(hsv, np.array([0,     sat_min, val_min]), np.array([h2-180,   255, 255]))
        return cv2.bitwise_or(m1, m2)
    else:
        return cv2.inRange(hsv, np.array([h1, sat_min, val_min]), np.array([h2, 255, 255]))

def build_text_mask(zone, cfg):
    """
    Detectează pixelii de text și returnează masca dilatată.
    Suportă alb (RGB), culori (HSV) sau ambele combinate.
    """
    mode = cfg.get("color_mode", "white")
    raw  = None

    if mode == "white":
        raw = detect_white(zone, cfg["white_threshold"])

    elif mode == "hsv":
        raw = detect_hsv(
            zone,
            cfg.get("text_hue",       30),
            cfg.get("text_hue_range", 15),
            cfg.get("text_sat_min",   80),
            cfg.get("text_val_min",  100),
        )

    elif mode == "both":
        w = detect_white(zone, cfg["white_threshold"])
        h = detect_hsv(
            zone,
            cfg.get("text_hue",       30),
            cfg.get("text_hue_range", 15),
            cfg.get("text_sat_min",   80),
            cfg.get("text_val_min",  100),
        )
        raw = cv2.bitwise_or(w, h)

    if raw is None or raw.sum() == 0:
        return None

    # Dilată pentru a acoperi conturul negru + antialiasing
    k      = cfg.get("dilate_kernel", 11)
    kernel = np.ones((k, k), np.uint8)
    mask   = cv2.dilate(raw, kernel, iterations=cfg.get("dilate_iters", 2))

    # Filtrează zgomot — păstrează doar blob-uri mari (text real)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    clean = np.zeros_like(mask)
    for i in range(1, n_labels):
        if stats[i, cv2.CC_STAT_AREA] > cfg.get("min_blob_area", 400):
            clean[labels == i] = 255

    return clean if clean.sum() > 0 else None

# ── Procesare video ──────────────────────────────────────────────────────────

def process_video(input_path, output_path, cfg):
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        error(f"Nu pot deschide videoclipul: {input_path}")
        sys.exit(1)

    fps    = cap.get(cv2.CAP_PROP_FPS)
    w      = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h      = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dur    = total / fps if fps > 0 else 0

    info(f"Video: {w}x{h} @ {fps:.2f}fps | {total} frames | {dur:.1f}s")
    progress(1, "Analizez video...")

    # Output temporar (fără audio) — folosim mp4v
    tmp_video = output_path + ".tmp.mp4"
    fourcc    = cv2.VideoWriter_fourcc(*"mp4v")
    writer    = cv2.VideoWriter(tmp_video, fourcc, fps, (w, h))

    if not writer.isOpened():
        error("Nu pot crea fișierul de output.")
        sys.exit(1)

    # Zona de căutare captions (toate cele 4 margini)
    y1 = int(h * cfg["zone_top_pct"])
    y2 = int(h * cfg["zone_bot_pct"])
    x1 = int(w * cfg.get("zone_left_pct",  0.0))
    x2 = int(w * cfg.get("zone_right_pct", 1.0))
    y1 = max(0, min(h-1, y1)); y2 = max(y1+1, min(h, y2))
    x1 = max(0, min(w-1, x1)); x2 = max(x1+1, min(w, x2))

    info(f"Zona captions: y={y1}–{y2}, x={x1}–{x2} ({y2-y1}×{x2-x1}px)")
    progress(2, "Procesez frame-uri...")

    t0            = time.time()
    frame_idx     = 0
    inpainted_cnt = 0
    last_pct      = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        zone = frame[y1:y2, x1:x2]
        mask = build_text_mask(zone, cfg)

        if mask is not None:
            # Inpaintează doar bounding box-ul strâns al textului detectat
            rows = np.where(mask.any(axis=1))[0]
            cols = np.where(mask.any(axis=0))[0]

            if len(rows) > 0 and len(cols) > 0:
                # Adaugă padding mic în jurul textului
                pad = 4
                r0 = max(0, rows[0] - pad)
                r1 = min(mask.shape[0], rows[-1] + pad + 1)
                c0 = max(0, cols[0] - pad)
                c1 = min(mask.shape[1], cols[-1] + pad + 1)

                patch      = zone[r0:r1, c0:c1]
                patch_mask = mask[r0:r1, c0:c1]

                if patch_mask.sum() > 0:
                    inpainted = cv2.inpaint(
                        patch, patch_mask,
                        cfg["inpaint_radius"],
                        cv2.INPAINT_TELEA
                    )
                    zone[r0:r1, c0:c1] = inpainted
                    frame[y1:y2, x1:x2] = zone
                    inpainted_cnt += 1

        writer.write(frame)
        frame_idx += 1

        # Trimite progress la fiecare 1%
        pct = min(int(frame_idx / total * 90), 90)  # 90% = procesare frames
        if pct > last_pct:
            elapsed = time.time() - t0
            eta     = (elapsed / frame_idx) * (total - frame_idx) if frame_idx > 0 else 0
            progress(pct, f"Frame {frame_idx}/{total} • ETA {eta:.0f}s")
            last_pct = pct

    cap.release()
    writer.release()

    elapsed = time.time() - t0
    info(f"Procesare frames gata în {elapsed:.1f}s | frames cu inpainting: {inpainted_cnt}/{total}")
    progress(92, "Remux audio...")

    # ── Remux audio (ffmpeg) ─────────────────────────────────────────────────
    # Caută ffmpeg: mai întâi în node_modules (ffmpeg-static), apoi în PATH
    ffmpeg_bin = find_ffmpeg()
    if not ffmpeg_bin:
        error("ffmpeg nu a fost găsit! Asigură-te că ai instalat npm dependencies.")
        sys.exit(1)

    remux_cmd = [
        ffmpeg_bin,
        "-i",  tmp_video,
        "-i",  input_path,
        "-map", "0:v",
        "-map", "1:a?",       # '?' = opțional (nu crapa dacă nu e audio)
        "-c",   "copy",
        "-shortest",
        "-movflags", "+faststart",
        output_path,
        "-y"
    ]

    result = subprocess.run(remux_cmd, capture_output=True)
    os.unlink(tmp_video)

    if result.returncode != 0:
        error(f"ffmpeg remux eșuat: {result.stderr.decode()[:300]}")
        sys.exit(1)

    progress(100, "Gata!")
    total_time = time.time() - t0
    info(f"Total: {total_time:.1f}s | Output: {output_path}")
    log({"type": "done", "output": output_path, "elapsed": round(total_time, 1)})


def find_ffmpeg():
    """Caută ffmpeg în node_modules/ffmpeg-static sau în PATH."""
    candidates = [
        # Relativ la script (lângă server.js)
        os.path.join(os.path.dirname(__file__), "node_modules", "ffmpeg-static", "ffmpeg"),
        os.path.join(os.path.dirname(__file__), "node_modules", "ffmpeg-static", "ffmpeg.exe"),
        # PATH
        "ffmpeg",
    ]
    for c in candidates:
        try:
            r = subprocess.run([c, "-version"], capture_output=True)
            if r.returncode == 0:
                return c
        except FileNotFoundError:
            continue
    return None


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 inpaint.py <input> <output> [config_json]")
        sys.exit(1)

    input_path  = sys.argv[1]
    output_path = sys.argv[2]

    # Config opțional din al 3-lea argument (JSON string)
    cfg = DEFAULT_CONFIG.copy()
    if len(sys.argv) >= 4:
        try:
            overrides = json.loads(sys.argv[3])
            cfg.update(overrides)
        except json.JSONDecodeError as e:
            error(f"Config JSON invalid: {e}")

    if not os.path.exists(input_path):
        error(f"Fișierul de input nu există: {input_path}")
        sys.exit(1)

    process_video(input_path, output_path, cfg)
