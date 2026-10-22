#!/usr/bin/env python3
"""Demo server for the FFmpeg checkout.

The preview needs an HTTP entry point; this serves a small page on port 3000
whose buttons run the `ffmpeg` / `ffprobe` binaries built from this repository
(see ../docker-compose.base44.yml). Standard library only, no dependencies.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = int(os.environ.get("PORT", "3000"))
MEDIA_DIR = os.environ.get("MEDIA_DIR", "/tmp/media")
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")

MEDIA_FILES = {
    "mp4": "demo.mp4",
    "gif": "demo.gif",
    "png": "demo.png",
}

_info_lock = threading.Lock()
_info_cache = {"at": 0.0, "data": None}


def run(cmd, timeout=180, full=False):
    """Run one command and capture a compact record of the result.

    Output is trimmed for display unless `full` is set (callers that need to
    parse it, e.g. ffprobe JSON or the codec listings).
    """
    started = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        code, err, out = proc.returncode, proc.stderr, proc.stdout
    except subprocess.TimeoutExpired:
        code, err, out = 124, "timed out after %ss" % timeout, ""
    out = out.strip()
    return {
        "command": " ".join(cmd),
        "returncode": code,
        "ms": int((time.time() - started) * 1000),
        "stderr": err.strip()[-1600:],
        "stdout": out if full else out[-1600:],
    }


def media_path(kind):
    return os.path.join(MEDIA_DIR, MEDIA_FILES[kind])


def build_info():
    """Version, build configuration and capability counts (cached briefly)."""
    with _info_lock:
        if _info_cache["data"] and time.time() - _info_cache["at"] < 60:
            return _info_cache["data"]

        version = run([FFMPEG, "-hide_banner", "-version"])
        lines = version["stdout"].splitlines()
        configuration = ""
        for line in lines:
            if line.startswith("configuration:"):
                configuration = line.split(":", 1)[1].strip()

        # Encoder/decoder lines start with a 6-char flag column, filter lines
        # with a 2-3 char one ("V....D aac", " TS aap", ".. abench").
        flag_column = {"encoders": 6, "decoders": 6, "filters": 3}

        def count(kind):
            res = run([FFMPEG, "-hide_banner", "-" + kind], full=True)
            if res["returncode"] != 0:
                return None
            width = flag_column[kind]
            pattern = r"^ [A-Z.]{2,%d} +\S" % width
            return len(re.findall(pattern, res["stdout"], re.MULTILINE))

        data = {
            "version": lines[0] if lines else "ffmpeg not found",
            "libraries": [l for l in lines[1:] if l.startswith("lib")],
            "configuration": configuration,
            "encoders": count("encoders"),
            "decoders": count("decoders"),
            "filters": count("filters"),
        }
        _info_cache.update({"at": time.time(), "data": data})
        return data


def make_mp4():
    return [run([
        FFMPEG, "-hide_banner", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=480x270:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", "4",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        media_path("mp4"),
    ])]


def make_gif():
    # palettegen buffers until EOF, so the clip is first written to a finite
    # file — feeding the split/palette graph straight from lavfi never ends.
    source = os.path.join(MEDIA_DIR, "gif-source.mp4")
    prep = run([
        FFMPEG, "-hide_banner", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
        "-t", "3",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        source,
    ])
    if prep["returncode"] != 0:
        return [prep]
    encode = run([
        FFMPEG, "-hide_banner", "-y",
        "-i", source,
        "-vf", "fps=12,scale=320:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
        "-loop", "0",
        media_path("gif"),
    ])
    return [prep, encode]


def make_png():
    return [run([
        FFMPEG, "-hide_banner", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=480x270:rate=25",
        "-ss", "1", "-frames:v", "1",
        media_path("png"),
    ])]


def probe_mp4():
    return run([
        FFPROBE, "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        media_path("mp4"),
    ], full=True)


ACTIONS = {"mp4": make_mp4, "gif": make_gif, "png": make_png}


def files_payload():
    out = []
    for kind, name in MEDIA_FILES.items():
        path = media_path(kind)
        if os.path.exists(path):
            out.append({"name": name, "url": "/files/" + name, "bytes": os.path.getsize(path)})
    return out


def describe(kind):
    """Run one encode action and collect everything the page shows."""
    steps = ACTIONS[kind]()
    result = {
        "ok": all(step["returncode"] == 0 for step in steps),
        "steps": steps,
        "files": files_payload(),
    }
    if kind == "mp4" and result["ok"]:
        probe = probe_mp4()
        try:
            result["probe"] = json.loads(probe["stdout"])
        except ValueError:
            result["probe"] = None
        probe["stdout"] = probe["stdout"][-1600:]  # display copy only
        result["steps"].append(probe)
    return result


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FFmpeg — build preview</title>
<style>
  :root {
    --bg: #10151c; --panel: #17202a; --line: #27333f;
    --ink: #e8eef4; --muted: #93a4b4; --accent: #4ec3a0;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  main { max-width: 1040px; margin: 0 auto; padding: 40px 20px 64px; }
  h1 { font-size: 30px; margin: 0 0 6px; letter-spacing: -0.02em; }
  .sub { color: var(--muted); margin: 0 0 28px; font-size: 15px; }
  .sub code, code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.92em; }
  .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 18px; }
  .card h2 { font-size: 15px; margin: 0 0 4px; letter-spacing: 0.01em; }
  .card p { color: var(--muted); font-size: 13px; margin: 0 0 14px; }
  button {
    background: var(--accent); color: #06231b; border: 0; border-radius: 8px;
    padding: 9px 14px; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer;
  }
  button:disabled { opacity: 0.55; cursor: progress; }
  .facts { display: flex; flex-wrap: wrap; gap: 8px 22px; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--line); }
  .fact { font-size: 13px; color: var(--muted); }
  .fact b { display: block; color: var(--ink); font-size: 20px; font-weight: 600; letter-spacing: -0.01em; }
  footer { color: var(--muted); font-size: 13px; margin-top: 28px; }
  .out { margin-top: 26px; }
  .out h2 { font-size: 15px; margin: 0 0 10px; }
  .media { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 14px; }
  .media figure { margin: 0; }
  .media img, .media video { max-width: min(100%, 480px); border-radius: 10px; border: 1px solid var(--line); display: block; }
  pre {
    background: #0c1116; border: 1px solid var(--line); border-radius: 10px;
    padding: 14px; overflow: auto; max-height: 340px; margin: 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px;
    color: #cfe0ee; white-space: pre-wrap; word-break: break-word;
  }
  pre .err { color: #ff9b8a; }
  .badge { font-size: 12px; color: var(--muted); margin-left: 8px; }
</style>
</head>
<body>
<main>
  <h1>FFmpeg, built from this checkout</h1>
  <p class="sub">Every button below runs the real <code>ffmpeg</code> / <code>ffprobe</code> binaries compiled from this repository.</p>

  <div class="card">
    <h2 id="version">Loading build information…</h2>
    <p id="config">Reading the binary.</p>
    <div class="facts" id="facts"></div>
  </div>

  <div class="grid" style="margin-top:16px">
    <div class="card">
      <h2>Encode H.264 MP4</h2>
      <p>lavfi <code>testsrc2</code> + <code>sine</code> audio, 4s, libx264 + AAC.</p>
      <button data-action="mp4">Run encode</button>
    </div>
    <div class="card">
      <h2>Make an animated GIF</h2>
      <p>3s clip with the <code>palettegen</code>/<code>paletteuse</code> filter graph.</p>
      <button data-action="gif">Run encode</button>
    </div>
    <div class="card">
      <h2>Extract a still frame</h2>
      <p>Seek to 1s and pull one PNG frame from a lavfi source.</p>
      <button data-action="png">Run encode</button>
    </div>
  </div>

  <section class="out" id="out" hidden>
    <h2>Output <span class="badge" id="out-status"></span></h2>
    <div class="media" id="out-media"></div>
    <pre id="out-log"></pre>
  </section>

  <footer>Served from <code>base44-demo/server.py</code> · artifacts live in the container's media volume.</footer>
</main>
<script>
const facts = document.getElementById('facts');
const log = document.getElementById('out-log');
const media = document.getElementById('out-media');
const out = document.getElementById('out');
const status = document.getElementById('out-status');

function fact(label, value) {
  const el = document.createElement('div');
  el.className = 'fact';
  el.innerHTML = '<b></b>';
  el.querySelector('b').textContent = value == null ? '—' : value;
  el.append(label);
  return el;
}

async function loadInfo() {
  try {
    const info = await (await fetch('/api/info')).json();
    document.getElementById('version').textContent = info.version || 'ffmpeg';
    document.getElementById('config').textContent =
      info.configuration ? 'Configured with: ' + info.configuration : 'Built from source.';
    facts.replaceChildren(
      fact('encoders', info.encoders),
      fact('decoders', info.decoders),
      fact('filters', info.filters)
    );
  } catch (e) {
    document.getElementById('version').textContent = 'Could not read the ffmpeg binary';
    document.getElementById('config').textContent = String(e);
  }
}

function showMedia(file) {
  if (file.name.endsWith('.mp4')) {
    const v = document.createElement('video');
    v.src = file.url; v.controls = true; v.autoplay = true; v.muted = true; v.loop = true;
    media.append(v);
  } else if (file.name.endsWith('.gif') || file.name.endsWith('.png')) {
    const img = document.createElement('img');
    img.src = file.url; img.alt = file.name;
    media.append(img);
  }
}

document.querySelectorAll('button[data-action]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const action = btn.dataset.action;
    btn.disabled = true;
    out.hidden = false;
    media.replaceChildren();
    status.textContent = 'running…';
    log.textContent = '';
    try {
      const res = await fetch('/api/run/' + action, { method: 'POST' });
      const data = await res.json();
      status.textContent = data.ok ? 'done' : 'failed';
      log.textContent = data.steps
        .map((s) => '$ ' + s.command + '\\n[' + s.returncode + ' · ' + s.ms + ' ms]\\n' + (s.stderr || s.stdout))
        .join('\\n\\n');
      const wanted = { mp4: '.mp4', gif: '.gif', png: '.png' }[action];
      data.files.filter((f) => f.name.endsWith(wanted)).forEach(showMedia);
      if (data.probe) {
        const f = data.probe.format || {};
        const streams = data.probe.streams || [];
        status.textContent += ' · ' + (Number(f.duration || 0)).toFixed(2) + 's · ' +
          (Number(f.size || 0) / 1024).toFixed(0) + ' KB · ' +
          streams.map((s) => s.codec_name).join(' + ');
      }
    } catch (e) {
      status.textContent = 'failed';
      log.textContent = String(e);
    } finally {
      btn.disabled = false;
    }
  });
});

loadInfo();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type):
        if not os.path.isfile(path):
            self.send_json({"error": "not found"}, 404)
            return
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/healthz":
            self.send_json({"ok": True})
        elif path == "/api/info":
            self.send_json(build_info())
        elif path.startswith("/files/"):
            name = os.path.basename(path[len("/files/"):])
            types = {
                ".mp4": "video/mp4",
                ".gif": "image/gif",
                ".png": "image/png",
            }
            ext = os.path.splitext(name)[1]
            self.send_file(os.path.join(MEDIA_DIR, name), types.get(ext, "application/octet-stream"))
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/run/"):
            self.send_json({"error": "not found"}, 404)
            return
        kind = path[len("/api/run/"):]
        if kind not in ACTIONS:
            self.send_json({"error": "unknown action"}, 400)
            return
        try:
            self.send_json(describe(kind))
        except Exception as exc:  # keep the demo page usable on any failure
            self.send_json({"ok": False, "steps": [], "files": [], "error": str(exc)}, 500)


def watch_source():
    """Restart on edit so the compose service hot-reloads like a dev server."""
    path = os.path.abspath(__file__)
    mtime = os.path.getmtime(path)

    def loop():
        nonlocal mtime
        while True:
            time.sleep(1)
            try:
                current = os.path.getmtime(path)
            except OSError:
                continue
            if current != mtime:
                print("source changed, restarting", flush=True)
                os.execv(sys.executable, [sys.executable, path] + sys.argv[1:])

    threading.Thread(target=loop, daemon=True).start()


if __name__ == "__main__":
    os.makedirs(MEDIA_DIR, exist_ok=True)
    if "--reload" in sys.argv:
        watch_source()
    print("demo server on 0.0.0.0:%d (media: %s)" % (PORT, MEDIA_DIR), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
