"""The acceptance sandbox admits only the production bounded PDF parser."""
import importlib.util
from pathlib import Path


def test_pdf_worker_launch_allowlist_rejects_other_commands_and_environment():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("build44_acceptance", root / "scripts/run_review_v2_acceptance.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    worker = root / "backend/shipping_pdf_worker.py"
    clean = {"PATH": runner.os.defpath, "LANG": "C.UTF-8"}
    valid = (runner.sys.executable, [runner.sys.executable, "-I", str(worker)], str(worker.parent), clean)
    assert runner.allowed_pdf_worker("subprocess.Popen", valid)
    assert not runner.allowed_pdf_worker("os.system", valid)
    assert not runner.allowed_pdf_worker("os.posix_spawn", valid)
    for argv in ([runner.sys.executable, "-c", "pass"],
                 [runner.sys.executable, str(worker)],
                 [runner.sys.executable, "-I", str(worker), "extra"]):
        assert not runner.allowed_pdf_worker("subprocess.Popen", (valid[0], argv, valid[2], clean))
    assert not runner.allowed_pdf_worker("subprocess.Popen", (*valid[:3], {**clean, "JWT_SECRET": "synthetic"}))
    assert not runner.allowed_pdf_worker("subprocess.Popen", (*valid[:3], None))
    assert not runner.allowed_pdf_worker("subprocess.Popen", (valid[0], valid[1], str(root), clean))
