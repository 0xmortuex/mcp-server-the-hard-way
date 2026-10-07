"""Run every module's main.py against a real QEMU, the way CI does.

Each module gets a fresh VM: the test kernel in kernel/, halted at reset
(-S) with its gdbstub on a free port and an interrupt log. The module's
main.py is then run as

    python NN-module/main.py --port PORT --elf kernel/kernel.elf --log LOG

and must exit 0. A main.py checks its own results with assert, so a chapter
whose code drifts out of sync with what its README claims fails here.

Usage: python verify.py            # all modules
       python verify.py 04 06      # just these
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
ELF = os.path.join(ROOT, "kernel", "kernel.elf")


def find_qemu() -> str:
    found = shutil.which("qemu-system-i386")
    if found:
        return found
    win = r"C:\Program Files\qemu\qemu-system-i386.exe"
    if os.path.isfile(win):
        return win
    sys.exit("qemu-system-i386 not found - install QEMU first")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port: int = s.getsockname()[1]
    s.close()
    return port


def run_module(qemu: str, module: str) -> tuple[bool, str, float]:
    script = os.path.join(ROOT, module, "main.py")
    port = free_port()
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "int.log")
        vm = subprocess.Popen(
            [qemu, "-display", "none", "-kernel", ELF, "-gdb", f"tcp:127.0.0.1:{port}", "-S",
             "-d", "int,cpu_reset", "-D", log, "-no-reboot"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        start = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, script, "--port", str(port), "--elf", ELF, "--log", log],
                capture_output=True, text=True, timeout=120, cwd=os.path.join(ROOT, module),
            )
            ok = proc.returncode == 0
            output = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            ok, output = False, "timed out after 120s"
        finally:
            vm.kill()
            vm.wait()
        return ok, output, time.monotonic() - start


def main() -> int:
    qemu = find_qemu()
    wanted = sys.argv[1:]
    modules = sorted(
        d for d in os.listdir(ROOT)
        if os.path.isfile(os.path.join(ROOT, d, "main.py"))
        and (not wanted or any(d.startswith(w) for w in wanted))
    )
    if not modules:
        print("no modules matched")
        return 1
    failed = []
    for m in modules:
        ok, output, secs = run_module(qemu, m)
        print(f"{'PASS' if ok else 'FAIL'}  {m}  ({secs:.1f}s)")
        if not ok:
            failed.append(m)
            print("    " + output.strip().replace("\n", "\n    "))
    print(f"\n{len(modules) - len(failed)}/{len(modules)} modules verified against real QEMU")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
