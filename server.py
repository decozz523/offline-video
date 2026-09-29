"""
Sea Louder — локальный сервер.

Зачем он нужен: браузер не может сам скачать видео с YouTube / TikTok
(они не отдают mp4-файл, только потоковый плеер), и любой сторонний API в браузере
блокируется CORS. Поэтому скачиванием занимается этот маленький сервер на твоём
компьютере: он вызывает yt-dlp, кладёт файл в папку загрузок и страница просто
показывает, что скачалось.

Запуск:  python server.py     (или двойной клик по start.bat)
Страница: http://127.0.0.1:8756/

Без внешних зависимостей — только стандартная библиотека Python + установленный yt-dlp.
"""

import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

# ---------------------------------------------------------------- настройки
HOST = "127.0.0.1"
PORT = 8756
ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / "downloads"          # сюда падают файлы (можно поменять на любой путь)
YTDLP = [sys.executable, "-m", "yt_dlp"]
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".ts"}
AUDIO_EXT = {".mp3", ".m4a", ".opus", ".ogg", ".wav", ".aac"}

DOWNLOADS.mkdir(exist_ok=True)

# ------------------------------------------------------------------- задачи
# id -> {"status": "working|done|error", "percent": 0..100, "file": "имя.mp4",
#        "title": "...", "log": "последняя строка вывода yt-dlp", "ts": time.time()}
JOBS = {}
JOBS_LOCK = threading.Lock()

PCT_RE = re.compile(r"\[download\]\s+([\d.]+)%")
DEST_RE = re.compile(r"\[download\] Destination: (.+)")


def safe_name(name: str, limit: int = 110) -> str:
    """Чистим имя файла от символов, недопустимых в Windows."""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:limit].strip() or f"video_{int(time.time())}")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, i = path.stem, path.suffix, 2
    while True:
        cand = path.with_name(f"{stem} ({i}){suffix}")
        if not cand.exists():
            return cand
        i += 1


def _find_ffmpeg():
    """ffmpeg нужен для склейки дорожек в один mp4.
    Ищем его сами: после установки через winget PATH обновляется только
    в новых процессах, а сервер мог стартовать раньше."""
    cands = [
        shutil.which("ffmpeg"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages",
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path(os.environ.get("ProgramFiles", "")) / "ffmpeg/bin/ffmpeg.exe",
    ]
    if cands[0]:
        return cands[0]
    for base in cands[1:2]:
        base = Path(base)
        if base.is_dir():
            for exe in base.rglob("ffmpeg.exe"):
                return str(exe)
    for base in cands[2:]:
        if Path(base).is_file():
            return str(base)
    return None


FFMPEG = _find_ffmpeg()
if FFMPEG:
    os.environ["PATH"] = str(Path(FFMPEG).parent) + os.pathsep + os.environ.get("PATH", "")


def ffmpeg_available() -> bool:
    return bool(FFMPEG) and Path(FFMPEG).is_file()


def ytdlp_works() -> bool:
    try:
        out = subprocess.run(YTDLP + ["--version"], capture_output=True, text=True, timeout=60)
        return out.returncode == 0
    except Exception:
        return False


def worker(job_id: str, url: str, mode: str, quality: str):
    """Скачивание в отдельном потоке, чтобы сервер не блокировался."""
    def note(**kw):
        with JOBS_LOCK:
            JOBS[job_id].update(kw)

    outtmpl = str(DOWNLOADS / "%(title).100B [%(id)s].%(ext)s")
    cmd = YTDLP + [
        "--no-playlist",
        "--newline",
        "--no-mtime",
        "--retries", "5",
        "--fragment-retries", "5",
        "-o", outtmpl,
    ]

    if mode == "audio":
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    else:
        # Берём mp4 с уже встроенным звуком — тогда склейка (и ffmpeg) не нужны вообще.
        # Формат 18 = 360p mp4, 22 = 720p mp4; если их нет — падаем в обычный выбор.
        if quality and quality != "best":
            cmd += ["-f",
                    f"best[ext=mp4][height<={quality}]/bestvideo[ext=mp4][height<={quality}]+bestaudio[ext=m4a]/"
                    f"best[height<={quality}]/best"]
        else:
            cmd += ["-f", "best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best"]
        cmd += ["--merge-output-format", "mp4"]

    # явный путь к ffmpeg — надёжнее, чем надеяться на PATH
    if FFMPEG:
        cmd += ["--ffmpeg-location", str(Path(FFMPEG).parent)]

    cmd.append(url)

    before = {p.name for p in DOWNLOADS.iterdir() if p.is_file()}
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        note(status="error", error="yt-dlp не найден. Установи: python -m pip install -U yt-dlp")
        return

    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            tail.append(line)
            tail = tail[-15:]
        if line.startswith("ERROR:"):
            tail = tail[-6:]
        m = PCT_RE.search(line)
        if m:
            note(percent=round(float(m.group(1)), 1), log=line[:200])
        elif "[ExtractAudio]" in line or "[Merger]" in line:
            note(log="обрабатываем файл…", percent=99.0)
    proc.wait()

    # из вывода оставляем только то, что объясняет ошибку, — без шума и путей
    def clean(lines):
        out = [re.sub(r"\s*/?[A-Za-z]:\\[^\s|]+", "", l).strip() for l in lines]
        out = [l for l in out if l and not l.startswith("WARNING:")]
        return " | ".join(out)[-500:]

    if proc.returncode != 0:
        err = ""
        for l in reversed(tail):
            if l.startswith("ERROR:"):
                err = clean([l] + tail[-2:])
                break
        note(status="error", error=err or clean(tail[-3:]) or f"yt-dlp вышел с кодом {proc.returncode}")
        return

    after = [p for p in DOWNLOADS.iterdir() if p.is_file()]
    # отдельные дорожки (video.fNNN + audio.fNNN) — это результат, который надо ещё
    # склеить ffmpeg'ом; такой файл нам не подходит
    partial = re.compile(r"\.f\d+\.[a-z0-9]+$", re.I)
    new_files = [p for p in after if p.name not in before and not partial.search(p.name)]

    # файл мог перезаписаться (тот же id) — тогда просто берём самый свежий готовый
    if not new_files:
        ready = [p for p in after if not partial.search(p.name)]
        if ready:
            newest = max(ready, key=lambda p: p.stat().st_mtime)
            if newest.stat().st_mtime > time.time() - 300:
                new_files = [newest]

    if not new_files:
        note(status="error", error="Видео и звук остались отдельными файлами — нужен ffmpeg для склейки. "
                                  "Установи его: winget install Gyan.FFmpeg (или возьми сборку с "
                                  "https://www.gyan.dev/ffmpeg/builds/ и добавь bin в PATH), потом повтори.")
        return

    f = max(new_files, key=lambda p: p.stat().st_mtime)
    note(status="done", percent=100.0, file=f.name, size=f.stat().st_size, log="готово")


def human_size(n: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"


def list_downloads():
    items = []
    for p in DOWNLOADS.iterdir():
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in VIDEO_EXT:
            kind = "video"
        elif ext in AUDIO_EXT:
            kind = "audio"
        else:
            continue
        items.append({
            "name": p.name,
            "size": p.stat().st_size,
            "sizeText": human_size(p.stat().st_size),
            "mtime": p.stat().st_mtime,
            "kind": kind,
            "url": "/media/" + p.name.replace("%", "%25").replace("#", "%23").replace("?", "%3F"),
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


class Handler(BaseHTTPRequestHandler):
    server_version = "SeaLouder/1.0"

    # ---- утилиты
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _file(self, path: Path, download=False):
        if not path.is_file():
            self.send_error(404, "Not found")
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size = path.stat().st_size
        start, end = 0, size - 1
        code = 200

        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                s, _, e = rng[6:].partition("-")
                if s:
                    start = int(s)
                    end = int(e) if e else size - 1
                else:                      # bytes=-500 — последние 500 байт
                    start = max(0, size - int(e))
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                code = 206
            except ValueError:
                start, end = 0, size - 1

        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_header("Content-Range", f"bytes */{size}")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename*=UTF-8\'\'{path.name}')
        self._cors()
        self.end_headers()

        if self.command == "HEAD":
            return

        with open(path, "rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(256 * 1024, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (ConnectionAbortedError, BrokenPipeError):
                    return
                left -= len(chunk)

    # ---- маршруты
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._route()

    def do_HEAD(self):
        self._route()

    def _route(self):
        u = urlparse(self.path)
        path, query = unquote(u.path), parse_qs(u.query)

        if path == "/api/health":
            return self._json({
                "ok": True,
                "ytdlp": ytdlp_works(),
                "ffmpeg": ffmpeg_available(),
                "downloads": str(DOWNLOADS),
                "count": len(list_downloads()),
            })

        if path == "/api/downloads":
            return self._json({"dir": str(DOWNLOADS), "items": list_downloads()})

        if path == "/api/status":
            jid = (query.get("id") or [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(jid)
            if not job:
                return self._json({"status": "error", "error": "Задача не найдена"}, 404)
            return self._json(job)

        if path.startswith("/media/"):
            name = os.path.basename(path[len("/media/"):])
            return self._file(DOWNLOADS / name, download=("dl" in query))

        if path.startswith("/api/file/"):
            name = os.path.basename(path[len("/api/file/"):])
            return self._file(DOWNLOADS / name, download=True)

        # ---- статика
        if path in ("/", "/index.html"):
            return self._file(ROOT / "index.html")
        if path == "/app.js":
            return self._file(ROOT / "app.js")
        if path == "/style.css":
            return self._file(ROOT / "style.css")
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        target = (ROOT / path.lstrip("/")).resolve()
        if str(target).startswith(str(ROOT)) and target.is_file():
            return self._file(target)
        self.send_error(404, "Not found")

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/download":
            return self._json({"status": "error", "error": "Неизвестный маршрут"}, 404)

        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json({"status": "error", "error": "Плохой JSON в запросе"}, 400)

        url = (payload.get("url") or "").strip()
        mode = "audio" if payload.get("mode") == "audio" else "video"
        quality = str(payload.get("quality") or "best")

        if not re.match(r"^https?://", url, re.I):
            return self._json({"status": "error", "error": "Это не похоже на ссылку http(s)."}, 400)

        if not ytdlp_works():
            return self._json({
                "status": "error",
                "error": "yt-dlp не установлен. Выполни: python -m pip install -U yt-dlp",
            }, 503)

        jid = uuid.uuid4().hex[:12]
        with JOBS_LOCK:
            JOBS[jid] = {"status": "working", "percent": 0.0, "mode": mode,
                         "url": url, "log": "получаем данные о видео…", "ts": time.time()}
        threading.Thread(target=worker, args=(jid, url, mode, quality), daemon=True).start()
        return self._json({"status": "working", "id": jid, "dir": str(DOWNLOADS)})

    def log_message(self, fmt, *args):        # не засоряем консоль
        if "/api/status" in (args[0] if args else ""):
            return
        sys.stderr.write("  %s\n" % (fmt % args))


def main():
    port = PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass

    print("=" * 58)
    print("  Sea Louder — локальный сервер скачивания")
    print("=" * 58)
    print(f"  Папка загрузок : {DOWNLOADS}")
    print(f"  Страница       : http://{HOST}:{port}/")
    if not ytdlp_works():
        print("\n  !! yt-dlp не найден. Установи командой:")
        print("     python -m pip install -U yt-dlp\n")
    print("\n  Остановить сервер: Ctrl+C\n")

    try:
        httpd = ThreadingHTTPServer((HOST, port), Handler)
    except OSError as e:
        print(f"  Порт {port} занят ({e}). Попробуй: python server.py {port + 1}")
        return

    threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{port}/")).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Остановлено. Файлы лежат в:", DOWNLOADS)


if __name__ == "__main__":
    main()