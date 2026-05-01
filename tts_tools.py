#!/usr/bin/env python3
"""
tts_tools.py — Text-to-Speech gratuit și nelimitat via Microsoft Edge TTS
Folosește edge-tts: voci neurale de înaltă calitate, fără API key, fără limite.

Usage (apelat de server.js):
    python3 tts_tools.py <text> <voice> <output_path> [opts_json]

opts_json:
    {
      "rate":   "+0%",    # viteză: "-20%" mai lent, "+20%" mai rapid
      "pitch":  "+0Hz",   # tonalitate: "-10Hz" mai jos, "+10Hz" mai sus
      "volume": "+0%"     # volum: "-10%" mai silențios
    }
"""

import sys
import os
import json
import asyncio

def log(msg):
    print(json.dumps(msg), flush=True)

def error(msg):
    log({"type": "error", "message": msg})

async def generate(text, voice, output_path, rate, pitch, volume):
    try:
        import edge_tts
    except ImportError:
        error("edge-tts nu este instalat. Rulează: pip install edge-tts")
        sys.exit(1)

    communicate = edge_tts.Communicate(
        text   = text,
        voice  = voice,
        rate   = rate,
        pitch  = pitch,
        volume = volume,
    )
    await communicate.save(output_path)

async def list_voices_async():
    try:
        import edge_tts
        voices = await edge_tts.list_voices()
        return voices
    except Exception:
        return []

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 tts_tools.py <action> [args...]")
        sys.exit(1)

    action = sys.argv[1]

    # ── List voices ───────────────────────────────────────────────────────────
    if action == "list":
        voices = asyncio.run(list_voices_async())
        # Filter: English + Romanian, only Neural
        filtered = [
            {
                "name":   v["ShortName"],
                "locale": v["Locale"],
                "gender": v["Gender"],
                "label":  v.get("FriendlyName", v["ShortName"]),
            }
            for v in voices
            if any(v["Locale"].startswith(l) for l in ["en-US","en-GB","en-AU","ro-RO"])
            and "Neural" in v["ShortName"]
        ]
        print(json.dumps({"type": "voices", "voices": filtered}), flush=True)
        sys.exit(0)

    # ── Generate speech ───────────────────────────────────────────────────────
    if action == "generate":
        if len(sys.argv) < 5:
            error("Usage: tts_tools.py generate <text> <voice> <output_path> [opts]")
            sys.exit(1)

        text        = sys.argv[2]
        voice       = sys.argv[3]
        output_path = sys.argv[4]
        opts        = json.loads(sys.argv[5]) if len(sys.argv) > 5 else {}

        rate   = opts.get("rate",   "+0%")
        pitch  = opts.get("pitch",  "+0Hz")
        volume = opts.get("volume", "+0%")

        if not text.strip():
            error("Textul este gol.")
            sys.exit(1)

        try:
            asyncio.run(generate(text, voice, output_path, rate, pitch, volume))
        except Exception as e:
            error(f"Eroare generare audio: {str(e)}")
            sys.exit(1)

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            error("Fișierul audio nu a fost generat.")
            sys.exit(1)

        size = os.path.getsize(output_path)
        log({"type": "done", "output": output_path, "size": size})
        sys.exit(0)

    error(f"Acțiune necunoscută: {action}")
    sys.exit(1)
