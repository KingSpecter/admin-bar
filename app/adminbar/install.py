"""Installs / removes Admin Bar for the current Windows user, like a normal app, without administrator rights.
Run by Install.cmd with the Python that ships inside the AdminBar folder (nothing is downloaded).

    files       %LOCALAPPDATA%\\Programs\\AdminBar\\{app, runtime}
    shortcuts   Desktop + Start menu: "Admin Bar"
                next to Install.cmd: Start / Stop / Settings / Punishment log / Uninstall
    start-up    HKCU\\...\\Run  ->  runtime\\pythonw.exe "app\\Admin Bar.pyw" --background
    uninstall   Windows Settings > Apps > Admin Bar   (or the "Uninstall Admin Bar" shortcut)

    python -I install.py install --src <the unzipped AdminBar folder> [--no-autostart] [--dry]
    pythonw -I install.py uninstall [--quiet] [--purge]

How it stays safe:
  * every file of the zip is checked against the list made at build time (package.sha256) before anything is
    replaced, so a half-unzipped or damaged folder never breaks a working install;
  * the new copy is put together next to the old one and started once in a check mode first; only then are the
    folders swapped (renames), so a failure at any step leaves the old install working;
  * files are copied as plain data: no "downloaded from the internet" marks, read-only flags or ACLs come along;
  * nothing is written into folders that Windows Security's Controlled folder access protects, so it never
    raises a "blocked" notification;
  * a setup started "as administrator" still installs for the signed-in user and starts the app un-elevated.

ADMINBAR_SANDBOX=<folder> (tests only) keeps everything inside that folder: the registry becomes a JSON file,
Desktop / Start menu are sub-folders, the app is only self-checked (not started) and the events get other names.

Exit codes: 0 done, 3 another setup is running, 7 failed. Stand-alone on purpose: it must not import the app.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP = "Admin Bar"
KEY = "AdminBar"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AdminBar"
MANIFEST = "app/adminbar/data/package.sha256"
PARTS = ("app", "runtime")
SANDBOX = os.environ.get("ADMINBAR_SANDBOX") or ""
_TAG = ("." + hashlib.sha1(SANDBOX.lower().encode()).hexdigest()[:10]) if SANDBOX else ""
QUIT_EVENT = "Local\\AdminBar.Quit" + _TAG
MUTEX = "Local\\AdminBar.Instance" + _TAG
SETUP_MUTEX = "Local\\AdminBar.Setup" + _TAG
NEED_FREE = 120 * 1024 * 1024  # bytes: new copy + old copy + slack
# the shortcuts put next to Install.cmd: (file name, arguments for "Admin Bar.pyw" or None = uninstall, tooltip)
FOLDER_SHORTCUTS = [
    ("Start Admin Bar", "", "Start Admin Bar (opens its window when it already runs)"),
    ("Stop Admin Bar", "--quit", "Stop the running Admin Bar"),
    ("Admin Bar settings", "--settings", "Open the Admin Bar window"),
    ("Punishment log", "--log", "Every punishment sent with Admin Bar"),
    ("Uninstall Admin Bar", None, "Remove Admin Bar from this PC"),
]
RUNTIME_FOR_UNINSTALL = ("python.exe", "pythonw.exe", "python3.dll", "python313.dll", "python313.zip",
                         "python313._pth", "vcruntime140.dll", "vcruntime140_1.dll", "_ctypes.pyd", "libffi-8.dll")
CREATE_NO_WINDOW, DETACHED_PROCESS, NEW_GROUP = 0x08000000, 0x00000008, 0x00000200


class SetupError(Exception):
    """A problem the person installing can fix; the text says how."""


def say(text=""):
    try:
        print("  " + text if text else "", flush=True)
    except (OSError, ValueError, AttributeError):
        pass  # pythonw has no console


def step(n, total, text):
    say(f"[{n}/{total}] {text}")


# ------------------------------------------------------------------ places
def install_dir():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Programs" / KEY


def data_dir():
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / KEY


def folder(csidl):
    buf = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
        return Path(buf.value)
    return None


def desktop():
    if SANDBOX:
        return Path(SANDBOX) / "Desktop"
    return folder(0x10)  # CSIDL_DESKTOPDIRECTORY (follows OneDrive redirection)


def start_menu():
    if SANDBOX:
        return Path(SANDBOX) / "Start Menu" / "Programs"
    return folder(0x02)  # CSIDL_PROGRAMS


def long_path(p):
    """A path Windows accepts even past 260 characters."""
    s = os.path.abspath(str(p))
    if len(s) < 240 or s.startswith("\\\\?\\"):
        return s
    return "\\\\?\\UNC\\" + s[2:] if s.startswith("\\\\") else "\\\\?\\" + s


def inside(child, parent):
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


# ------------------------------------------------------------------ message boxes
def ask(text, title=APP):
    """Yes / No box (works without a console)."""
    if SANDBOX:
        return os.environ.get("ADMINBAR_SANDBOX_ANSWER", "no") == "yes"
    return ctypes.windll.user32.MessageBoxW(None, text, title, 0x4 | 0x20 | 0x40000) == 6  # YESNO|QUESTION|TOPMOST


def tell(text, title=APP):
    if SANDBOX:
        _sandbox_note("message", text)
        return
    ctypes.windll.user32.MessageBoxW(None, text, title, 0x40 | 0x40000)


# ------------------------------------------------------------------ registry (a JSON file in the sandbox)
def _sandbox_file():
    return Path(SANDBOX) / "registry.json"


def _sandbox_load():
    try:
        return json.loads(_sandbox_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _sandbox_save(data):
    _sandbox_file().parent.mkdir(parents=True, exist_ok=True)
    _sandbox_file().write_text(json.dumps(data, indent=1), encoding="utf-8")


def _sandbox_note(kind, value):
    data = _sandbox_load()
    data.setdefault("_" + kind, []).append(value)
    _sandbox_save(data)


def reg_set(path, values):
    """values: {name: str | int | None}; None deletes the value."""
    if SANDBOX:
        data = _sandbox_load()
        k = data.setdefault(path, {})
        for name, value in values.items():
            if value is None:
                k.pop(name, None)
            else:
                k[name] = value
        _sandbox_save(data)
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
        for name, value in values.items():
            if value is None:
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
            elif isinstance(value, int):
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)
            else:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def reg_get(path, name):
    if SANDBOX:
        return _sandbox_load().get(path, {}).get(name)
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def reg_delete_key(path):
    if SANDBOX:
        data = _sandbox_load()
        data.pop(path, None)
        _sandbox_save(data)
        return
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        pass


def set_run(value):
    reg_set(RUN_KEY, {KEY: value or None})


def set_uninstall_entry(dest, version, icon, uninstall_cmd, source):
    size = 0
    for f in dest.rglob("*"):
        try:
            size += f.stat().st_size if f.is_file() else 0
        except OSError:
            pass
    reg_set(UNINSTALL_KEY, {
        "DisplayName": APP, "DisplayVersion": version, "Publisher": APP, "InstallLocation": str(dest),
        "DisplayIcon": str(icon), "UninstallString": uninstall_cmd, "QuietUninstallString": uninstall_cmd + " --quiet",
        "InstallDate": time.strftime("%Y%m%d"), "SourceFolder": str(source) if source else None,
        "NoModify": 1, "NoRepair": 1, "EstimatedSize": size // 1024})


# ------------------------------------------------------------------ Windows Security: Controlled folder access
def controlled_folders():
    """The folders Controlled folder access guards right now ([] when it is off). Writing there from an app
    Windows does not know raises a "blocked" notification, so setup never tries."""
    if SANDBOX:
        if os.environ.get("ADMINBAR_SANDBOX_CFA") != "1":
            return []
        return [Path(SANDBOX) / "Desktop", Path(SANDBOX) / "home" / "Documents"]
    import winreg
    base = r"SOFTWARE\Microsoft\Windows Defender\Windows Defender Exploit Guard\Controlled Folder Access"
    on = None
    for path in (r"SOFTWARE\Policies\Microsoft\Windows Defender\Windows Defender Exploit Guard\Controlled Folder Access",
                 base):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
                on = int(winreg.QueryValueEx(k, "EnableControlledFolderAccess")[0])
                break
        except (OSError, ValueError):
            continue
    if on not in (1, 3, 4):  # 0 off, 2 audit only (no notification); 1/3/4 block
        return []
    out = [folder(c) for c in (0x10, 0x05, 0x27, 0x0E, 0x0D, 0x06)]  # desktop, documents, pictures, videos, music, fav
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + r"\ProtectedFolders") as k:
            i = 0
            while True:
                try:
                    out.append(Path(winreg.EnumValue(k, i)[0]))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    public = os.environ.get("PUBLIC")
    if public:
        out += [Path(public) / n for n in ("Desktop", "Documents", "Pictures", "Videos", "Music")]
    return [p for p in out if p]


def guarded(path, guards):
    return any(inside(path, g) for g in guards)


# ------------------------------------------------------------------ who is running setup
class _TokenElevation(ctypes.Structure):
    _fields_ = [("elevated", wt.DWORD)]


def _token(process):
    """(elevated, user SID text) of a process handle, or None."""
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.OpenProcessToken.argtypes = (wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE))
    adv.GetTokenInformation.argtypes = (wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD))
    adv.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wt.LPWSTR))
    tok = wt.HANDLE()
    if not adv.OpenProcessToken(process, 0x0008, ctypes.byref(tok)):  # TOKEN_QUERY
        return None
    try:
        el = _TokenElevation()
        n = wt.DWORD()
        if not adv.GetTokenInformation(tok, 20, ctypes.byref(el), ctypes.sizeof(el), ctypes.byref(n)):
            return None
        buf = ctypes.create_string_buffer(256)
        if not adv.GetTokenInformation(tok, 1, buf, 256, ctypes.byref(n)):  # TokenUser
            return None
        sid_ptr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wt.LPWSTR()
        if not adv.ConvertSidToStringSidW(sid_ptr, ctypes.byref(text)):
            return None
        sid = text.value
        ctypes.windll.kernel32.LocalFree(text)
        return bool(el.elevated), sid
    finally:
        ctypes.windll.kernel32.CloseHandle(tok)


def who_runs_setup():
    """'normal', 'admin-same-user' (Run as administrator: fine) or 'admin-other-user' (the files would go to the
    administrator's profile, not the person signed in)."""
    if SANDBOX and os.environ.get("ADMINBAR_SANDBOX_ELEVATION"):
        return os.environ["ADMINBAR_SANDBOX_ELEVATION"]
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wt.HANDLE
    me = _token(k32.GetCurrentProcess())
    if not me or not me[0]:
        return "normal"
    u32 = ctypes.windll.user32
    u32.GetShellWindow.restype = wt.HWND
    shell = u32.GetShellWindow()
    pid = wt.DWORD()
    if not shell or not u32.GetWindowThreadProcessId(shell, ctypes.byref(pid)):
        return "admin-same-user"
    k32.OpenProcess.restype = wt.HANDLE
    h = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return "admin-same-user"
    try:
        other = _token(h)
    finally:
        k32.CloseHandle(h)
    if other and other[1] != me[1]:
        return "admin-other-user"
    return "admin-same-user" if not (other and other[0]) else "normal"  # UAC off: everything is elevated


# ------------------------------------------------------------------ the running app
def _k32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenEventW.restype = wt.HANDLE
    k32.OpenEventW.argtypes = (wt.DWORD, wt.BOOL, wt.LPCWSTR)
    k32.OpenMutexW.restype = wt.HANDLE
    k32.OpenMutexW.argtypes = (wt.DWORD, wt.BOOL, wt.LPCWSTR)
    k32.CreateMutexW.restype = wt.HANDLE
    k32.CreateMutexW.argtypes = (ctypes.c_void_p, wt.BOOL, wt.LPCWSTR)
    k32.SetEvent.argtypes = (wt.HANDLE,)
    k32.CloseHandle.argtypes = (wt.HANDLE,)
    k32.OpenProcess.restype = wt.HANDLE
    k32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
    k32.QueryFullProcessImageNameW.argtypes = (wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
    k32.TerminateProcess.argtypes = (wt.HANDLE, wt.UINT)
    k32.WaitForSingleObject.argtypes = (wt.HANDLE, wt.DWORD)
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = (wt.DWORD, wt.DWORD)
    return k32


def app_running():
    k32 = _k32()
    m = k32.OpenMutexW(0x00100000, False, MUTEX)  # SYNCHRONIZE
    if m:
        k32.CloseHandle(m)
        return True
    return False


def stop_running(timeout=8.0):
    """Ask a running Admin Bar (any version) to quit and wait for it. True when one was running."""
    k32 = _k32()
    h = k32.OpenEventW(0x0002, False, QUIT_EVENT)  # EVENT_MODIFY_STATE
    if not h:
        return False
    k32.SetEvent(h)
    k32.CloseHandle(h)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not app_running():
            time.sleep(0.6)  # let the process finish exiting so its files are free
            return True
        time.sleep(0.2)
    return True


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


def processes_from(where):
    """PIDs of the processes whose program file lies inside `where` (an Admin Bar that ignored the quit request)."""
    k32 = _k32()
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == wt.HANDLE(-1).value:
        return []
    found, me, root = [], os.getpid(), os.path.normcase(os.path.abspath(str(where))) + os.sep
    try:
        e = _ProcessEntry()
        e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            pid = e.th32ProcessID
            if pid and pid != me and e.szExeFile.lower() in ("python.exe", "pythonw.exe"):
                h = k32.OpenProcess(0x1000, False, pid)
                if h:
                    buf = ctypes.create_unicode_buffer(32768)
                    n = wt.DWORD(32768)
                    if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                        if os.path.normcase(buf.value).startswith(root):
                            found.append(pid)
                    k32.CloseHandle(h)
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return found


def end_processes_from(where):
    k32 = _k32()
    n = 0
    for pid in processes_from(where):
        h = k32.OpenProcess(0x0001 | 0x00100000, False, pid)  # TERMINATE | SYNCHRONIZE
        if h:
            if k32.TerminateProcess(h, 0):
                k32.WaitForSingleObject(h, 5000)
                n += 1
            k32.CloseHandle(h)
    if n:
        time.sleep(0.4)
    return n


# ------------------------------------------------------------------ files
class GUID(ctypes.Structure):
    _fields_ = [("a", wt.DWORD), ("b", wt.WORD), ("c", wt.WORD), ("d", ctypes.c_ubyte * 8)]

    def __init__(self, text):
        super().__init__()
        ctypes.oledll.ole32.CLSIDFromString(text, ctypes.byref(self))


def _com(ptr, index, restype, *argtypes):
    """Method `index` of a COM object's vtable."""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])


def shortcut(lnk, target, args, workdir, icon, icon_index, description):
    """Create a .lnk the way installers do: CoCreateInstance(ShellLink) + IPersistFile.Save."""
    ole32 = ctypes.oledll.ole32
    try:
        ole32.CoInitialize(None)
    except OSError:
        pass  # already initialised in another mode: still usable
    link = ctypes.c_void_p()
    try:
        ole32.CoCreateInstance(ctypes.byref(GUID("{00021401-0000-0000-C000-000000000046}")), None, 1,
                               ctypes.byref(GUID("{000214F9-0000-0000-C000-000000000046}")), ctypes.byref(link))
        HR, S = ctypes.HRESULT, wt.LPCWSTR
        _com(link, 20, HR, S)(link, str(target))            # SetPath
        _com(link, 11, HR, S)(link, args)                   # SetArguments
        _com(link, 9, HR, S)(link, str(workdir))            # SetWorkingDirectory
        _com(link, 7, HR, S)(link, description)             # SetDescription
        _com(link, 17, HR, S, ctypes.c_int)(link, str(icon), icon_index)  # SetIconLocation
        pf = ctypes.c_void_p()
        _com(link, 0, HR, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
            link, ctypes.byref(GUID("{0000010b-0000-0000-C000-000000000046}")), ctypes.byref(pf))  # IPersistFile
        try:
            _com(pf, 6, HR, S, wt.BOOL)(pf, str(lnk), True)  # Save
        finally:
            _com(pf, 2, wt.ULONG)(pf)                        # Release
        return Path(lnk).exists()
    except OSError:
        return False
    finally:
        if link:
            _com(link, 2, wt.ULONG)(link)
        ole32.CoUninitialize()


def _writable(path, _func, _exc):
    """rmtree helper: clear a read-only flag, then retry."""
    try:
        os.chmod(path, stat.S_IWRITE)
        _func(path)
    except OSError:
        pass


def remove_tree(path, tries=25):
    path = Path(path)
    for _ in range(tries):
        try:
            if path.exists():
                if sys.version_info >= (3, 12):
                    shutil.rmtree(long_path(path), onexc=_writable)
                else:
                    shutil.rmtree(long_path(path), onerror=lambda f, p, e: _writable(p, f, e))
            if not path.exists():
                return True
        except OSError:
            pass
        time.sleep(0.3)
    return not path.exists()


def remove_file(path):
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        try:
            os.chmod(path, stat.S_IWRITE)
            Path(path).unlink()
        except OSError:
            pass


def read_version(app_src):
    """The version of the copy being installed (read, not imported: the installer never runs the new app)."""
    import re
    try:
        with open(long_path(app_src / "adminbar" / "__init__.py"), encoding="utf-8") as f:
            m = re.search(r'VERSION\s*=\s*"([^"]+)"', f.read())
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def read_manifest(app_src):
    """[(relative path, size, sha256)] from the list made at build time, or None (source folder, no list)."""
    f = long_path(app_src / "adminbar" / "data" / "package.sha256")
    if not os.path.isfile(f):
        return None
    out = []
    with open(f, encoding="utf-8") as fh:
        text = fh.read()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            digest, size, rel = line.split(" ", 2)
            if rel.startswith("/") or ".." in rel.split("/") or rel.split("/")[0] not in PARTS:
                raise SetupError(f"The file list inside the AdminBar folder is damaged ({rel}).")
            out.append((rel, int(size), digest))
    return out


def _copy(src, dst, tries=6):
    """Copy the data only (no internet mark, no read-only flag, no ACL); returns sha256. Retries while an
    antivirus scan holds the file for a moment."""
    for attempt in range(tries):
        try:
            os.makedirs(long_path(dst.parent), exist_ok=True)
            h = hashlib.sha256()
            with open(long_path(src), "rb") as fi, open(long_path(dst), "wb") as fo:
                while True:
                    block = fi.read(1 << 20)
                    if not block:
                        break
                    h.update(block)
                    fo.write(block)
            return h.hexdigest()
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.5)
    return ""


def stage_files(src, rt_src, manifest, stage):
    """Copy the new version into `stage` (stage/app, stage/runtime). Checks every file against the list."""
    remove_tree(stage, tries=4)
    files = 0
    if manifest is not None:
        missing, damaged = [], []
        for rel, size, digest in manifest:
            part, _, rest = rel.partition("/")
            s = (src / "app" if part == "app" else rt_src) / Path(rest)
            if not os.path.isfile(long_path(s)):
                missing.append(rel)
                continue
            if os.path.getsize(long_path(s)) != size or _copy(s, stage / part / Path(rest)) != digest:
                damaged.append(rel)
            files += 1
        if missing or damaged:
            bad = missing + damaged
            raise SetupError(f"{len(missing)} file(s) missing and {len(damaged)} damaged in the AdminBar folder"
                             f" (first: {bad[0]}).\n  The zip was not fully unpacked, or the download is broken."
                             "\n  Delete this folder, unpack AdminBar.zip again (right-click > Extract All)"
                             " and run Install.cmd again.")
        return files
    # a source folder without the build list: copy what is there
    for part, base in (("app", src / "app"), ("runtime", rt_src)):
        for root, dirs, names in os.walk(long_path(base)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in names:
                if name.endswith((".pyc", ".log")):
                    continue
                p = Path(root) / name
                _copy(p, stage / part / Path(os.path.relpath(p, long_path(base))))
                files += 1
    return files


def self_check(stage):
    """Start the staged copy once in check mode (no window, nothing saved). '' when fine, else the error."""
    py = stage / "runtime" / "python.exe"
    try:
        r = subprocess.run([str(py), "-I", "-B", str(stage / "app" / "Admin Bar.pyw"), "--selfcheck"],
                           cwd=str(stage), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=90, creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "the check did not finish in 90 seconds"
    except OSError as exc:
        return f"could not start it ({exc.strerror or exc})"
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0 and out.endswith("selfcheck ok"):
        return ""
    return out.splitlines()[-1] if out else f"exit code {r.returncode}"


def swap_in(stage, dest):
    """Move stage/{app,runtime} into dest. The old folders are renamed aside first, so a failure puts them back."""
    stamp = str(int(time.time() * 1000))
    moved, placed = [], []
    try:
        for part in PARTS:
            old = dest / part
            if old.exists():
                aside = dest / f".old-{part}-{stamp}"
                for attempt in range(20):
                    try:
                        old.rename(aside)
                        break
                    except OSError:
                        if attempt == 19:
                            raise
                        time.sleep(0.3)
                moved.append((aside, old))
        for part in PARTS:
            (stage / part).rename(dest / part)
            placed.append(part)
    except OSError:
        for part in placed:
            try:
                (dest / part).rename(stage / part)
            except OSError:
                pass
        for aside, old in moved:
            try:
                aside.rename(old)
            except OSError:
                pass
        raise
    for aside, _old in moved:
        remove_tree(aside, tries=3)


def cleanup_leftovers(dest):
    """Old copies a previous setup could not delete, the 1.0 / 1.1 layout, a failed stage."""
    if not dest.exists():
        return
    for p in dest.iterdir():
        if p.name.startswith((".old-", ".stage")) or p.name == "adminbar":
            remove_tree(p, tries=2)
    for name in ("Admin Bar.pyw", "Uninstall.cmd"):
        remove_file(dest / name)
    remove_tree(Path(tempfile.gettempdir()) / "AdminBar-uninstall", tries=2)  # left by an earlier uninstall


def launch(pyw, script, dest, elevated, links):
    """Start the installed app. From an elevated setup it goes through Explorer so it runs as the normal user
    (an elevated Admin Bar could not type into a normal game window)."""
    if SANDBOX:
        _sandbox_note("launched", str(pyw))
        return True
    try:
        if elevated:
            lnk = next((p for p in links if p.exists()), None)
            if not lnk:
                return False
            subprocess.Popen([os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe"), str(lnk)],
                             close_fds=True)
        else:
            subprocess.Popen([str(pyw), str(script)], cwd=str(dest), close_fds=True,
                             creationflags=DETACHED_PROCESS | NEW_GROUP, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def find_runtime(src):
    """The runtime to install: the one running this script when it is the bundled one, else the folder's own."""
    here = Path(sys.executable).resolve().parent
    if (here / "python313._pth").exists() and (here / "pythonw.exe").exists():
        return here
    for rt in (src / "runtime", src / ".build" / "runtime"):
        if os.path.isfile(long_path(rt / "pythonw.exe")):
            return rt
    return None


def setup_lock():
    k32 = _k32()
    h = k32.CreateMutexW(None, False, SETUP_MUTEX)
    if h and ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        k32.CloseHandle(h)
        return None
    return h or True


# ------------------------------------------------------------------ install
def install(src, autostart=True, dry=False):
    src = Path(os.path.abspath(src))
    dest = install_dir()
    app_src = src / "app"
    rt_src = find_runtime(src)
    if not os.path.isfile(long_path(app_src / "Admin Bar.pyw")) or rt_src is None:
        say(f"Files are missing in {src}.")
        say("Unpack the whole AdminBar.zip (right-click > Extract All) and run Install.cmd from the new folder.")
        return 7
    if inside(src, dest) or inside(dest, src):
        say("This is the installed copy of Admin Bar, not the unpacked zip.")
        say("Run Install.cmd from the folder you unpacked AdminBar.zip into.")
        return 7
    who = who_runs_setup()
    if who == "admin-other-user":
        say("Setup was started as a different Windows user (an administrator account).")
        say("Close this window and double-click Install.cmd normally - Admin Bar needs no administrator rights.")
        return 7
    lock = setup_lock()
    if lock is None:
        say("Setup is already running in another window. Let that one finish.")
        return 3
    try:
        VERSION = read_version(app_src)
        try:
            manifest = read_manifest(app_src)
        except (OSError, ValueError) as exc:
            raise SetupError(f"The file list inside the AdminBar folder can't be read ({exc}).") from exc
        say(f"Admin Bar {VERSION} for {os.environ.get('USERNAME') or 'this user'}"
            + (" (started as administrator: not needed, but fine)" if who == "admin-same-user" else ""))
        say(f"from  {src}")
        say(f"to    {dest}")
        say("")
        total = 5
        step(1, total, "Checking the files ...")
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SetupError(f"Can't create {dest} ({exc.strerror or exc}).") from exc
        try:
            free = shutil.disk_usage(str(dest)).free
        except OSError:
            free = NEED_FREE
        if free < NEED_FREE:
            raise SetupError(f"Not enough free disk space on {dest.drive or dest.anchor} "
                             f"({free // (1024 * 1024)} MB free, about 120 MB needed).")
        cleanup_leftovers(dest)
        stage = dest / f".stage-{os.getpid()}"
        try:
            files = stage_files(src, rt_src, manifest, stage)
        except SetupError:
            remove_tree(stage, tries=4)
            raise
        except OSError as exc:
            remove_tree(stage, tries=4)
            if getattr(exc, "winerror", 0) == 112 or exc.errno == 28:
                raise SetupError("The disk is full. Free some space and run Install.cmd again.") from exc
            raise SetupError(f"Could not copy a file: {exc}") from exc
        say(f"      {files} files ok" + ("" if manifest is not None else " (source folder, not a packed zip)"))
        step(2, total, "Testing the new copy ...")
        problem = self_check(stage)
        if problem:
            remove_tree(stage, tries=4)
            raise SetupError(f"The new copy does not start: {problem}\n  Nothing was changed. "
                             "Unpack AdminBar.zip again and retry; if it keeps happening, send this text.")
        say("      ok")
        if dry:  # files only: no shortcuts, registry or launch (used to test the installer)
            for part in PARTS:
                remove_tree(dest / part)
                (stage / part).rename(dest / part)
            remove_tree(stage, tries=2)
            say("Dry run: files copied, nothing else changed.")
            return 0
        step(3, total, "Installing ...")
        if stop_running():
            say("      closed the running Admin Bar")
        if end_processes_from(dest):
            say("      an old Admin Bar did not close by itself: ended it")
        try:
            swap_in(stage, dest)
        except OSError as exc:
            remove_tree(stage, tries=4)
            raise SetupError("Admin Bar's old files are still in use. Quit Admin Bar (right-click its icon at the "
                             f"bottom right > Quit), then run Install.cmd again. ({exc.strerror or exc})") from exc
        remove_tree(stage, tries=2)
        cleanup_leftovers(dest)

        step(4, total, "Shortcuts and start with Windows ...")
        pyw = dest / "runtime" / "pythonw.exe"
        script = dest / "app" / "Admin Bar.pyw"
        installer = dest / "app" / "adminbar" / "install.py"
        icon = dest / "app" / "adminbar" / "data" / "icon.ico"
        run = f'"{script}"'
        uninstall_args = f'-I "{installer}" uninstall'
        guards = controlled_folders()
        notes, made, links = [], [], []
        for label, where in (("Desktop", desktop()), ("Start menu", start_menu())):
            if not where:
                continue
            if guarded(where, guards):
                notes.append(f"no {label} shortcut: Windows Security protects that folder (Controlled folder access)")
                continue
            try:
                where.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            lnk = where / f"{APP}.lnk"
            if shortcut(lnk, pyw, run, dest, icon, 0, "Rule search and punish bar"):
                made.append(label)
                links.append(lnk)
            else:
                notes.append(f"could not make the {label} shortcut")
        folder_ok = 0
        source_for_registry = None
        if inside(src, tempfile.gettempdir()):
            notes.append("no shortcuts in the setup folder (it is a temporary folder)")
        elif guarded(src, guards):
            notes.append("no shortcuts in this folder: Windows Security protects it (Controlled folder access)")
        else:
            for name, args, tip in FOLDER_SHORTCUTS:
                a = uninstall_args if args is None else (f"{run} {args}".strip())
                if shortcut(src / f"{name}.lnk", pyw, a, dest, icon, 0, tip):
                    folder_ok += 1
            if folder_ok:
                source_for_registry = src
            else:
                notes.append("no shortcuts in this folder (Windows did not let setup write there)")
        try:
            set_uninstall_entry(dest, VERSION, icon, f'"{pyw}" {uninstall_args}', source_for_registry)
        except OSError as exc:
            notes.append(f"could not add the Windows Settings > Apps entry ({exc})")
        try:
            set_run(f'"{pyw}" "{script}" --background' if autostart else None)
        except OSError as exc:
            notes.append(f"could not change the start-with-Windows setting ({exc})")
        for n in notes:
            say(f"      note: {n}")

        step(5, total, "Starting Admin Bar ...")
        started = launch(pyw, script, dest, who == "admin-same-user", links)
        say("      ok" if started else "      start it from the Start menu: Admin Bar")
        say("")
        say("DONE - Admin Bar is installed" + (" and running." if started else "."))
        say("")
        say("  Open the bar    Alt+Q in game (Alt+W hides it)")
        say("  Settings        left-click the Admin Bar icon at the bottom right of the taskbar")
        say("                  (Windows may tuck new icons under the ^ arrow: drag it out once)")
        say("  Close it        right-click that icon > Quit" + (", or 'Stop Admin Bar' here" if folder_ok else ""))
        say("  Start it again  'Admin Bar' on the Desktop / Start menu" + (", or 'Start Admin Bar' here"
                                                                        if folder_ok else ""))
        say("  Remove it       Windows Settings > Apps > Admin Bar" + (", or 'Uninstall Admin Bar' here"
                                                                     if folder_ok else ""))
        say("  It starts by itself when Windows starts." if autostart else "  It does not start with Windows.")
        say("  Settings and the punishment log are kept when you install a newer version.")
        return 0
    finally:
        if lock not in (None, True):
            _k32().CloseHandle(lock)


# ------------------------------------------------------------------ uninstall
def uninstall(quiet=False, stage2=False, purge=False):
    os.chdir(tempfile.gettempdir())  # never sit inside the folder being removed
    dest = install_dir()
    if not stage2:
        if not quiet and not ask(f"Remove {APP} from this PC?"):
            return 0
        purge = purge or ((not quiet) and ask("Also delete your Admin Bar settings and punishment log?"))
        here = Path(sys.executable).resolve()
        if inside(here, dest):
            # Windows cannot delete the Python that is running: carry on from a small copy in TEMP
            tmp = Path(tempfile.gettempdir()) / "AdminBar-uninstall"
            remove_tree(tmp, tries=2)
            tmp.mkdir(parents=True, exist_ok=True)
            for f in RUNTIME_FOR_UNINSTALL:
                if (here.parent / f).exists():
                    _copy(here.parent / f, tmp / f)
            _copy(Path(__file__).resolve(), tmp / "install.py")
            args = [str(tmp / "pythonw.exe"), "-I", "-B", str(tmp / "install.py"), "uninstall", "--stage2"]
            if quiet:
                args.append("--quiet")
            if purge:
                args.append("--purge")
            subprocess.Popen(args, cwd=str(tmp), close_fds=True)
            return 0
    stop_running()
    end_processes_from(dest)
    source = reg_get(UNINSTALL_KEY, "SourceFolder")
    set_run(None)
    reg_delete_key(UNINSTALL_KEY)
    for where in (desktop(), start_menu()):
        if where:
            remove_file(where / f"{APP}.lnk")
    if source:
        for name, _a, _t in FOLDER_SHORTCUTS:
            remove_file(long_path(Path(source) / f"{name}.lnk"))
    if purge:
        remove_tree(data_dir())
    gone = remove_tree(dest)
    if not quiet:
        if gone:
            tell(f"{APP} was removed" + (" with your settings." if purge else ". Your settings were kept."))
        else:
            tell(f"{APP} was stopped and unregistered, but some files are still in use:\n{dest}\n"
                 "Delete that folder after restarting the PC.")
    return 0


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install")
    i.add_argument("--src", required=True)
    i.add_argument("--no-autostart", action="store_true")
    i.add_argument("--dry", action="store_true")
    u = sub.add_parser("uninstall")
    u.add_argument("--quiet", action="store_true")
    u.add_argument("--stage2", action="store_true")
    u.add_argument("--purge", action="store_true")
    a = p.parse_args()
    if sys.version_info < (3, 9) or sys.platform != "win32":
        say("Admin Bar needs 64-bit Windows 10 or 11.")
        return 7
    try:
        if a.cmd == "install":
            return install(a.src, not a.no_autostart, a.dry)
        return uninstall(a.quiet, a.stage2, a.purge)
    except SetupError as exc:
        say("")
        say(f"PROBLEM: {exc}")
        return 7
    except Exception as exc:  # noqa: BLE001 - shown to the person installing
        say("")
        say(f"Setup failed: {exc!r}")
        if a.cmd == "uninstall" and not a.quiet:
            tell(f"Uninstall failed: {exc!r}")
        return 7


if __name__ == "__main__":
    sys.exit(main())
