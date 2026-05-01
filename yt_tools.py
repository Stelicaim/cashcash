#!/usr/bin/env python3
"""
yt_tools.py — YouTube downloader + transcript extractor
Apelat de server.js via spawn.

Fixes:
  - Download: detectează automat ffmpeg-static din node_modules
              + forțează output mp4
  - Transcript: traduce fiecare segment cu timestamp-ul lui
"""

import sys
import os
import json
import re
import glob
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Helpers ──────────────────────────────────────────────────────────────────

def log(msg):
    print(json.dumps(msg), flush=True)

def progress(pct, message=""):
    log({"type": "progress", "percent": int(pct), "message": message})

def info(msg):
    log({"type": "info", "message": msg})

def error(msg):
    log({"type": "error", "message": msg})

# ── Find ffmpeg ───────────────────────────────────────────────────────────────

def find_ffmpeg():
    """
    Caută ffmpeg în:
    1. node_modules/ffmpeg-static/ (instalat de npm)
    2. PATH sistem
    Returnează (binary_path, dir_path) sau (None, None).
    """
    candidates = [
        os.path.join(SCRIPT_DIR, "node_modules", "ffmpeg-static", "ffmpeg"),
        os.path.join(SCRIPT_DIR, "node_modules", "ffmpeg-static", "ffmpeg.exe"),
        "ffmpeg",
    ]
    for c in candidates:
        try:
            r = subprocess.run([c, "-version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                ffmpeg_bin = c
                ffmpeg_dir = os.path.dirname(os.path.abspath(c)) if os.path.isabs(c) else None
                return ffmpeg_bin, ffmpeg_dir
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None, None

# ── Find yt-dlp ───────────────────────────────────────────────────────────────

def find_ytdlp():
    for name in ["yt-dlp", "yt_dlp"]:
        try:
            r = subprocess.run([name, "--version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return [name]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    # Fallback: modul Python
    try:
        r = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True, timeout=5
        )
        if r.returncode == 0:
            return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        pass
    return None

# ════════════════════════════════════════════════════
#  DOWNLOAD
# ════════════════════════════════════════════════════

def download_video(url, opts):
    output_path = opts.get("output_path", "/tmp/yt_video.mp4")
    quality     = str(opts.get("quality", "1080"))

    ytdlp = find_ytdlp()
    if not ytdlp:
        error("yt-dlp nu este instalat. Rulează: pip install yt-dlp")
        sys.exit(1)

    ffmpeg_bin, ffmpeg_dir = find_ffmpeg()
    if ffmpeg_bin:
        info(f"ffmpeg găsit: {ffmpeg_bin}")
    else:
        info("ATENȚIE: ffmpeg nu a fost găsit — video și audio pot fi separate.")

    # Output template fără extensie fixă — yt-dlp alege extensia
    output_dir  = os.path.dirname(output_path)
    base_name   = os.path.splitext(os.path.basename(output_path))[0]
    output_tmpl = os.path.join(output_dir, base_name + ".%(ext)s")

    # Format selection
    if quality == "audio":
        fmt = "bestaudio[ext=m4a]/bestaudio/best"
    else:
        # Fără constrângere de extensie pe video — yt-dlp + ffmpeg fac merge
        fmt = (
            f"bestvideo[height<={quality}]+bestaudio"
            f"/best[height<={quality}]"
            f"/best"
        )

    cmd = ytdlp + [
        "--format",               fmt,
        "--output",               output_tmpl,
        "--merge-output-format",  "mp4",      # ← forțează mp4 la output
        "--newline",
        "--no-playlist",
        "--no-warnings",
        "--progress",
    ]

    # Dacă am găsit ffmpeg-static, îl dăm explicit lui yt-dlp
    if ffmpeg_dir:
        cmd += ["--ffmpeg-location", ffmpeg_dir]
    elif ffmpeg_bin and ffmpeg_bin != "ffmpeg":
        cmd += ["--ffmpeg-location", os.path.dirname(ffmpeg_bin)]

    cmd.append(url)

    info(f"CMD: {' '.join(cmd[:6])} ...")
    progress(1, "Se conectează la YouTube...")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    last_pct = 0
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        m = re.search(r"\[download\]\s+([\d.]+)%", line)
        if m:
            pct = float(m.group(1))
            if pct > last_pct + 0.5:
                last_pct = pct
                progress(min(int(pct * 0.95), 95), f"Descărcând {pct:.1f}%...")
        elif "[Merger]" in line or "Merging" in line:
            progress(97, "Se unesc stream-urile video+audio...")
        elif "[ExtractAudio]" in line:
            progress(97, "Se extrage audio...")
        elif line.startswith("[") and "ERROR" in line.upper():
            info(f"yt-dlp: {line}")

    proc.wait()

    if proc.returncode != 0:
        error(
            "yt-dlp a eșuat.\n"
            "Posibile cauze:\n"
            "• URL invalid sau video privat/geo-blocat\n"
            "• yt-dlp necesită update: pip install -U yt-dlp\n"
            "• ffmpeg lipsește (necesar pentru merge video+audio)"
        )
        sys.exit(1)

    # Găsește fișierul creat (ignoră .part, .ytdl)
    pattern = os.path.join(output_dir, base_name + ".*")
    matches = [
        f for f in glob.glob(pattern)
        if not f.endswith(".part") and not f.endswith(".ytdl")
    ]

    if not matches:
        error(
            f"Fișierul descărcat nu a fost găsit.\n"
            f"Pattern: {pattern}\n"
            f"Director: {os.listdir(output_dir)}"
        )
        sys.exit(1)

    final_path = matches[0]
    size = os.path.getsize(final_path)
    info(f"Output: {final_path} ({size // 1024 // 1024} MB)")
    progress(100, "Gata!")
    log({"type": "done", "output": final_path, "size": size})


# ════════════════════════════════════════════════════
#  TRANSCRIPT
# ════════════════════════════════════════════════════

def extract_video_id(url):
    for p in [
        r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"shorts/([a-zA-Z0-9_-]{11})",
        r"embed/([a-zA-Z0-9_-]{11})",
    ]:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def segments_to_timestamped(segments):
    """Construiește textul cu timestamp-uri din segmente."""
    lines = []
    for s in segments:
        t = int(s.get("start", 0))
        mm, ss = divmod(t, 60)
        lines.append(f"[{mm:02d}:{ss:02d}] {s.get('text','').replace(chr(10),' ').strip()}")
    return "\n".join(lines)


def segments_to_plain(segments):
    """Construiește textul simplu din segmente."""
    return " ".join(
        s.get("text", "").replace("\n", " ").strip()
        for s in segments
        if s.get("text", "").strip()
    )


def translate_segments(segments, target_lang):
    """
    Traduce segmentele păstrând structura cu timestamp-uri.
    
    Strategie: grupăm textele cu separator unic, traducem blocul,
    splituitm înapoi și reataşăm timestamp-urile.
    Dacă splittuil eșuează, facem fallback la traducere globală.
    """
    from deep_translator import GoogleTranslator

    SEP       = "\n|||SEP|||\n"
    BATCH     = 80      # segmente per request de traducere
    CHAR_LIM  = 4500    # limita de caractere Google Translate

    translator  = GoogleTranslator(source="auto", target=target_lang)
    translated  = [dict(s) for s in segments]  # copie

    i = 0
    batch_num = 0
    total_batches = (len(segments) + BATCH - 1) // BATCH

    while i < len(segments):
        batch_segs = segments[i:i + BATCH]
        texts      = [s.get("text", "").replace("\n", " ").strip() for s in batch_segs]

        # Construiește blocul de traducere
        block = SEP.join(texts)

        # Dacă blocul depășește limita, reduce batch-ul
        if len(block) > CHAR_LIM:
            # Redu batch-ul progresiv până încape
            sub_batch = []
            sub_len   = 0
            for t in texts:
                if sub_len + len(t) + len(SEP) > CHAR_LIM and sub_batch:
                    break
                sub_batch.append(t)
                sub_len += len(t) + len(SEP)
            # Dacă tot un singur segment e prea lung, îl trunchiăm
            if not sub_batch:
                sub_batch = [texts[0][:CHAR_LIM]]
            texts      = sub_batch
            batch_segs = batch_segs[:len(sub_batch)]
            block      = SEP.join(texts)

        try:
            translated_block = translator.translate(block)
            # Split înapoi
            parts = translated_block.split("|||SEP|||")
            parts = [p.strip().strip("\n") for p in parts]

            if len(parts) == len(batch_segs):
                for j, seg in enumerate(batch_segs):
                    translated[i + j]["text"] = parts[j]
            else:
                # Fallback: distribuie textul tradus uniform
                info(f"Batch {batch_num+1}: split mismatch ({len(parts)} vs {len(batch_segs)}), fallback")
                combined = " ".join(parts)
                words    = combined.split()
                per_seg  = max(1, len(words) // len(batch_segs))
                for j in range(len(batch_segs)):
                    start_w = j * per_seg
                    end_w   = start_w + per_seg if j < len(batch_segs)-1 else len(words)
                    translated[i + j]["text"] = " ".join(words[start_w:end_w])

        except Exception as e:
            info(f"Eroare traducere batch {batch_num+1}: {e} — se păstrează original")

        batch_num += 1
        pct = 65 + int(batch_num / total_batches * 28)
        progress(min(pct, 93), f"Traducere... {batch_num}/{total_batches} blocuri")

        i += len(batch_segs)

    return translated


def get_transcript(url, opts):
    target_lang = opts.get("target_lang", None)

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        error("youtube-transcript-api nu este instalat. Rulează: pip install youtube-transcript-api")
        sys.exit(1)

    vid_id = extract_video_id(url)
    if not vid_id:
        error("URL invalid. Nu am putut extrage ID-ul video.")
        sys.exit(1)

    info(f"Video ID: {vid_id}")
    progress(10, "Se obține transcrierea...")

    preferred = ["ro", "en", "fr", "de", "es", "it", "pt", "ru", "ja", "ko"]
    segments  = None
    original_lang = "unknown"
    last_exc  = None

    # ── Metoda 1: get_transcript direct (compatibil toate versiunile) ─────────
    for lang in preferred:
        try:
            raw = YouTubeTranscriptApi.get_transcript(vid_id, languages=[lang])
            segments      = _normalize_segments(raw)
            original_lang = lang
            info(f"Transcriere OK (lang={lang})")
            break
        except Exception as e:
            last_exc = e

    if segments is None:
        # Orice limbă disponibilă
        try:
            raw = YouTubeTranscriptApi.get_transcript(vid_id)
            segments      = _normalize_segments(raw)
            original_lang = "auto"
            info("Transcriere OK (limbă automată)")
        except Exception as e:
            last_exc = e

    # ── Metoda 2: API nou >= 0.6 ──────────────────────────────────────────────
    if segments is None:
        try:
            api  = YouTubeTranscriptApi()
            tl   = api.list(vid_id)
            t    = _pick_transcript(tl, preferred)
            raw  = t.fetch()
            segments      = _normalize_segments(raw)
            original_lang = getattr(t, "language_code", "unknown")
            info(f"Transcriere OK (API nou, lang={original_lang})")
        except Exception as e:
            last_exc = e

    # ── Metoda 3: API vechi < 0.6 ─────────────────────────────────────────────
    if segments is None:
        try:
            tl   = YouTubeTranscriptApi.list_transcripts(vid_id)
            t    = _pick_transcript(tl, preferred)
            raw  = t.fetch()
            segments      = _normalize_segments(raw)
            original_lang = getattr(t, "language_code", "unknown")
            info(f"Transcriere OK (API vechi, lang={original_lang})")
        except Exception as e:
            last_exc = e

    if segments is None:
        msg = str(last_exc) if last_exc else "Eroare necunoscută"
        if "disabled" in msg.lower():
            error("Transcrierea este dezactivată pentru acest video.")
        elif "NoTranscriptFound" in msg or "Could not retrieve" in msg:
            error("Nu există nicio transcriere disponibilă pentru acest video.")
        else:
            error(f"Nu pot accesa transcrierea: {msg}")
        sys.exit(1)

    progress(55, "Se procesează textul...")

    # ── Construiește textele originale ────────────────────────────────────────
    plain_text       = segments_to_plain(segments)
    timestamped_text = segments_to_timestamped(segments)

    translated_plain      = plain_text
    translated_timestamped = timestamped_text
    translated_lang        = original_lang

    # ── Traducere segment cu segment ─────────────────────────────────────────
    should_translate = (
        target_lang
        and target_lang.strip()
        and target_lang not in ("none", "")
        and target_lang != original_lang
    )

    if should_translate:
        progress(63, f"Se traduce în {target_lang}...")
        try:
            from deep_translator import GoogleTranslator  # noqa — verifică instalarea

            # Traduce fiecare segment păstrând structura
            translated_segs = translate_segments(segments, target_lang)

            # Reconstruiește AMBELE formate din segmentele traduse
            translated_plain       = segments_to_plain(translated_segs)
            translated_timestamped = segments_to_timestamped(translated_segs)
            translated_lang        = target_lang

            info(f"Traducere completă: {len(translated_plain)} caractere")

        except ImportError:
            error("deep-translator nu este instalat. Rulează: pip install deep-translator")
            sys.exit(1)
        except Exception as e:
            info(f"Eroare traducere: {e} — se returnează textul original")

    progress(100, "Gata!")
    log({
        "type":             "done",
        "plain":            translated_plain,
        "timestamped":      translated_timestamped,   # ← tradus, cu timestamps
        "original_lang":    original_lang,
        "translated_lang":  translated_lang,
        "segment_count":    len(segments),
        "char_count":       len(plain_text),
    })


# ── Helpers transcript ────────────────────────────────────────────────────────

def _pick_transcript(tl, preferred):
    for lang in preferred:
        try:
            return tl.find_transcript([lang])
        except Exception:
            continue
    try:
        return next(iter(tl))
    except StopIteration:
        raise RuntimeError("Nu există transcrieri disponibile.")


def _normalize_segments(segments):
    """Normalizează la lista de dict {'start', 'duration', 'text'}."""
    if not segments:
        return []
    result = []
    for s in segments:
        if isinstance(s, dict):
            result.append(s)
        else:
            result.append({
                "start":    getattr(s, "start",    getattr(s, "offset", 0)),
                "duration": getattr(s, "duration", 0),
                "text":     getattr(s, "text",     str(s)),
            })
    return result


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 yt_tools.py <action> <url> [opts_json]")
        sys.exit(1)

    action = sys.argv[1]
    url    = sys.argv[2]
    opts   = json.loads(sys.argv[3]) if len(sys.argv) >= 4 else {}

    if action == "download":
        download_video(url, opts)
    elif action == "transcript":
        get_transcript(url, opts)
    else:
        error(f"Acțiune necunoscută: {action}")
        sys.exit(1)
