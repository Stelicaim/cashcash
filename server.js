"use strict";

const express     = require("express");
const multer      = require("multer");
const path        = require("path");
const fs          = require("fs");
const { spawn }   = require("child_process");
const { v4: uuid} = require("uuid");
const cors        = require("cors");

const PORT       = process.env.PORT || 3000;
const UPLOAD_DIR = path.join(__dirname, "tmp", "uploads");
const OUTPUT_DIR = path.join(__dirname, "tmp", "outputs");
const CLEANUP_MS = 15 * 60 * 1000;

const PYTHON_CMD = (() => {
  try { return fs.readFileSync(path.join(__dirname, ".python_cmd"), "utf8").trim(); }
  catch { return process.platform === "win32" ? "python" : "python3"; }
})();

const INPAINT_SCRIPT = path.join(__dirname, "inpaint.py");
const YT_SCRIPT      = path.join(__dirname, "yt_tools.py");

[UPLOAD_DIR, OUTPUT_DIR].forEach(d => fs.mkdirSync(d, { recursive: true }));

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, "public")));

const storage = multer.diskStorage({
  destination: UPLOAD_DIR,
  filename:    (_, file, cb) => cb(null, uuid() + path.extname(file.originalname)),
});
const upload = multer({
  storage,
  limits:     { fileSize: 3 * 1024 * 1024 * 1024 },
  fileFilter: (_, file, cb) => {
    const ok = /\.(mp4|mkv|mov|avi|webm|mpeg|mpg|flv|3gp)$/i.test(file.originalname)
            || file.mimetype.startsWith("video/");
    ok ? cb(null, true) : cb(new Error("Tip nesupорtat."));
  },
});

function scheduleDelete(p, ms = CLEANUP_MS) {
  setTimeout(() => fs.unlink(p, () => {}), ms);
}
function sendSSE(res, event, data) {
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

// ── Shared in-memory job store ────────────────────────────────────────────────
const jobs = new Map();

function spawnPythonJob(args, onDone) {
  const jobId = uuid();
  const job   = { status: "running", percent: 0, message: "Se pornește...", outputFile: null, fileName: null, result: null, error: null };
  jobs.set(jobId, job);

  const py = spawn(PYTHON_CMD, args);
  let buf  = "";

  py.stdout.on("data", chunk => {
    buf += chunk.toString();
    const lines = buf.split("\n");
    buf = lines.pop();
    for (const line of lines) {
      const t = line.trim();
      if (!t) continue;
      try {
        const msg = JSON.parse(t);
        switch (msg.type) {
          case "progress": job.percent = msg.percent; job.message = msg.message || ""; break;
          case "info":     console.log(`[${jobId.slice(0,6)}] ${msg.message}`); break;
          case "error":    job.status = "error"; job.error = msg.message; break;
          case "done":     job.status = "done"; job.percent = 100; job.result = msg; job.message = "Gata!"; if(onDone) onDone(job, msg); break;
        }
      } catch { console.log(`[py] ${t}`); }
    }
  });

  py.stderr.on("data", c => { const t = c.toString().trim(); if(t) console.error(`[py err] ${t}`); });
  py.on("close", code => { if(code !== 0 && job.status !== "done") { job.status = "error"; job.error = job.error || `Python exited ${code}`; } });
  py.on("error", err  => { job.status = "error"; job.error = `Nu pot porni Python: ${err.message}`; });

  return jobId;
}

// ── SSE helper (shared) ───────────────────────────────────────────────────────
function makeSSEEndpoint(getPayload) {
  return (req, res) => {
    const job = jobs.get(req.params.jobId);
    if (!job) return res.status(404).end();
    res.setHeader("Content-Type",  "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection",    "keep-alive");
    res.flushHeaders();

    const push = () => {
      const j = jobs.get(req.params.jobId);
      if (j) sendSSE(res, "status", getPayload(j));
    };

    push();
    const iv = setInterval(() => {
      const j = jobs.get(req.params.jobId);
      if (!j) { clearInterval(iv); res.end(); return; }
      push();
      if (j.status === "done" || j.status === "error") { clearInterval(iv); setTimeout(() => res.end(), 200); }
    }, 400);
    req.on("close", () => clearInterval(iv));
  };
}

// ── Generic status poll ───────────────────────────────────────────────────────
app.get("/status/:jobId", (req, res) => {
  const j = jobs.get(req.params.jobId);
  if (!j) return res.status(404).json({ error: "Job negăsit." });
  res.json({ status: j.status, percent: j.percent, message: j.message,
             downloadUrl: j.outputFile ? `/download/${j.outputFile}` : null,
             fileName: j.fileName, result: j.result, error: j.error });
});

// ════════════════════════════════════════════════════
//  CAPTION REMOVER
// ════════════════════════════════════════════════════
app.post("/upload", upload.single("video"), (req, res) => {
  if (!req.file) return res.status(400).json({ error: "Niciun fișier primit." });

  let cfg = {};
  if (req.body.config) { try { cfg = JSON.parse(req.body.config); } catch {} }

  const inputPath  = req.file.path;
  const ext        = path.extname(req.file.originalname) || ".mp4";
  const outputName = `clean_${uuid()}${ext}`;
  const outputPath = path.join(OUTPUT_DIR, outputName);

  const args  = [INPAINT_SCRIPT, inputPath, outputPath];
  if (Object.keys(cfg).length > 0) args.push(JSON.stringify(cfg));

  const jobId = spawnPythonJob(args, (job, msg) => {
    job.outputFile = outputName;
    job.fileName   = "clean_" + req.file.originalname;
    scheduleDelete(inputPath);
    scheduleDelete(outputPath);
  });

  const j = jobs.get(jobId);
  j.fileName = "clean_" + req.file.originalname;

  res.json({ jobId });
});

app.get("/caption/events/:jobId", makeSSEEndpoint(j => ({
  percent: j.percent, message: j.message, status: j.status,
  downloadUrl: j.outputFile ? `/download/${j.outputFile}` : null,
  fileName: j.fileName, error: j.error,
})));

app.get("/download/:filename", (req, res) => {
  const safe = path.basename(req.params.filename);
  const file = path.join(OUTPUT_DIR, safe);
  if (!fs.existsSync(file)) return res.status(404).send("Fișierul a expirat.");
  const ext   = path.extname(safe).toLowerCase();
  const mimes = { ".mp4":"video/mp4",".mkv":"video/x-matroska",".mov":"video/quicktime",
                  ".avi":"video/x-msvideo",".webm":"video/webm",".mp3":"audio/mpeg",".m4a":"audio/mp4" };
  res.setHeader("Content-Type",        mimes[ext] || "application/octet-stream");
  res.setHeader("Content-Disposition", `attachment; filename="${safe}"`);
  res.setHeader("Content-Length",      fs.statSync(file).size);
  fs.createReadStream(file).pipe(res);
});

// ════════════════════════════════════════════════════
//  YOUTUBE DOWNLOADER
// ════════════════════════════════════════════════════
app.post("/yt/download", (req, res) => {
  const { url, quality = "1080" } = req.body;
  if (!url) return res.status(400).json({ error: "URL lipsă." });

  const ext        = quality === "audio" ? ".mp3" : ".mp4";
  const outputName = `yt_${uuid()}${ext}`;
  const outputPath = path.join(OUTPUT_DIR, outputName);
  const opts       = JSON.stringify({ output_path: outputPath, quality });

  const jobId = spawnPythonJob([YT_SCRIPT, "download", url, opts], (job, msg) => {
    const actualPath = msg.output || outputPath;
    const actualName = path.basename(actualPath);
    job.outputFile = actualName;
    job.fileName   = quality === "audio" ? `audio.mp3` : `video_${quality}p.mp4`;
    scheduleDelete(actualPath);
  });

  res.json({ jobId });
});

app.get("/yt/events/:jobId", makeSSEEndpoint(j => ({
  percent: j.percent, message: j.message, status: j.status,
  downloadUrl: j.outputFile ? `/download/${j.outputFile}` : null,
  fileName: j.fileName, result: j.result, error: j.error,
})));

// ════════════════════════════════════════════════════
//  YOUTUBE TRANSCRIPT
// ════════════════════════════════════════════════════
app.post("/yt/transcript", (req, res) => {
  const { url, targetLang = "none" } = req.body;
  if (!url) return res.status(400).json({ error: "URL lipsă." });

  const opts  = JSON.stringify({ target_lang: targetLang });
  const jobId = spawnPythonJob([YT_SCRIPT, "transcript", url, opts], (job, msg) => {
    console.log(`[transcript done] chars=${msg.char_count}`);
  });

  res.json({ jobId });
});

app.get("/yt/transcript/events/:jobId", makeSSEEndpoint(j => ({
  percent: j.percent, message: j.message, status: j.status,
  result: j.result, error: j.error,
})));

// ════════════════════════════════════════════════════
//  TEXT TO SPEECH — Microsoft Edge TTS (gratuit, nelimitat)
//  Voci neurale de înaltă calitate, fără API key
// ════════════════════════════════════════════════════
const TTS_SCRIPT = path.join(__dirname, "tts_tools.py");

// POST /tts/generate
app.post("/tts/generate", (req, res) => {
  const { text, voice = "en-US-GuyNeural", rate = "+0%", pitch = "+0Hz", volume = "+0%" } = req.body;
  if (!text?.trim()) return res.status(400).json({ error: "Textul este gol." });

  const outputName = `tts_${uuid()}.mp3`;
  const outputPath = path.join(OUTPUT_DIR, outputName);
  const opts       = JSON.stringify({ rate, pitch, volume });

  const jobId = spawnPythonJob(
    [TTS_SCRIPT, "generate", text, voice, outputPath, opts],
    (job, msg) => {
      job.outputFile = outputName;
      job.fileName   = `speech_${voice.split("-")[2] || "voice"}.mp3`;
      scheduleDelete(outputPath, 30 * 60 * 1000);
      console.log(`[tts] ${voice} → ${outputName} (${(msg.size/1024).toFixed(0)}KB)`);
    }
  );

  res.json({ jobId });
});

// SSE for TTS
app.get("/tts/events/:jobId", makeSSEEndpoint(j => ({
  percent:     j.percent,
  message:     j.message,
  status:      j.status,
  downloadUrl: j.outputFile ? `/download/${j.outputFile}` : null,
  fileName:    j.fileName,
  error:       j.error,
})));

// GET /tts/voices — returnează lista de voci disponibile
app.get("/tts/voices", (req, res) => {
  // Voci preset (fără a lansa Python la fiecare request)
  // Selectate manual: cele mai bune voci male EN + RO similare cu Dan
  res.json({
    voices: [
      // ── English Male — similare cu Dan (Upbeat, Dynamic, Friendly) ──
      { name: "en-US-GuyNeural",          label: "Guy",          lang: "EN",  gender: "M", desc: "Natural · Friendly · Versatil" },
      { name: "en-US-ChristopherNeural",  label: "Christopher",  lang: "EN",  gender: "M", desc: "Confident · Energetic · Clar" },
      { name: "en-US-EricNeural",         label: "Eric",         lang: "EN",  gender: "M", desc: "Warm · Dynamic · Natural" },
      { name: "en-US-RogerNeural",        label: "Roger",        lang: "EN",  gender: "M", desc: "Upbeat · Prietenos · Rapid" },
      { name: "en-US-SteffanNeural",      label: "Steffan",      lang: "EN",  gender: "M", desc: "Profesional · Narativ" },
      // ── English Female ──
      { name: "en-US-JennyNeural",        label: "Jenny",        lang: "EN",  gender: "F", desc: "Natural · Conversațional" },
      { name: "en-US-AriaNeural",         label: "Aria",         lang: "EN",  gender: "F", desc: "Vibrant · Expresivă" },
      // ── English UK ──
      { name: "en-GB-RyanNeural",         label: "Ryan (UK)",    lang: "EN",  gender: "M", desc: "British · Natural · Dynamic" },
      { name: "en-GB-SoniaNeural",        label: "Sonia (UK)",   lang: "EN",  gender: "F", desc: "British · Profesional" },
      // ── Romanian ──
      { name: "ro-RO-EmilNeural",         label: "Emil",         lang: "RO",  gender: "M", desc: "Română · Natural · Masculin" },
      { name: "ro-RO-AlinaNeural",        label: "Alina",        lang: "RO",  gender: "F", desc: "Română · Natural · Feminin" },
    ]
  });
});

// ── Start ─────────────────────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`\n🎬 Caption Remover v2 — http://localhost:${PORT}`);
  console.log(`   Python: ${PYTHON_CMD} | inpaint.py + yt_tools.py + tts_tools.py`);
  console.log(`   TTS: Microsoft Edge TTS (gratuit, nelimitat)\n`);
});
