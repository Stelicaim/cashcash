/**
 * setup.js
 * Rulează o singură dată: verifică Python și instalează dependențele necesare.
 * Usage: node setup.js
 */

const { execSync, spawnSync } = require("child_process");

const RED    = "\x1b[31m";
const GREEN  = "\x1b[32m";
const YELLOW = "\x1b[33m";
const CYAN   = "\x1b[36m";
const RESET  = "\x1b[0m";

function log(color, icon, msg) {
  console.log(`${color}${icon} ${msg}${RESET}`);
}

function run(cmd, opts = {}) {
  try {
    execSync(cmd, { stdio: "inherit", ...opts });
    return true;
  } catch {
    return false;
  }
}

function runCapture(cmd) {
  try {
    return execSync(cmd, { encoding: "utf8" }).trim();
  } catch {
    return null;
  }
}

console.log(`\n${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}`);
console.log(`${CYAN}  Caption Remover — Setup Check${RESET}`);
console.log(`${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n`);

// ── 1. Node.js ──────────────────────────────────────────────────────────────
const nodeVer = process.version;
log(GREEN, "✔", `Node.js ${nodeVer}`);

// ── 2. Python ───────────────────────────────────────────────────────────────
const pythonCmd =
  runCapture("python3 --version") ? "python3" :
  runCapture("python --version")  ? "python"  : null;

if (!pythonCmd) {
  log(RED, "✘", "Python nu a fost găsit!");
  console.log(`\n  Instalează Python 3.8+ de la: https://www.python.org/downloads/`);
  console.log(`  Pe Windows asigură-te că bifezi "Add Python to PATH" la instalare.\n`);
  process.exit(1);
}

const pyVer = runCapture(`${pythonCmd} --version`);
log(GREEN, "✔", `${pyVer} (cmd: ${pythonCmd})`);

// Salvează comanda Python detectată
const fs = require("fs");
fs.writeFileSync(".python_cmd", pythonCmd);

// ── 3. pip ──────────────────────────────────────────────────────────────────
const pipCmd =
  runCapture(`${pythonCmd} -m pip --version`) ? `${pythonCmd} -m pip` :
  runCapture("pip3 --version") ? "pip3" :
  runCapture("pip --version")  ? "pip"  : null;

if (!pipCmd) {
  log(RED, "✘", "pip nu a fost găsit! Instalează pip manual.");
  process.exit(1);
}
log(GREEN, "✔", `pip disponibil`);

// ── 4. OpenCV ───────────────────────────────────────────────────────────────
const opencvCheck = runCapture(`${pythonCmd} -c "import cv2; print(cv2.__version__)"`);
if (opencvCheck) {
  log(GREEN, "✔", `OpenCV ${opencvCheck} deja instalat`);
} else {
  log(YELLOW, "⟳", "Instalez opencv-python...");
  const ok = run(`${pipCmd} install opencv-python`);
  if (!ok) {
    // Fallback: headless (fără GUI, mai ușor pe servere)
    log(YELLOW, "⟳", "Încerc opencv-python-headless...");
    run(`${pipCmd} install opencv-python-headless`);
  }
  const check2 = runCapture(`${pythonCmd} -c "import cv2; print(cv2.__version__)"`);
  if (check2) {
    log(GREEN, "✔", `OpenCV ${check2} instalat cu succes`);
  } else {
    log(RED, "✘", "OpenCV nu a putut fi instalat. Rulează manual:");
    console.log(`   pip install opencv-python\n`);
    process.exit(1);
  }
}

// ── 5. NumPy ────────────────────────────────────────────────────────────────
const numpyCheck = runCapture(`${pythonCmd} -c "import numpy; print(numpy.__version__)"`);
if (numpyCheck) {
  log(GREEN, "✔", `NumPy ${numpyCheck}`);
} else {
  log(YELLOW, "⟳", "Instalez numpy...");
  run(`${pipCmd} install numpy`);
}

// ── 6. ffmpeg-static ────────────────────────────────────────────────────────
try {
  const ffmpegPath = require("ffmpeg-static");
  if (ffmpegPath && fs.existsSync(ffmpegPath)) {
    log(GREEN, "✔", `ffmpeg-static: ${ffmpegPath}`);
  } else {
    throw new Error();
  }
} catch {
  log(YELLOW, "⟳", "Instalez npm dependencies...");
  run("npm install");
}

// ── 7. Directoare tmp ───────────────────────────────────────────────────────
["tmp/uploads", "tmp/outputs"].forEach((dir) => {
  fs.mkdirSync(dir, { recursive: true });
  log(GREEN, "✔", `Folder creat: ${dir}/`);
});

// ── Done ────────────────────────────────────────────────────────────────────
console.log(`\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}`);
log(GREEN, "✔", "Setup complet! Pornește serverul cu:");
console.log(`\n   ${CYAN}npm start${RESET}\n`);
console.log(`   Deschide: ${CYAN}http://localhost:3000${RESET}`);
console.log(`${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n`);

// ── 8. yt-dlp ────────────────────────────────────────────────────────────────
const ytdlpCheck = runCapture("yt-dlp --version");
if (ytdlpCheck) {
  log(GREEN, "✔", `yt-dlp ${ytdlpCheck}`);
} else {
  log(YELLOW, "⟳", "Instalez yt-dlp...");
  run(`${pipCmd} install yt-dlp`);
  const check = runCapture("yt-dlp --version");
  check
    ? log(GREEN, "✔", `yt-dlp ${check} instalat`)
    : log(RED,   "✘", "yt-dlp nu a putut fi instalat. Rulează: pip install yt-dlp");
}

// ── 9. youtube-transcript-api ─────────────────────────────────────────────────
const ytApiCheck = runCapture(`${pythonCmd} -c "import youtube_transcript_api; print('ok')"`);
if (ytApiCheck) {
  log(GREEN, "✔", "youtube-transcript-api deja instalat");
} else {
  log(YELLOW, "⟳", "Instalez youtube-transcript-api...");
  run(`${pipCmd} install youtube-transcript-api`);
}

// ── 10. deep-translator (opțional, pentru traducere) ──────────────────────────
const deepTrCheck = runCapture(`${pythonCmd} -c "import deep_translator; print('ok')"`);
if (deepTrCheck) {
  log(GREEN, "✔", "deep-translator deja instalat");
} else {
  log(YELLOW, "⟳", "Instalez deep-translator...");
  run(`${pipCmd} install deep-translator`);
}

// ── 11. edge-tts (Text-to-Speech gratuit, voci Microsoft Neural) ──────────────
const edgeTtsCheck = runCapture(`${pythonCmd} -c "import edge_tts; print('ok')"`);
if (edgeTtsCheck) {
  log(GREEN, "✔", "edge-tts deja instalat");
} else {
  log(YELLOW, "⟳", "Instalez edge-tts (voci neurale gratuite)...");
  run(`${pipCmd} install edge-tts`);
  const check = runCapture(`${pythonCmd} -c "import edge_tts; print('ok')"`);
  check
    ? log(GREEN, "✔", "edge-tts instalat cu succes")
    : log(RED,   "✘", "edge-tts nu a putut fi instalat. Rulează: pip install edge-tts");
}
