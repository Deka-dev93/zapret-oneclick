"""Обёртка над ядром Flowseal/tg-ws-proxy (папка proxy/, MIT).

Настройки (порт, secret, dc_ip, Cloudflare-fallback) берутся из конфига установленного
TgWsProxy (%APPDATA%\\TgWsProxy\\config.json), поэтому уже настроенный Telegram продолжает
работать без изменений. Если конфига нет — создаётся свой.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from proxy.config import coerce_domain_list, parse_dc_ip_list, proxy_config
from proxy.tg_ws_proxy import _run

log = logging.getLogger("tgproxy")

DEFAULTS = {
    "port": 1443, "host": "127.0.0.1", "dc_ip": ["2:149.154.167.220", "4:149.154.167.220"],
    "buf_kb": 256, "pool_size": 4, "cfproxy": True, "h2": True,
    "cfproxy_user_domain_enabled": False, "cfproxy_user_domain": [],
    "cfproxy_worker_enabled": False, "cfproxy_worker_domain": [],
    "force_test_dc": False, "no_secure": False,
}
TGWS_CONFIG = Path(os.environ.get("APPDATA", "")) / "TgWsProxy" / "config.json"


def load_tgws_config() -> dict:
    try:
        return json.loads(TGWS_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


class TgProxy:
    def __init__(self, own_cfg: dict, save: Callable[[], None]):
        self.own, self.save = own_cfg, save
        self.cfg = self._merge()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_ev: Optional[asyncio.Event] = None
        self.error: Optional[str] = None
        self.imported = bool(load_tgws_config().get("secret"))

    def _merge(self) -> dict:
        cfg = dict(DEFAULTS)
        cfg.update({k: v for k, v in load_tgws_config().items() if k in DEFAULTS or k == "secret"})
        if not cfg.get("secret"):
            if "tg_secret" not in self.own:
                self.own["tg_secret"] = os.urandom(16).hex()
                self.save()
            cfg["secret"] = self.own["tg_secret"]
        return cfg

    @property
    def host(self): return self.cfg["host"]

    @property
    def port(self): return int(self.cfg["port"])

    @property
    def secret(self): return self.cfg["secret"]

    @property
    def link(self) -> str:
        return f"tg://proxy?server={self.host}&port={self.port}&secret=dd{self.secret}"

    def port_busy(self) -> bool:
        with socket.socket() as s:
            s.settimeout(0.5)
            return s.connect_ex((self.host, self.port)) == 0

    def _apply(self):
        c, pc = self.cfg, proxy_config
        pc.port, pc.host, pc.secret = self.port, self.host, self.secret
        pc.dc_redirects = parse_dc_ip_list(c["dc_ip"])
        pc.buffer_size = max(4, c["buf_kb"]) * 1024
        pc.pool_size = max(0, c["pool_size"])
        pc.fallback_cfproxy = c["cfproxy"]
        pc.cfproxy_h2_media = c["h2"]
        ud = coerce_domain_list(c["cfproxy_user_domain"])
        wd = coerce_domain_list(c["cfproxy_worker_domain"])
        pc.cfproxy_user_domains = ud if c["cfproxy_user_domain_enabled"] else []
        pc.cfproxy_worker_domains = wd if c["cfproxy_worker_enabled"] else []
        pc.force_test_dc = c["force_test_dc"]
        pc.disable_secure = c["no_secure"]

    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_ev = asyncio.Event()
        try:
            self._loop.run_until_complete(_run(stop_event=self._stop_ev))
        except OSError as e:
            self.error = f"Порт {self.port} занят (закройте TgWsProxy в трее): {e}"
        except Exception as e:  # noqa: BLE001
            self.error = f"Ошибка прокси: {e}"
        finally:
            try:
                pend = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
                for t in pend:
                    t.cancel()
                if pend:
                    self._loop.run_until_complete(asyncio.gather(*pend, return_exceptions=True))
            finally:
                self._loop.close()

    def start(self) -> bool:
        if self.running:
            return True
        self.error = None
        self.cfg = self._merge()
        if self.port_busy():
            self.error = (f"Порт {self.port} уже занят — вероятно, запущен TgWsProxy. "
                          "Закройте его в трее или используйте его.")
            return False
        try:
            self._apply()
        except ValueError as e:
            self.error = f"Неверный dc_ip: {e}"
            return False
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="tgproxy")
        self._thread.start()
        for _ in range(30):  # ждём, пока порт начнёт слушать
            time.sleep(0.1)
            if self.error or self.port_busy():
                break
        return self.error is None

    def stop(self):
        if self._loop and self._stop_ev and self._thread and self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._stop_ev.set)
            self._thread.join(5)
        self._thread = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self.error)
