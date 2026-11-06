"""Actual parent/child pipe wakes assembly independently from financial jobs."""
import asyncio
from pathlib import Path
import tempfile
import unittest

from background_worker_process import ManagedWorker


class CompletionWakeupTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_worker_pipe_routes_assembly_and_existing_wakeup_separately(self):
        with tempfile.TemporaryDirectory(prefix="assembly-wakeup-") as directory:
            root = Path(directory)
            ready, assembly, financial = (root / name for name in ("ready", "assembly", "financial"))
            script = root / "child.py"
            backend = str(Path(__file__).resolve().parents[1])
            script.write_text(
                "import asyncio, sys\nfrom pathlib import Path\n"
                f"sys.path.insert(0, {backend!r})\n"
                "from background_worker_runner import parent_closed\n"
                "async def main():\n"
                "    financial, assembly = asyncio.Event(), asyncio.Event()\n"
                "    parent = asyncio.create_task(parent_closed(financial, assembly))\n"
                f"    Path({str(ready)!r}).write_text('ready')\n"
                "    await assembly.wait()\n"
                f"    Path({str(assembly)!r}).write_text('wrong' if financial.is_set() else 'assembly-only')\n"
                "    await financial.wait()\n"
                f"    Path({str(financial)!r}).write_text('existing-wakeup')\n"
                "    await parent\n"
                "asyncio.run(main())\n", encoding="utf-8")
            worker = ManagedWorker(worker_path=script, restart_delay=10, shutdown_timeout=3)
            worker.start()

            async def wait_file(path):
                async with asyncio.timeout(5):
                    while not path.exists():
                        await asyncio.sleep(0.01)

            try:
                await wait_file(ready)
                self.assertTrue(await worker.wake(assembly=True))
                await wait_file(assembly)
                self.assertEqual(assembly.read_text(), "assembly-only")
                self.assertTrue(await worker.wake())
                await wait_file(financial)
                self.assertEqual(financial.read_text(), "existing-wakeup")
            finally:
                await worker.stop()
            self.assertEqual(worker.process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
