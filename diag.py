"""Диагностика: выясняет, почему не работает обход (права, файлы, антивирус, драйвер, сеть)."""
from __future__ import annotations

import ctypes
import os
import platform
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import engine

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
NO_WINDOW = engine.NO_WINDOW


@dataclass
class Item:
    status: str
    title: str
    detail: str = ""


def _run(cmd, timeout=15) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return r.stdout.decode("oem", "replace") + r.stderr.decode("oem", "replace")
    except Exception as e:  # noqa: BLE001
        return f"ERR {e}"


def _processes() -> set:
    out = _run(["tasklist", "/fo", "csv", "/nh"])
    return {m.lower() for m in re.findall(r'^"([^"]+)"', out, re.M)}


def _service_state(name: str) -> Optional[str]:
    out = _run(["sc", "query", name])   # вывод локализован — ищем только английские коды состояния
    m = re.search(r":\s*\d+\s+(RUNNING|STOPPED|START_PENDING|STOP_PENDING|PAUSED|PAUSE_PENDING|CONTINUE_PENDING)\b", out)
    return m.group(1) if m else None


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ── отдельные проверки ─────────────────────────────────────────────────────
def check_admin() -> Item:
    if _is_admin():
        return Item(OK, "Права администратора", "приложение запущено от администратора")
    return Item(FAIL, "Нет прав администратора",
                "драйвер WinDivert не загрузится. Закройте приложение и запустите ещё раз, подтвердив окно UAC")


def check_windows() -> Item:
    v = sys.getwindowsversion()
    arch = platform.machine().upper()
    build = v.build
    if arch not in ("AMD64", "X86_64"):
        return Item(FAIL, f"Архитектура {arch}", "нужна 64-битная Windows (x64); на ARM / 32-бит не работает")
    if v.major < 10:
        return Item(WARN, f"Windows {v.major}.{v.minor} (сборка {build})",
                    "для Windows 7/8 нужны другие файлы WinDivert (см. README zapret)")
    return Item(OK, f"Windows {v.major} (сборка {build}), {arch}", "")


def check_path(zdir: Path) -> Item:
    p = str(zdir)
    probs = []
    if re.search(r"[^\x00-\x7f]", p):
        probs.append("в пути есть кириллица или спецсимволы")
    if "onedrive" in p.lower():
        probs.append("папка в OneDrive")
    if probs:
        return Item(WARN, "Путь к папке zapret", f"{p} — {', '.join(probs)}. Лучше C:\\zapret")
    return Item(OK, "Путь к папке zapret", p)


def check_files(zdir: Path) -> Item:
    if not zdir.exists():
        return Item(FAIL, "Папка zapret не найдена", f"{zdir} — нажмите «Скачать свежий zapret» в Настройках")
    miss = engine.missing_files(zdir)
    if miss:
        return Item(FAIL, "Не хватает файлов zapret", ", ".join(miss) +
                    " — их почти наверняка удалил антивирус. Добавьте папку в исключения, "
                    "восстановите из карантина или скачайте zapret заново")
    n = len(list(zdir.glob("general*.bat")))
    return Item(OK, "Файлы zapret на месте", f"winws.exe, WinDivert, cygwin1.dll; стратегий: {n}")


def check_bfe() -> Item:
    st = _service_state("BFE")
    if st == "RUNNING":
        return Item(OK, "Служба фильтрации (BFE)", "запущена")
    return Item(FAIL, "Служба Base Filtering Engine не запущена",
                "без неё WinDivert не работает. Запустите службу «Служба базовой фильтрации» (services.msc)")


def check_conflicts() -> Item:
    probs = []
    if _service_state("zapret") in ("RUNNING", "STOP_PENDING", "START_PENDING"):
        probs.append("установлена и запущена служба zapret")
    procs = _processes()
    for pn in ("goodbyedpi.exe", "zapret.exe", "winws2.exe"):
        if pn in procs:
            probs.append(f"процесс {pn}")
    if "winws.exe" in procs:
        probs.append("уже запущен winws.exe (другая копия zapret)")
    if probs:
        return Item(FAIL, "Конфликтующий обходчик", "; ".join(probs) +
                    ". Остановите/удалите (service.bat → Remove Services) и закройте программу")
    stale = [s for s in ("WinDivert", "WinDivert14") if _service_state(s) in ("RUNNING", "START_PENDING")]
    if stale:
        return Item(WARN, "Драйвер WinDivert уже загружен",
                    f"служба {', '.join(stale)} осталась в памяти после прошлого запуска. Обычно безвредно; "
                    "если winws.exe не стартует (ошибка 10/177) — выполните в командной строке: "
                    "sc stop WinDivert, затем sc delete WinDivert")
    return Item(OK, "Конфликтующих обходчиков нет", "")


def check_vpn() -> Item:
    procs = _processes()
    known = {"openvpn.exe": "OpenVPN", "wireguard.exe": "WireGuard", "nekoray.exe": "NekoRay",
             "v2rayn.exe": "v2rayN", "clash.exe": "Clash", "clash-verge.exe": "Clash Verge",
             "hiddify.exe": "Hiddify", "outline.exe": "Outline", "nordvpn.exe": "NordVPN",
             "protonvpn.exe": "ProtonVPN", "cloudflare warp.exe": "WARP", "warp-svc.exe": "WARP"}
    hit = [v for k, v in known.items() if k in procs]
    prox = _run(["reg", "query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                 "/v", "ProxyEnable"])
    sys_proxy = "0x1" in prox
    msgs = []
    if hit:
        msgs.append("запущен VPN/прокси: " + ", ".join(hit))
    if sys_proxy:
        msgs.append("включён системный прокси Windows")
    if msgs:
        return Item(WARN, "VPN / прокси", "; ".join(msgs) + ". Отключите их на время подбора стратегии")
    return Item(OK, "VPN и прокси не обнаружены", "")


def check_antivirus(zdir: Path) -> List[Item]:
    items = []
    procs = _processes()
    if "avp.exe" in procs:
        items.append(Item(WARN, "Обнаружен Kaspersky",
                          "добавьте папку zapret, ZapretOneClick.exe и winws.exe в исключения и доверенные программы; "
                          "выключите «Сканирование защищённых соединений», «Безопасное соединение», «Защиту от сетевых атак»"))
    for name, label in (("avastsvc.exe", "Avast"), ("avgsvc.exe", "AVG"), ("egui.exe", "ESET"),
                        ("mbam.exe", "Malwarebytes"), ("drweb", "Dr.Web"), ("bdagent.exe", "Bitdefender")):
        if any(name in p for p in procs):
            items.append(Item(WARN, f"Обнаружен {label}", "добавьте папку zapret и ZapretOneClick.exe в исключения"))
    # Defender: детекты по нашим файлам и исключения
    ps = ("$d = Get-MpThreatDetection -ErrorAction SilentlyContinue | ForEach-Object { $_.Resources }; "
          "$e = (Get-MpPreference -ErrorAction SilentlyContinue).ExclusionPath; "
          "'DET:' + ($d -join '|'); 'EXC:' + ($e -join '|')")
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=25)
    det = re.search(r"DET:(.*)", out)
    exc = re.search(r"EXC:(.*)", out)
    if det is not None:
        hits = [x for x in det.group(1).split("|") if re.search(r"winws|windivert|zapret|cygwin1", x, re.I)]
        if hits:
            items.append(Item(FAIL, "Защитник Windows удалял файлы zapret",
                              f"{len(hits)} срабатываний (например {hits[0][-70:]}). Восстановите из карантина "
                              "и добавьте папку в исключения: Безопасность Windows → Защита от вирусов → Исключения"))
        elif "avp.exe" not in procs:
            paths = [x.strip().lower().rstrip("\\") for x in (exc.group(1).split("|") if exc else []) if x.strip()]
            covered = any(str(zdir).lower().startswith(p) for p in paths)
            if not covered:
                items.append(Item(INFO, "Папка zapret не в исключениях Защитника",
                                  "если файлы пропадают или winws.exe не стартует — добавьте её в исключения"))
    if not items:
        items.append(Item(OK, "Признаков блокировки антивирусом не найдено", ""))
    return items


def check_hvci() -> Item:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity") as k:
            enabled = winreg.QueryValueEx(k, "Enabled")[0]
        if enabled == 1:
            return Item(WARN, "Включена «Целостность памяти» (изоляция ядра)",
                        "может блокировать драйвер WinDivert (ошибка 577/1275). "
                        "Безопасность Windows → Безопасность устройства → Изоляция ядра → выключить, перезагрузить ПК")
    except OSError:
        pass
    return Item(OK, "Изоляция ядра не блокирует драйверы", "")


def check_live(zdir: Path, strategies, running: Optional[engine.Winws]) -> Item:
    if running is not None and running.alive:
        return Item(OK, "winws.exe сейчас работает", running.strategy.name if running.strategy else "")
    if not strategies:
        return Item(FAIL, "Пробный запуск winws.exe", "нет ни одной стратегии (повреждена папка zapret)")
    w = engine.Winws()
    ok = w.start(strategies[0])
    err = w.last_error
    w.stop()
    if ok:
        return Item(OK, "Пробный запуск winws.exe", f"процесс стартует (стратегия «{strategies[0].name}»)")
    return Item(FAIL, "winws.exe не запускается", err)


def check_dns() -> Item:
    try:
        ip = socket.gethostbyname("discord.com")
    except OSError as e:
        return Item(FAIL, "DNS не разрешает discord.com", f"{e}. Проверьте интернет и DNS")
    bad = ip.startswith(("127.", "0.", "10.", "192.168.", "172.16.")) or ip == "255.255.255.255"
    if bad:
        return Item(WARN, "DNS подменяет discord.com", f"получен адрес {ip}. Включите Secure DNS (DoH) в браузере/системе")
    return Item(INFO, "DNS", f"discord.com → {ip}. Для надёжной работы включите Secure DNS (DoH) в браузере")


def check_internet() -> Item:
    ok = engine._probe("https://www.cloudflare.com")
    if ok:
        return Item(OK, "Интернет", "доступ к внешним сайтам есть")
    return Item(FAIL, "Нет доступа в интернет", "не открывается даже cloudflare.com — проверьте подключение")


def check_baseline() -> Item:
    tg = engine.load_targets() if engine.bundle_ready() else engine.DEFAULT_TARGETS
    n = engine.check_targets(tg)
    if n == len(tg):
        return Item(OK, "Discord и YouTube доступны", f"{n} из {len(tg)} адресов открываются — обход может быть не нужен")
    return Item(INFO, "Discord и YouTube без обхода", f"доступно {n} из {len(tg)} адресов (блокировка есть)")


# ── общий прогон ───────────────────────────────────────────────────────────
def run(zdir: Path, running: Optional[engine.Winws], emit: Callable[[Item], None],
        stage: Callable[[str], None] = lambda s: None) -> Item:
    """Запускает все проверки, по ходу вызывает emit(Item). Возвращает вердикт (Item)."""
    results: List[Item] = []

    def add(it):
        for x in (it if isinstance(it, list) else [it]):
            results.append(x)
            emit(x)

    stage("Права и система…")
    add(check_admin())
    add(check_windows())
    stage("Файлы zapret…")
    add(check_path(zdir))
    add(check_files(zdir))
    stage("Службы и конфликты…")
    add(check_bfe())
    add(check_conflicts())
    add(check_vpn())
    stage("Антивирус…")
    add(check_antivirus(zdir))
    add(check_hvci())
    stage("Пробный запуск winws.exe…")
    strategies = engine.load_strategies() if engine.is_zapret_dir(zdir) else []
    add(check_live(zdir, strategies, running))
    stage("Сеть…")
    add(check_internet())
    add(check_dns())
    add(check_baseline())

    fails = [r for r in results if r.status == FAIL]
    warns = [r for r in results if r.status == WARN]
    if fails:
        f = fails[0]
        return Item(FAIL, "Вероятная причина: " + f.title, f.detail)
    if warns:
        w = warns[0]
        return Item(WARN, "Возможная причина: " + w.title, w.detail)
    return Item(OK, "Серьёзных проблем не найдено",
                "запуск возможен. Если сайты не открываются — включите Secure DNS (DoH) и отключите VPN")


def report_text(items: List[Item], verdict: Item) -> str:
    sym = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]", INFO: "[INFO]"}
    lines = ["Zapret OneClick — отчёт диагностики", "=" * 40, f"{sym[verdict.status]} {verdict.title}"]
    if verdict.detail:
        lines.append("       " + verdict.detail)
    lines.append("")
    for it in items:
        lines.append(f"{sym[it.status]} {it.title}")
        if it.detail:
            lines.append("       " + it.detail)
    return "\n".join(lines)
