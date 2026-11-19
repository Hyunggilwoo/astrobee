import importlib.util
import os

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PATCH = os.path.join(ROOT, "hci", "docker", "scripts", "patch_nav_cam.py")


def _load():
    spec = importlib.util.spec_from_file_location("patch_nav_cam", PATCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_patch_changes_only_nav_cam_rate():
    module = _load()
    original = open(os.path.join(ROOT, "astrobee", "config", "simulation", "simulation.config")).read()
    assert "nav_cam_rate   = 0.0;" in original
    patched = module.patch_text(original, "2.0")
    assert "nav_cam_rate   = 2.0;" in patched
    assert "dock_cam_rate  = 0.0;" in patched
    assert "haz_cam_rate   = 0.0;" in patched
    assert patched != original
    assert module.patch_text(patched, "2.0") == patched


def test_patch_rejects_a_bad_rate():
    module = _load()
    try:
        module.patch_text("nav_cam_rate = 0.0;\n", "2.0; rm -rf")
    except ValueError:
        return
    raise AssertionError("bad rate was accepted")
