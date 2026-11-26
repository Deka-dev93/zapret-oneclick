"""Движок: папка zapret (Flowseal), разбор стратегий из .bat, запуск winws, автоподбор."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, List, Optional

REPO = "Flowseal/zapret-discord-youtube"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ZapretOneClick"
DOWNLOAD_DIR = DATA_DIR / "zapret"
CONFIG_PATH = DATA_DIR / "config.json"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
# Необязательная подсказка: путь к готовой папке zapret через переменную окружения
KNOWN_LOCAL = Path(os.environ["ZOC_ZAPRET_DIR"]) if os.environ.get("ZOC_ZAPRET_DIR") else None

DEFAULT_TARGETS = [
    "https://discord.com", "https://gateway.discord.gg", "https://cdn.discordapp.com",
    "https://updates.discord.com", "https://www.youtube.com", "https://youtu.be",
    "https://i.ytimg.com", "https://redirector.googlevideo.com",
]

Log = Callable[[str], None]
_zdir: Path = DOWNLOAD_DIR


# ------------------------------------------------------------------ конфиг
def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))   # sig — терпим BOM
    except Exception:
        return {}


def save_config(cfg: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------ папка zapret
def is_zapret_dir(p: Path) -> bool:
    return (p / "bin" / "winws.exe").exists() and any(p.glob("general*.bat"))


def zapret_dir() -> Path:
    return _zdir


def set_zapret_dir(p: Path):
    global _zdir
    _zdir = Path(p)


def pick_initial_dir(cfg: dict) -> Path:
    """Сохранённая папка → известная локальная → папка загрузки."""
    for cand in (cfg.get("zapret_dir"), KNOWN_LOCAL, DOWNLOAD_DIR):
        if cand and is_zapret_dir(Path(cand)):  # None пропускается
            return Path(cand)
    return DOWNLOAD_DIR


def bundle_ready() -> bool:
    return is_zapret_dir(_zdir)


def _http_get(url: str, timeout=30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ZapretOneClick"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _http_download(url: str, progress: Optional[Callable[[int], None]] = None, timeout=60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ZapretOneClick"})
    data = bytearray()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or 0)
        last = -1
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            data += chunk
            if progress and total:
                pct = len(data) * 100 // total
                if pct != last:
                    last = pct
                    progress(pct)
    return bytes(data)


REQUIRED_FILES = ["bin/winws.exe", "bin/WinDivert.dll", "bin/WinDivert64.sys", "bin/cygwin1.dll"]


def missing_files(root: Optional[Path] = None) -> List[str]:
    """Обязательные файлы zapret, которых нет (или они пустые) — часто их удаляет антивирус."""
    root = root or _zdir
    return [f for f in REQUIRED_FILES if not (root / f).exists() or (root / f).stat().st_size == 0]


def download_bundle(log: Log, progress: Optional[Callable[[int], None]] = None) -> Path:
    """Скачивает последний релиз Flowseal/zapret-discord-youtube в DOWNLOAD_DIR."""
    log("Ищу последний релиз zapret-discord-youtube…")
    rel = json.loads(_http_get(f"https://api.github.com/repos/{REPO}/releases/latest"))
    asset = next((a for a in rel.get("assets", []) if a["name"].lower().endswith(".zip")), None)
    if not asset:
        raise RuntimeError("В релизе нет zip-архива")
    log(f"Скачиваю {asset['name']}…")
    tmp = Path(tempfile.mkdtemp(prefix="zoc_"))
    try:
        zpath = tmp / "bundle.zip"
        zpath.write_bytes(_http_download(asset["browser_download_url"], progress, timeout=120))
        ex = tmp / "x"
        with zipfile.ZipFile(zpath) as z:
            for m in z.namelist():
                if not (ex / m).resolve().is_relative_to(ex.resolve()):
                    raise RuntimeError("Небезопасный путь в архиве")
            z.extractall(ex)
        root = next((p.parent.parent for p in ex.rglob("winws.exe")), None)
        if not root:
            raise RuntimeError("winws.exe не найден в архиве")
        keep = {f.name: f.read_bytes() for f in DOWNLOAD_DIR.glob("lists/*-user.txt")}
        if DOWNLOAD_DIR.exists():
            kill_winws()
            shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)
        shutil.copytree(root, DOWNLOAD_DIR)
        (DOWNLOAD_DIR / "lists").mkdir(exist_ok=True)
        for n, b in keep.items():
            (DOWNLOAD_DIR / "lists" / n).write_bytes(b)
        log(f"Установлено: zapret {rel.get('tag_name', '')}")
        return DOWNLOAD_DIR
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure_user_lists():
    """Создаёт *-user.txt как это делает service.bat (load_user_lists)."""
    lists = _zdir / "lists"
    lists.mkdir(exist_ok=True)
    defaults = {
        "ipset-exclude-user.txt": "203.0.113.113/32\n",
        "list-general-user.txt": "domain.example.abc\n",
        "list-exclude-user.txt": "domain.example.abc\n",
    }
    for n, content in defaults.items():
        if not (lists / n).exists():
            (lists / n).write_text(content, encoding="utf-8")


# ---------------------------------------------------- игровой фильтр / цели
def game_filter() -> tuple[str, str, str]:
    """(режим, GameFilterTCP, GameFilterUDP) — как service.bat load_game_filter."""
    mode, tcp, udp = "disabled", "1024-65535", "1024-65535"
    try:
        for line in (_zdir / "utils" / "game_filter.enabled").read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            k, v = k.strip().lower(), v.strip()
            if k == "mode":
                mode = v.lower()
            elif k == "tcp" and re.fullmatch(r"[\d,\-]+", v):
                tcp = v
            elif k == "udp" and re.fullmatch(r"[\d,\-]+", v):
                udp = v
    except OSError:
        pass
    t = tcp if mode in ("all", "tcp") else "12"
    u = udp if mode in ("all", "udp") else "12"
    return (mode if mode in ("all", "tcp", "udp") else "disabled"), t, u


def load_targets() -> List[str]:
    """Цели Discord/YouTube из utils/targets.txt (если нет — значения по умолчанию)."""
    urls = []
    try:
        for line in (_zdir / "utils" / "targets.txt").read_text(encoding="utf-8").splitlines():
            m = re.match(r'\s*(Discord|YouTube)\w*\s*=\s*"(https?://[^"]+)"', line)
            if m:
                urls.append(m.group(2))
    except OSError:
        pass
    return urls or DEFAULT_TARGETS


# --------------------------------------------------------- разбор стратегий
class Strategy:
    def __init__(self, name: str, args: List[str]):
        self.name, self.args = name, args

    def __repr__(self):
        return f"Strategy({self.name!r}, {len(self.args)} args)"


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def _tokenize(s: str) -> List[str]:
    out, cur, q, has = [], [], False, False
    for ch in s:
        if ch == '"':
            q, has = not q, True
        elif ch.isspace() and not q:
            if cur or has:
                out.append("".join(cur))
            cur, has = [], False
        else:
            cur.append(ch)
    if cur or has:
        out.append("".join(cur))
    return out


def parse_bat(path: Path, root: Path, tcp: str = "12", udp: str = "12") -> Optional[Strategy]:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'winws\.exe"?\s+(.*)', text, re.S | re.I)
    if not m:
        return None
    body = re.sub(r"\^\s*\r?\n", " ", m.group(1))
    body = body.split("\n")[0]
    body = body.replace("^!", "!")
    body = (body.replace("%BIN%", str(root / "bin") + "\\")
                .replace("%LISTS%", str(root / "lists") + "\\")
                .replace("%GameFilterTCP%", tcp).replace("%GameFilterUDP%", udp))
    args = _tokenize(body)
    if any("%" in a for a in args):
        return None
    return Strategy(path.stem, args)


def load_strategies() -> List[Strategy]:
    _, tcp, udp = game_filter()
    files = sorted(_zdir.glob("general*.bat"), key=lambda p: _natural(p.stem))
    return [s for s in (parse_bat(f, _zdir, tcp, udp) for f in files) if s]


# ------------------------------------------------------------------- winws
def kill_winws():
    subprocess.run(["taskkill", "/F", "/IM", "winws.exe"], capture_output=True,
                   creationflags=NO_WINDOW)


def zapret_service_running() -> bool:
    r = subprocess.run(["sc", "query", "zapret"], capture_output=True, text=True,
                       creationflags=NO_WINDOW)
    return "RUNNING" in r.stdout


def enable_tcp_timestamps():
    subprocess.run(["netsh", "interface", "tcp", "set", "global", "timestamps=enabled"],
                   capture_output=True, creationflags=NO_WINDOW)


WIN_ERRORS = {
    2: "не найден файл (WinDivert64.sys или другая часть zapret удалена — чаще всего антивирусом)",
    3: "не найден путь (папка zapret повреждена)",
    5: "отказано в доступе (нет прав администратора или блокирует антивирус)",
    87: "неверный параметр (стратегия не подходит этой версии winws)",
    193: "файл повреждён или не подходит для этой Windows (нужна 64-бит Windows 10/11)",
    225: "антивирус заблокировал файл как вирус",
    577: "Windows блокирует драйвер WinDivert (подпись драйвера / Secure Boot / изоляция ядра / антивирус)",
    740: "нужны права администратора",
    1058: "служба WinDivert отключена",
    1060: "служба WinDivert не установлена",
    1275: "драйвер заблокирован Windows (изоляция ядра «Целостность памяти») или антивирусом",
    1450: "не хватает системных ресурсов для драйвера",
}
NT_CODES = {
    0xC0000135: "не найдена DLL (WinDivert.dll или cygwin1.dll удалены антивирусом)",
    0xC000007B: "несовместимая разрядность (нужна 64-бит Windows)",
    0xC0000142: "не удалось инициализировать DLL (блокировка антивирусом)",
    0xC0000005: "аварийное завершение (возможно, вмешивается антивирус)",
    0xC0000022: "доступ запрещён (антивирус или права)",
}


def explain_failure(exit_code: Optional[int], output: str = "", exc: Optional[BaseException] = None) -> str:
    """Человеческое объяснение, почему winws.exe не запустился."""
    if exc is not None:
        we = getattr(exc, "winerror", None)
        base = WIN_ERRORS.get(we, str(exc))
        return f"не удалось запустить winws.exe: {base}" + (f" [код {we}]" if we else "")
    for m in re.finditer(r"(?:win(?:dows)? ?error|error code|GetLastError\(\)|error)\D{0,6}(\d{1,5})", output, re.I):
        n = int(m.group(1))
        if n in WIN_ERRORS:
            return f"{WIN_ERRORS[n]} [код {n}]"
    if exit_code is not None and (exit_code & 0xFFFFFFFF) in NT_CODES:
        return f"{NT_CODES[exit_code & 0xFFFFFFFF]} [0x{exit_code & 0xFFFFFFFF:08X}]"
    # настоящая причина идёт в КОНЦЕ вывода — берём последние строки, а не начало
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    tail = " | ".join(lines[-3:])[-260:]
    low = output.lower()
    hint = ""
    if re.search(r"hostlist|ipset", low) and re.search(r"fail|error|cannot|can't|not found|unable", " ".join(lines[-4:]).lower()):
        hint = ("winws не смог прочитать список доменов/IP из папки lists. Проверьте, что lists\\ на месте, "
                "в пути нет кириллицы, и добавьте папку в исключения антивируса. ")
    elif "windivert" in low:
        hint = "ошибка драйвера WinDivert (права, антивирус, изоляция ядра или конфликт с другим обходчиком). "
    return (f"winws.exe завершился с кодом {exit_code}. {hint}" +
            (f"Последние строки вывода: {tail}" if tail else "Вывода нет")).strip()


class Winws:
    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self.strategy: Optional[Strategy] = None
        self.last_error = ""
        self.fatal = ""
        self._out: List[str] = []

    def _drain(self, proc: subprocess.Popen):
        try:
            for raw in proc.stdout:
                self._out.append(raw.decode("utf-8", "replace").rstrip())
                del self._out[:-80]
        except Exception:
            pass

    def start(self, s: Strategy) -> bool:
        self.stop()
        self.last_error, self._out = "", []
        exe = _zdir / "bin" / "winws.exe"
        if not exe.exists():
            self.last_error = "winws.exe отсутствует в папке zapret (удалён антивирусом?)"
            return False
        try:
            proc = subprocess.Popen([str(exe)] + s.args, cwd=str(exe.parent), creationflags=NO_WINDOW,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as e:
            self.proc = None
            self.last_error = explain_failure(None, exc=e)
            return False
        self.proc = proc
        threading.Thread(target=self._drain, args=(proc,), daemon=True).start()
        for _ in range(10):                      # даём 2 секунды, но выходим сразу, если процесс упал
            time.sleep(0.2)
            if proc.poll() is not None:
                break
        if proc.poll() is not None:
            time.sleep(0.15)                     # дочитать вывод
            self.last_error = explain_failure(proc.returncode, "\n".join(self._out))
            self.proc = None
            return False
        self.strategy = s
        return True

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc, self.strategy = None, None
        kill_winws()

    @property
    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)


# -------------------------------------------------------------- проверка сети
def _probe(url: str) -> bool:
    r = subprocess.run(
        ["curl.exe", "-s", "-o", "NUL", "--http1.1", "-m", "5", "--connect-timeout", "3",
         "--range", "0-16383", url], capture_output=True, creationflags=NO_WINDOW)
    return r.returncode == 0


def check_targets(targets: List[str]) -> int:
    with ThreadPoolExecutor(min(len(targets), 12)) as ex:
        return sum(ex.map(_probe, targets))


def auto_select(winws: Winws, strategies: List[Strategy], preferred: Optional[str],
                log: Log, progress: Callable[[int, int, str, int], None],
                cancel: threading.Event):
    """Перебирает стратегии, выбирает лучшую по числу доступных целей и оставляет запущенной.
    Возвращает (стратегия | None, баллы, всего)."""
    targets = load_targets()
    total = len(targets)
    order = sorted(strategies, key=lambda s: s.name != preferred)  # сохранённая — первой
    best, best_score = None, -1
    winws.fatal = ""
    fails: List[str] = []
    for i, s in enumerate(order, 1):
        if cancel.is_set():
            winws.stop()
            return None, 0, total
        progress(i - 1, len(order), s.name, max(best_score, 0))
        if not winws.start(s):
            log(f"[{i}/{len(order)}] {s.name}: не запустилась — {winws.last_error}")
            fails.append(winws.last_error)
            # одна и та же причина у первых трёх — дальше перебирать бессмысленно (проблема не в стратегии)
            if best is None and len(fails) >= 3 and len(set(fails[-3:])) == 1:
                winws.fatal = fails[-1]
                log("winws.exe не запускается ни с одной стратегией — перебор остановлен.")
                break
            continue
        fails.clear()
        score = check_targets(targets)
        log(f"[{i}/{len(order)}] {s.name}: {score}/{total}")
        if score > best_score:
            best, best_score = s, score
        if score == total:
            break
    progress(len(order), len(order), best.name if best else "", max(best_score, 0))
    if not best or best_score <= 0:
        winws.stop()
        return None, 0, total
    if winws.strategy is not best:
        winws.start(best)
    log(f"Выбрана стратегия: {best.name} ({best_score}/{total})")
    return best, best_score, total
