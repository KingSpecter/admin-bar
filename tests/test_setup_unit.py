"""The installer's pieces, in-process and sandboxed (see test_setup_sim.py for whole runs of Install.cmd)."""
import importlib
import os
import shutil
import sys
from collections import namedtuple
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR / "adminbar"))


@pytest.fixture
def inst(monkeypatch, tmp_path):
    sb = tmp_path / "sb"
    sb.mkdir()
    monkeypatch.setenv("ADMINBAR_SANDBOX", str(sb))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    import install
    mod = importlib.reload(install)
    yield mod
    monkeypatch.delenv("ADMINBAR_SANDBOX")
    importlib.reload(install)


def fake_src(tmp_path):
    src = tmp_path / "AdminBar"
    (src / "runtime").mkdir(parents=True)
    (src / "runtime" / "pythonw.exe").write_bytes(b"fake")
    (src / "runtime" / "python.exe").write_bytes(b"fake")
    shutil.copytree(APP_DIR, src / "app", ignore=shutil.ignore_patterns("__pycache__"))
    return src


def test_dry_install_replaces_the_old_layout(inst, monkeypatch, tmp_path):
    monkeypatch.setattr(inst, "self_check", lambda stage: "")
    monkeypatch.setattr(inst, "find_runtime", lambda src: src / "runtime")
    src = fake_src(tmp_path)
    dest = inst.install_dir()
    (dest / "adminbar").mkdir(parents=True)                         # a 1.1 install
    (dest / "Admin Bar.pyw").write_text("old")
    (dest / "Uninstall.cmd").write_text("old")
    assert inst.install(str(src), dry=True) == 0
    assert (dest / "app" / "Admin Bar.pyw").exists() and (dest / "runtime" / "pythonw.exe").exists()
    assert sorted(p.name for p in dest.iterdir()) == ["app", "runtime"]
    assert inst.install(str(tmp_path / "nothing"), dry=True) == 7


def test_a_copy_that_fails_its_check_changes_nothing(inst, monkeypatch, tmp_path):
    monkeypatch.setattr(inst, "self_check", lambda stage: "boom")
    monkeypatch.setattr(inst, "find_runtime", lambda src: src / "runtime")
    src = fake_src(tmp_path)
    dest = inst.install_dir()
    (dest / "app").mkdir(parents=True)
    (dest / "app" / "old.txt").write_text("the working install")
    with pytest.raises(inst.SetupError, match="does not start: boom"):
        inst.install(str(src))
    assert (dest / "app" / "old.txt").read_text() == "the working install"
    assert not [p for p in dest.iterdir() if p.name.startswith(".stage")]


def test_swap_puts_the_old_folders_back_when_it_fails(inst, tmp_path, monkeypatch):
    dest, stage = tmp_path / "dest", tmp_path / "dest" / ".stage-1"
    for part in ("app", "runtime"):
        (dest / part).mkdir(parents=True)
        (dest / part / "v").write_text("old")
        (stage / part).mkdir(parents=True)
        (stage / part / "v").write_text("new")
    real = Path.rename

    def rename(self, target):
        if self == stage / "runtime":
            raise PermissionError(5, "in use")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(inst.time, "sleep", lambda s: None)
    with pytest.raises(OSError):
        inst.swap_in(stage, dest)
    assert (dest / "app" / "v").read_text() == "old" and (dest / "runtime" / "v").read_text() == "old"
    assert (stage / "app" / "v").read_text() == "new"
    assert not [p for p in dest.iterdir() if p.name.startswith(".old-")]


def test_the_file_list_cannot_point_outside(inst, tmp_path):
    app = tmp_path / "app"
    (app / "adminbar" / "data").mkdir(parents=True)
    (app / "adminbar" / "data" / "package.sha256").write_text("ab 1 app/../../evil.txt\n")
    with pytest.raises(inst.SetupError, match="damaged"):
        inst.read_manifest(app)
    (app / "adminbar" / "data" / "package.sha256").write_text("ab 1 windows/x.dll\n")
    with pytest.raises(inst.SetupError):
        inst.read_manifest(app)


def test_staging_checks_sizes_and_hashes(inst, tmp_path):
    src = tmp_path / "src"
    (src / "app").mkdir(parents=True)
    (src / "runtime").mkdir()
    (src / "app" / "a.txt").write_bytes(b"hello")
    (src / "runtime" / "b.txt").write_bytes(b"world")
    import hashlib
    good = [("app/a.txt", 5, hashlib.sha256(b"hello").hexdigest()),
            ("runtime/b.txt", 5, hashlib.sha256(b"world").hexdigest())]
    assert inst.stage_files(src, src / "runtime", good, tmp_path / "stage") == 2
    assert (tmp_path / "stage" / "runtime" / "b.txt").read_bytes() == b"world"
    bad = good[:1] + [("runtime/b.txt", 5, "0" * 64), ("runtime/c.txt", 1, "0" * 64)]
    with pytest.raises(inst.SetupError, match=r"1 file\(s\) missing and 1 damaged"):
        inst.stage_files(src, src / "runtime", bad, tmp_path / "stage2")


def test_copies_carry_no_read_only_flag(inst, tmp_path):
    import stat
    f = tmp_path / "ro.txt"
    f.write_text("x")
    os.chmod(f, stat.S_IREAD)
    inst._copy(f, tmp_path / "out" / "ro.txt")
    assert os.access(tmp_path / "out" / "ro.txt", os.W_OK)
    os.chmod(f, stat.S_IWRITE)


def test_not_enough_disk_space(inst, monkeypatch, tmp_path):
    monkeypatch.setattr(inst, "find_runtime", lambda src: src / "runtime")
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(inst.shutil, "disk_usage", lambda p: Usage(1, 1, 5 * 1024 * 1024))
    with pytest.raises(inst.SetupError, match="Not enough free disk space"):
        inst.install(str(fake_src(tmp_path)))


def test_protected_folder_check(inst, tmp_path):
    guards = [tmp_path / "Desktop"]
    assert inst.guarded(tmp_path / "Desktop" / "AdminBar", guards)
    assert not inst.guarded(tmp_path / "Downloads" / "AdminBar", guards)
    assert not inst.guarded(tmp_path / "Desktop2", guards)


def test_controlled_folder_access_reading_on_this_pc():
    import install
    importlib.reload(install)
    assert isinstance(install.controlled_folders(), list)  # reads, never fails


def test_who_runs_setup_on_this_pc():
    import install
    assert install.who_runs_setup() in ("normal", "admin-same-user")


def test_no_process_lives_in_an_empty_folder(inst, tmp_path):
    assert inst.processes_from(tmp_path) == []


def test_long_path_prefix(inst):
    short = r"C:\a\b.txt"
    assert inst.long_path(short) == short
    long = "C:\\" + "x" * 250 + "\\f.txt"
    assert inst.long_path(long).startswith("\\\\?\\C:\\")
    assert inst.long_path("\\\\server\\share\\" + "y" * 250).startswith("\\\\?\\UNC\\server\\share\\")


def test_version_is_read_not_imported(inst):
    from adminbar import VERSION
    assert inst.read_version(APP_DIR) == VERSION
