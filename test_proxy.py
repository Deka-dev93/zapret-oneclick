"""Смоук-тест прокси: запуск ядра tg-ws-proxy, порт слушается, мусорное рукопожатие не роняет, остановка."""
import socket, time
from tgproxy import TgProxy

p = TgProxy({}, lambda: None)
print("порт/secret из:", "TgWsProxy" if p.imported else "own", p.port, p.secret[:6] + "…")
assert not p.port_busy(), "порт уже занят — закройте TgWsProxy"
assert p.start(), p.error
assert p.running and p.port_busy()
s = socket.create_connection((p.host, p.port), 3); s.sendall(b"\x00" * 64); time.sleep(0.5); s.close()
assert p.running
p.stop(); time.sleep(0.5)
assert not p.running and not p.port_busy()
print("proxy OK: старт, приём соединения, остановка")
