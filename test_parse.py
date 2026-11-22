"""Проверка разбора на реальной локальной папке zapret и на синтетическом .bat."""
import tempfile
from pathlib import Path
import engine

BAT = r'''@echo off
set "BIN=%~dp0bin\"
start "zapret: %~n0" /min "%BIN%winws.exe" --wf-tcp=80,443,%GameFilterTCP% --wf-udp=443,%GameFilterUDP% ^
--filter-udp=443 --hostlist="%LISTS%list-general.txt" --new ^
--filter-tcp=443 --dpi-desync-fake-tls=^! --new ^
--filter-udp=%GameFilterUDP% --dpi-desync-cutoff=n2
'''
d = Path(tempfile.mkdtemp(prefix="my dir "))
f = d / "general (ALT).bat"
f.write_text(BAT, encoding="utf-8")
s = engine.parse_bat(f, d)
assert s.args[0] == "--wf-tcp=80,443,12" and "--dpi-desync-fake-tls=!" in s.args
assert s.args[3] == f"--hostlist={d}\\lists\\list-general.txt" and s.args[-1] == "--dpi-desync-cutoff=n2"
print("synthetic OK")

if engine.KNOWN_LOCAL and engine.KNOWN_LOCAL.exists():  # задайте ZOC_ZAPRET_DIR для проверки на реальной папке
    engine.set_zapret_dir(engine.KNOWN_LOCAL)
    print("game filter:", engine.game_filter())
    print("targets:", len(engine.load_targets()), engine.load_targets()[:2])
    files = list(engine.KNOWN_LOCAL.glob("general*.bat"))
    sts = engine.load_strategies()
    print(f"strategies parsed: {len(sts)}/{len(files)}")
    assert len(sts) == len(files), [f.stem for f in files if f.stem not in {s.name for s in sts}]
    print(sts[0].name, "->", " ".join(sts[0].args)[:230])
