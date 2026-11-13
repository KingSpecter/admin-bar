"""Simulated installs: the real Install.cmd from the real dist/AdminBar.zip, run by cmd.exe the way a
double-click runs it, inside a throw-away Windows profile (ADMINBAR_SANDBOX).

Nothing here touches the real PC: LOCALAPPDATA / APPDATA / USERPROFILE / TEMP / OneDrive point into the
sandbox, the registry is a JSON file there, Desktop and Start menu are sandbox folders, the quit / setup
events have sandbox-only names, and the app is only self-checked, never started. PATH holds only Windows'
own folders, so any use of an installed Python would fail the run.

    python -m pytest -q tests/test_setup_sim.py          (needs dist/AdminBar.zip: python -I build.py zip)
"""
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ADMINBAR = HERE.parent
ZIP = next((d / "dist" / "AdminBar.zip" for d in (ADMINBAR.parent, ADMINBAR) if (d / "dist" / "AdminBar.zip").exists()),
           ADMINBAR / "dist" / "AdminBar.zip")
SYSROOT = os.environ.get("SystemRoot", r"C:\Windows")
RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINST = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AdminBar"
SHORTCUTS = ["Start Admin Bar", "Stop Admin Bar", "Admin Bar settings", "Punishment log", "Uninstall Admin Bar"]

pytestmark = pytest.mark.skipif(not ZIP.exists() or sys.platform != "win32", reason="needs dist/AdminBar.zip")


def lp(p):
    s = os.path.abspath(str(p))
    return s if s.startswith("\\\\?\\") else "\\\\?\\" + s


class Box:
    """One throw-away Windows profile."""

    def __init__(self, root: Path, home_name="home"):
        self.root = root
        self.home = root / home_name
        self.local = self.home / "AppData" / "Local"
        self.roaming = self.home / "AppData" / "Roaming"
        self.temp = root / "temp"
        for p in (self.local, self.roaming, self.temp, self.home / "Downloads", self.home / "Desktop",
                  self.home / "Documents"):
            p.mkdir(parents=True, exist_ok=True)
        self.dest = self.local / "Programs" / "AdminBar"
        self.data = self.roaming / "AdminBar"
        self.extra = {}

    def env(self, **more):
        env = {k: v for k, v in os.environ.items()
               if k.upper() not in ("PATH", "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV")}
        env.update(PATH=f"{SYSROOT}\\System32;{SYSROOT};{SYSROOT}\\System32\\Wbem",
                   USERPROFILE=str(self.home), LOCALAPPDATA=str(self.local), APPDATA=str(self.roaming),
                   TEMP=str(self.temp), TMP=str(self.temp), OneDrive=str(self.home / "OneDrive"),
                   ADMINBAR_SANDBOX=str(self.root))
        env.update(self.extra)
        env.update(more)
        return env

    def unzip(self, parent: Path, name="AdminBar") -> Path:
        """Like Explorer's Extract All: <parent>/<name>/AdminBar/... -> returns the folder holding Install.cmd."""
        out = parent / name
        with zipfile.ZipFile(ZIP) as z:
            for info in z.infolist():
                target = Path(lp(out)) / info.filename
                if info.is_dir():
                    continue
                os.makedirs(target.parent, exist_ok=True)
                target.write_bytes(z.read(info))
        return out / "AdminBar"

    def run(self, cmd_file: Path, cmd=None, timeout=240, **env):
        """Double-click: Explorer runs  cmd.exe /c ""<file>" "  ."""
        exe = cmd or os.path.join(SYSROOT, "System32", "cmd.exe")
        line = f'"{exe}" /d /c ""{cmd_file}" "'
        r = subprocess.run(line, env=self.env(**env), capture_output=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, cwd=str(self.temp))
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
        return r.returncode, out

    def registry(self):
        try:
            return json.loads((self.root / "registry.json").read_text(encoding="utf-8"))
        except OSError:
            return {}

    def installed_ok(self):
        """The installed copy is complete and starts (self-check with its own Python)."""
        py = self.dest / "runtime" / "python.exe"
        assert py.exists(), "no runtime installed"
        left = [p.name for p in self.dest.iterdir() if p.name.startswith((".stage", ".old-"))]
        assert not left, left
        r = subprocess.run([str(py), "-I", "-B", str(self.dest / "app" / "Admin Bar.pyw"), "--selfcheck"],
                           env=self.env(), capture_output=True, text=True, timeout=90)
        assert r.returncode == 0 and "selfcheck ok" in r.stdout, r.stdout + r.stderr
        reg = self.registry()
        assert "--background" in reg[RUN]["AdminBar"] and str(self.dest) in reg[RUN]["AdminBar"]
        assert reg[UNINST]["DisplayName"] == "Admin Bar"
        assert (self.root / "Start Menu" / "Programs" / "Admin Bar.lnk").exists()
        return reg


def setup_mutex_name(box):
    tag = hashlib.sha1(str(box.root).lower().encode()).hexdigest()[:10]
    return "Local\\AdminBar.Setup." + tag, "Local\\AdminBar.Instance." + tag, "Local\\AdminBar.Quit." + tag


@pytest.fixture
def box(tmp_path):
    b = Box(tmp_path / "sb")
    yield b
    subprocess.run(["icacls", str(tmp_path), "/reset", "/T", "/C", "/Q"], capture_output=True)
    subprocess.run(["attrib", "-R", str(tmp_path / "*"), "/S", "/D"], capture_output=True)


# ------------------------------------------------------------------ the normal path
def test_fresh_install_from_a_folder_with_awkward_characters(box):
    src = box.unzip(box.home / "Downloads" / "Mo & Co (x) 100% !wow ^ مجلد")
    rc, out = box.run(src / "Install.cmd")
    assert rc == 0, out
    assert "DONE - Admin Bar is installed and running." in out
    reg = box.installed_ok()
    assert (box.root / "Desktop" / "Admin Bar.lnk").exists()
    for name in SHORTCUTS:
        assert (src / f"{name}.lnk").exists(), name
    assert Path(reg[UNINST]["SourceFolder"]).resolve() == src.resolve()
    assert reg["_launched"] == [str(box.dest / "runtime" / "pythonw.exe")]
    assert not list(src.rglob("__pycache__")), "setup wrote .pyc files into the unpacked folder"


def test_reinstall_replaces_files_and_keeps_settings(box):
    src = box.unzip(box.home / "Downloads")
    assert box.run(src / "Install.cmd")[0] == 0
    box.data.mkdir(parents=True, exist_ok=True)
    (box.data / "settings.json").write_text('{"open_hotkey": "Alt+E"}')
    (box.data / "punishments.log").write_text("old log")
    (box.dest / "app" / "stray.txt").write_text("left over from an old version")
    rc, out = box.run(src / "Install.cmd")
    assert rc == 0, out
    box.installed_ok()
    assert not (box.dest / "app" / "stray.txt").exists()
    assert (box.data / "settings.json").read_text() == '{"open_hotkey": "Alt+E"}'
    assert (box.data / "punishments.log").read_text() == "old log"


def test_upgrade_over_the_1_0_layout(box):
    (box.dest / "adminbar").mkdir(parents=True)
    (box.dest / "adminbar" / "ui.py").write_text("old")
    (box.dest / "Admin Bar.pyw").write_text("old")
    (box.dest / "Uninstall.cmd").write_text("old")
    src = box.unzip(box.home / "Desktop")
    rc, out = box.run(src / "Install.cmd")
    assert rc == 0, out
    box.installed_ok()
    assert sorted(p.name for p in box.dest.iterdir()) == ["app", "runtime"]


def test_running_admin_bar_is_closed_first(box):
    src = box.unzip(box.home / "Downloads")
    _setup, mutex, quit_ev = setup_mutex_name(box)
    ready = box.root / "ready.txt"
    helper = box.root / "fake_app.py"
    helper.write_text(
        "import ctypes, sys\n"
        "k = ctypes.windll.kernel32\n"
        "k.CreateMutexW.restype = k.CreateEventW.restype = ctypes.c_void_p\n"
        "m = k.CreateMutexW(None, False, sys.argv[1]); e = k.CreateEventW(None, True, False, sys.argv[2])\n"
        "open(sys.argv[3], 'w').write('ready')\n"
        "k.WaitForSingleObject(ctypes.c_void_p(e), 60000)\n")
    p = subprocess.Popen([sys.executable, "-I", str(helper), mutex, quit_ev, str(ready)])
    try:
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.05)
        rc, out = box.run(src / "Install.cmd")
        assert rc == 0, out
        assert "closed the running Admin Bar" in out
        assert p.wait(10) == 0
    finally:
        p.kill()
    box.installed_ok()


def test_an_old_admin_bar_that_does_not_quit_is_ended(box):
    src = box.unzip(box.home / "Downloads")
    assert box.run(src / "Install.cmd")[0] == 0
    stuck = subprocess.Popen([str(box.dest / "runtime" / "python.exe"), "-I", "-c", "import time; time.sleep(120)"],
                             env=box.env())
    try:
        time.sleep(0.5)
        rc, out = box.run(src / "Install.cmd")
        assert rc == 0, out
        assert stuck.wait(10) is not None
    finally:
        stuck.kill()
    box.installed_ok()


def test_install_from_the_source_folder(box):
    """What failed on the developer PC: Install.cmd run in adminbar/ itself (runtime in .build/runtime)."""
    src = box.root / "projects" / "adminbar"
    src.mkdir(parents=True)
    shutil.copy2(ADMINBAR / "Install.cmd", src / "Install.cmd")
    shutil.copytree(ADMINBAR / "app", src / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ADMINBAR / ".build" / "runtime", src / ".build" / "runtime",
                    ignore=shutil.ignore_patterns("__pycache__"))
    rc, out = box.run(src / "Install.cmd")
    assert rc == 0, out
    assert "source folder" in out
    box.installed_ok()


# ------------------------------------------------------------------ opened from inside the zip
def test_opened_inside_the_zip_with_explorer(tmp_path):
    box = Box(tmp_path / "sb", home_name="home (x) & y")
    shutil.copy2(ZIP, box.home / "Downloads" / "AdminBar (1).zip")
    temp_copy = box.temp / "Temp1_AdminBar (1).zip" / "AdminBar"
    temp_copy.mkdir(parents=True)
    with zipfile.ZipFile(ZIP) as z:
        (temp_copy / "Install.cmd").write_bytes(z.read("AdminBar/Install.cmd"))
    rc, out = box.run(temp_copy / "Install.cmd")
    assert rc == 0, out
    assert "Unpacked to" in out
    unpacked = box.home / "Downloads" / "AdminBar"
    assert (unpacked / "runtime" / "python.exe").exists()
    reg = box.installed_ok()
    assert (unpacked / "Start Admin Bar.lnk").exists()
    assert Path(reg[UNINST]["SourceFolder"]).resolve() == unpacked.resolve()


def test_opened_inside_the_zip_with_winrar_skips_other_zips(box):
    shutil.copy2(ZIP, box.home / "Desktop" / "AdminBar.zip")
    with zipfile.ZipFile(box.home / "Downloads" / "AdminBar-other.zip", "w") as z:  # newer, but not this one
        z.writestr("AdminBar/Install.cmd", "@echo off\r\necho wrong zip\r\n")
    os.utime(box.home / "Desktop" / "AdminBar.zip", (time.time() - 3600,) * 2)
    temp_copy = box.temp / "Rar$EXa1234.5678" / "AdminBar"
    temp_copy.mkdir(parents=True)
    with zipfile.ZipFile(ZIP) as z:
        (temp_copy / "Install.cmd").write_bytes(z.read("AdminBar/Install.cmd"))
    rc, out = box.run(temp_copy / "Install.cmd")
    assert rc == 0, out
    assert "wrong zip" not in out
    assert (box.home / "Desktop" / "AdminBar" / "Install.cmd").exists()
    box.installed_ok()


def test_opened_inside_the_zip_by_a_32_bit_cmd(box):
    shutil.copy2(ZIP, box.home / "Downloads" / "AdminBar.zip")
    temp_copy = box.temp / "Temp2_AdminBar.zip" / "AdminBar"
    temp_copy.mkdir(parents=True)
    with zipfile.ZipFile(ZIP) as z:
        (temp_copy / "Install.cmd").write_bytes(z.read("AdminBar/Install.cmd"))
    rc, out = box.run(temp_copy / "Install.cmd", cmd=os.path.join(SYSROOT, "SysWOW64", "cmd.exe"))
    assert rc == 0, out
    box.installed_ok()


def test_opened_inside_the_zip_and_no_zip_found(box):
    temp_copy = box.temp / "Temp1_AdminBar.zip" / "AdminBar"
    temp_copy.mkdir(parents=True)
    with zipfile.ZipFile(ZIP) as z:
        (temp_copy / "Install.cmd").write_bytes(z.read("AdminBar/Install.cmd"))
    rc, out = box.run(temp_copy / "Install.cmd")
    assert rc == 1
    assert 'Right-click AdminBar.zip and choose "Extract All..."' in out
    assert not box.dest.exists()


# ------------------------------------------------------------------ broken or odd folders
def test_missing_python_dll(box):
    src = box.unzip(box.home / "Downloads")
    (src / "runtime" / "python313.dll").unlink()
    rc, out = box.run(src / "Install.cmd")
    assert rc == 1 and "Some Admin Bar files are missing" in out
    assert not box.dest.exists()


def test_missing_app_folder(box):
    src = box.unzip(box.home / "Downloads")
    shutil.rmtree(src / "app")
    rc, out = box.run(src / "Install.cmd")
    assert rc == 1 and "Some Admin Bar files are missing" in out


def test_missing_tk_file_is_caught_before_anything_changes(box):
    src = box.unzip(box.home / "Downloads")
    assert box.run(src / "Install.cmd")[0] == 0
    before = (box.dest / "app" / "adminbar" / "data" / "rules.json").read_bytes()
    (src / "runtime" / "tcl" / "tk8.6" / "tk.tcl").unlink()
    rc, out = box.run(src / "Install.cmd")
    assert rc == 1 and "1 file(s) missing" in out and "tk.tcl" in out, out
    assert (box.dest / "app" / "adminbar" / "data" / "rules.json").read_bytes() == before
    box.installed_ok()  # the old install still works


def test_damaged_file_is_caught_before_anything_changes(box):
    src = box.unzip(box.home / "Downloads")
    assert box.run(src / "Install.cmd")[0] == 0
    f = src / "app" / "adminbar" / "rules.py"
    f.write_bytes(f.read_bytes().replace(b"def ", b"dex ", 1))
    rc, out = box.run(src / "Install.cmd")
    assert rc == 1 and "damaged" in out and "rules.py" in out, out
    box.installed_ok()


def test_second_setup_window_waits_for_the_first(box):
    src = box.unzip(box.home / "Downloads")
    k32 = ctypes.WinDLL("kernel32")
    k32.CreateMutexW.restype = ctypes.c_void_p
    h = k32.CreateMutexW(None, False, setup_mutex_name(box)[0])
    try:
        rc, out = box.run(src / "Install.cmd")
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))
    assert rc == 1 and "Setup is already running in another window" in out
    assert not (box.dest / "app").exists()


def test_read_only_folder_and_files(box):
    src = box.unzip(box.home / "Downloads")
    subprocess.run(["attrib", "+R", str(src / "*"), "/S"], check=True, capture_output=True)
    user = os.environ.get("USERNAME")
    subprocess.run(["icacls", str(src), "/deny", f"{user}:(WD,AD)"], check=True, capture_output=True)
    try:
        rc, out = box.run(src / "Install.cmd")
        assert rc == 0, out
        assert "no shortcuts in this folder (Windows did not let setup write there)" in out
        assert box.run(src / "Install.cmd")[0] == 0  # read-only flags did not come along
    finally:
        subprocess.run(["icacls", str(src), "/remove:d", user], capture_output=True)
    reg = box.installed_ok()
    assert "SourceFolder" not in reg[UNINST]


def test_controlled_folder_access_is_never_triggered(box):
    src = box.unzip(box.home / "Documents")
    rc, out = box.run(src / "Install.cmd", ADMINBAR_SANDBOX_CFA="1")
    assert rc == 0, out
    assert "no Desktop shortcut: Windows Security protects that folder" in out
    assert "no shortcuts in this folder: Windows Security protects it" in out
    assert not (box.root / "Desktop" / "Admin Bar.lnk").exists()
    assert not list(src.glob("*.lnk"))
    box.installed_ok()


def test_run_as_a_different_administrator(box):
    src = box.unzip(box.home / "Downloads")
    rc, out = box.run(src / "Install.cmd", ADMINBAR_SANDBOX_ELEVATION="admin-other-user")
    assert rc == 1 and "different Windows user" in out
    assert not (box.dest / "app").exists()


def test_run_as_administrator_same_user(box):
    src = box.unzip(box.home / "Downloads")
    rc, out = box.run(src / "Install.cmd", ADMINBAR_SANDBOX_ELEVATION="admin-same-user")
    assert rc == 0 and "started as administrator: not needed, but fine" in out
    box.installed_ok()


def test_old_windows(box):
    src = box.unzip(box.home / "Downloads")
    rc, out = box.run(src / "Install.cmd", ADMINBAR_SANDBOX_BUILD="9600")
    assert rc == 1 and "needs 64-bit Windows 10 or Windows 11" in out


def test_install_cmd_inside_the_installed_folder(box):
    src = box.unzip(box.home / "Downloads")
    assert box.run(src / "Install.cmd")[0] == 0
    shutil.copy2(src / "Install.cmd", box.dest / "Install.cmd")
    rc, out = box.run(box.dest / "Install.cmd")
    assert rc == 1 and "This is the installed copy" in out
    (box.dest / "Install.cmd").unlink()
    box.installed_ok()


def test_very_long_folder_path(box):
    parent = box.home / "Downloads"
    room = 250 - len(str(parent)) - len("\\AdminBar\\AdminBar\\Install.cmd")
    deep = parent
    while room > 12:
        deep = deep / ("long folder " + "x" * min(40, room - 13))
        room = 250 - len(str(deep)) - len("\\AdminBar\\AdminBar\\Install.cmd")
    src = box.unzip(deep)
    with zipfile.ZipFile(ZIP) as z:
        longest = max(len(str(src.parent)) + 1 + len(n) for n in z.namelist())
    assert longest > 260  # the deepest files are past Windows' old path limit
    rc, out = box.run(src / "Install.cmd")
    assert rc == 0, out
    assert "short copy" in out
    assert not (box.local / "AdminBar-setup-runtime").exists()  # the short copy is gone again
    box.installed_ok()


def test_install_cmd_is_plain_ascii_with_crlf():
    data = (ADMINBAR / "Install.cmd").read_bytes()
    assert data.count(b"\n") == data.count(b"\r\n")
    assert all(32 <= b <= 126 or b in (10, 13) for b in data)


def test_network_share(box):
    unc = "\\\\localhost\\" + str(box.root)[0] + "$" + str(box.root)[2:]
    if not os.path.isdir(unc):
        pytest.skip("no admin share on this PC")
    box.unzip(box.home / "Downloads")
    src = unc + "\\home\\Downloads\\AdminBar\\AdminBar"
    rc, out = box.run(Path(src) / "Install.cmd")
    assert rc == 0, out
    box.installed_ok()


# ------------------------------------------------------------------ uninstall
def _wait_gone(path, seconds=60):
    for _ in range(seconds * 5):
        if not path.exists():
            return True
        time.sleep(0.2)
    return False


def test_uninstall_keeps_settings_then_purge_removes_them(box):
    src = box.unzip(box.home / "Downloads")
    assert box.run(src / "Install.cmd")[0] == 0
    box.data.mkdir(parents=True, exist_ok=True)
    (box.data / "settings.json").write_text("{}")
    py = box.dest / "runtime" / "python.exe"
    inst = box.dest / "app" / "adminbar" / "install.py"
    subprocess.run([str(py), "-I", str(inst), "uninstall", "--quiet"], env=box.env(), check=True, timeout=60)
    assert _wait_gone(box.dest), "install folder was not removed"
    reg = box.registry()
    assert UNINST not in reg and "AdminBar" not in reg.get(RUN, {})
    assert not (box.root / "Start Menu" / "Programs" / "Admin Bar.lnk").exists()
    assert not (box.root / "Desktop" / "Admin Bar.lnk").exists()
    assert not list(src.glob("*.lnk"))
    assert (box.data / "settings.json").exists()

    assert box.run(src / "Install.cmd")[0] == 0
    subprocess.run([str(py), "-I", str(inst), "uninstall", "--quiet", "--purge"], env=box.env(), check=True,
                   timeout=60)
    assert _wait_gone(box.dest) and _wait_gone(box.data)
