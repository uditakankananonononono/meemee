import asyncio
import os
import signal
import sys
import tempfile
from pathlib import Path

from meemee.tools.shell import ShellArgs, ShellCommand


async def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        marker, ready = root / "marker", root / "ready"
        child = (
            "import time,pathlib; time.sleep(0.5); pathlib.Path('marker').write_text('survived')"
        )
        parent = f"import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c',{child!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); pathlib.Path('ready').write_text(str(p.pid)); time.sleep(30)"
        task = asyncio.create_task(
            ShellCommand(root, {Path(sys.executable).name}).run(
                ShellArgs(argv=[sys.executable, "-c", parent])
            )
        )
        try:
            for _ in range(500):
                if ready.exists():
                    break
                await asyncio.sleep(0.01)
            pid = int(ready.read_text())
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0.8)
            print(
                f"cancellation: child pid={pid}; marker_exists={marker.exists()}; marker={marker.read_text() if marker.exists() else None}"
            )
        finally:
            if ready.exists():
                try:
                    os.kill(int(ready.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if not task.done():
                task.cancel()


asyncio.run(main())
