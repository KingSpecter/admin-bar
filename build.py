"""Developer build for Admin Bar.

    python -I adminbar/build.py data      rules.json for the app, from ../data/rules.json (the rulebooks)
    python -I adminbar/build.py icon      icon.ico + logo PNGs from assets/logo.png
    python -I adminbar/build.py runtime   the private Python the app ships with (.build/runtime)
    python -I adminbar/build.py zip       dist/AdminBar.zip (what you hand out)
    python -I adminbar/build.py actions   print every rule's parsed commands (review after a rules change)
    python -I adminbar/build.py all       data + icon + runtime + zip

The runtime is the official Windows "embeddable" Python from python.org, unmodified (PSF-signed), plus the
PSF-signed Tk files of the same Python version taken from a local install (the embeddable package leaves
tkinter out). Nothing is compiled or packed into an .exe, so there is nothing for an antivirus to guess at.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import sys
import urllib.request
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP = HERE / "app"
DATA = APP / "adminbar" / "data"
SRC = ROOT / "data" / "rules.json"          # the rulebook project this folder sits in, when there is one
DIST = (ROOT if SRC.exists() else HERE) / "dist"
CACHE = HERE / ".cache"
BUILD = HERE / ".build"
LOGO = HERE / "assets" / "logo.png"

PY_VERSION = "3.13.2"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
EMBED_SHA256 = "1e803610b140cbf69dfa2ceaaeb39651bef75a239c381289e827c30862a27b93"
LOCAL_PY = Path(r"C:\Python313")          # a normal install of the same version (for the Tk files)
TK_FILES = ["_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll"]

KEEP = ("id", "book", "num", "name", "short", "section", "text", "punishment", "exception", "note",
        "keywords", "see_also", "context")


# ----------------------------------------------------------------------------- rules data

def build_data() -> None:
    if not SRC.exists():
        print("rules data: no ../data/rules.json next to this folder, keeping app/adminbar/data/rules.json")
        return
    src = json.loads(SRC.read_text(encoding="utf-8"))
    books = [{"prefix": b["prefix"], "title": b["title"]} for b in src["books"]]
    rules = []
    for r in src["rules"]:
        row = {k: r[k] for k in KEEP if k in r and r[k] not in ("", [], {}, None)}
        row["section"] = row.get("section", "").replace(" (heading added)", "").replace(" (numbering added)", "")
        rules.append(row)
    out = DATA / "rules.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"books": books, "rules": rules}, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}: {len(rules)} rules")


# ----------------------------------------------------------------------------- PNG in / out (pure Python)

def read_png(path: Path):
    """8-bit RGB / RGBA / grey / palette PNG -> (w, h, list of float RGBA rows, premultiplied)."""
    b = path.read_bytes()
    assert b[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, plte, trns = 8, b"", None, None
    w = h = depth = ctype = interlace = 0
    while pos < len(b):
        n, kind = struct.unpack(">I4s", b[pos:pos + 8])
        data = b[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            w, h, depth, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", data)
        elif kind == b"IDAT":
            idat += data
        elif kind == b"PLTE":
            plte = data
        elif kind == b"tRNS":
            trns = data
        elif kind == b"IEND":
            break
    if depth != 8 or interlace:
        raise SystemExit("logo.png must be an 8-bit, non-interlaced PNG")
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = w * chans
    prev = bytearray(stride)
    rows = []
    i = 0
    for _y in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - chans] if x >= chans else 0
            up = prev[x]
            c = prev[x - chans] if x >= chans else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + up) & 255
            elif f == 3:
                line[x] = (line[x] + ((a + up) >> 1)) & 255
            elif f == 4:
                p = a + up - c
                pa, pb, pc = abs(p - a), abs(p - up), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else (up if pb <= pc else c))) & 255
        prev = line
        px = []
        for x in range(w):
            s = line[x * chans:(x + 1) * chans]
            if ctype == 6:
                r, g, bl, al = s
            elif ctype == 2:
                r, g, bl, al = s[0], s[1], s[2], 255
            elif ctype == 0:
                r = g = bl = s[0]
                al = 255
            elif ctype == 4:
                r = g = bl = s[0]
                al = s[1]
            else:
                k = s[0]
                r, g, bl = plte[3 * k:3 * k + 3]
                al = trns[k] if trns and k < len(trns) else 255
            fa = al / 255.0
            px.append((r * fa, g * fa, bl * fa, al))
        rows.append(px)
    return w, h, rows


def _resample_1d(line, out_n):
    """Area-average a list of RGBA tuples to out_n samples."""
    n = len(line)
    scale = n / out_n
    out = []
    for i in range(out_n):
        a, b = i * scale, (i + 1) * scale
        acc = [0.0, 0.0, 0.0, 0.0]
        j = int(a)
        while j < b and j < n:
            wgt = min(j + 1, b) - max(j, a)
            p = line[j]
            for c in range(4):
                acc[c] += p[c] * wgt
            j += 1
        out.append(tuple(v / scale for v in acc))
    return out


def resample(rows, size):
    """Square crop (centre) + area-average resize."""
    h, w = len(rows), len(rows[0])
    side = min(w, h)
    x0, y0 = (w - side) // 2, (h - side) // 2
    cropped = [r[x0:x0 + side] for r in rows[y0:y0 + side]]
    horiz = [_resample_1d(r, size) for r in cropped]
    cols = [_resample_1d([horiz[y][x] for y in range(side)], size) for x in range(size)]
    return [[cols[x][y] for x in range(size)] for y in range(size)]


def rounded(img, radius_frac=0.2):
    """Soft rounded corners, like an app tile."""
    n = len(img)
    r = n * radius_frac
    out = []
    for y in range(n):
        row = []
        for x in range(n):
            px, py = x + 0.5, y + 0.5
            qx, qy = abs(px - n / 2) - (n / 2 - r), abs(py - n / 2) - (n / 2 - r)
            d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
            cov = min(1.0, max(0.0, 0.5 - d))
            p = img[y][x]
            row.append((p[0] * cov, p[1] * cov, p[2] * cov, p[3] * cov))
        out.append(row)
    return out


def to_rgba_bytes(img) -> bytes:
    out = bytearray()
    for row in img:
        for r, g, b, a in row:
            if a > 0:
                f = 255.0 / a
                r, g, b = r * f, g * f, b * f
            out += bytes((min(255, int(r + 0.5)), min(255, int(g + 0.5)), min(255, int(b + 0.5)),
                          min(255, int(a + 0.5))))
    return bytes(out)


def png_bytes(size: int, rgba: bytes) -> bytes:
    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def bmp_icon_entry(size: int, rgba: bytes) -> bytes:
    """A classic 32-bit icon image (what LoadImage likes best for the small sizes)."""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = bytearray()
    for y in range(size - 1, -1, -1):  # bottom-up, BGRA
        for x in range(size):
            i = (y * size + x) * 4
            r, g, b, a = rgba[i:i + 4]
            pixels += bytes((b, g, r, a))
    mask_row = ((size + 31) // 32) * 4
    return header + bytes(pixels) + bytes(mask_row * size)


ICON_SIZES = [256, 64, 48, 40, 32, 24, 20, 16]
LOGO_SIZES = [36, 44, 54, 72]


def build_icon() -> None:
    print("reading", LOGO.relative_to(ROOT), "...")
    _w, _h, rows = read_png(LOGO)
    entries, images = [], []
    for n in ICON_SIZES:
        rgba = to_rgba_bytes(rounded(resample(rows, n)))
        data = png_bytes(n, rgba) if n >= 256 else bmp_icon_entry(n, rgba)
        images.append(data)
        entries.append(n)
        if n in (64,):
            pass
    for n in LOGO_SIZES:
        rgba = to_rgba_bytes(rounded(resample(rows, n), 0.24))
        (DATA / f"logo_{n}.png").write_bytes(png_bytes(n, rgba))
    head = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    table = b""
    for n, img in zip(entries, images):
        table += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
    (DATA / "icon.ico").write_bytes(head + table + b"".join(images))
    print(f"wrote icon.ico ({', '.join(map(str, ICON_SIZES))}) and logo_*.png ({', '.join(map(str, LOGO_SIZES))})")


# ----------------------------------------------------------------------------- runtime

def build_runtime() -> Path:
    CACHE.mkdir(exist_ok=True)
    embed_zip = CACHE / f"python-{PY_VERSION}-embed-amd64.zip"
    if not embed_zip.exists():
        print("downloading", EMBED_URL)
        urllib.request.urlretrieve(EMBED_URL, embed_zip)
    digest = hashlib.sha256(embed_zip.read_bytes()).hexdigest()
    if digest != EMBED_SHA256:
        embed_zip.unlink()
        raise SystemExit(f"embeddable Python checksum mismatch ({digest}) - deleted, run again")
    rt = BUILD / "runtime"
    if rt.exists():
        shutil.rmtree(rt)
    rt.mkdir(parents=True)
    with zipfile.ZipFile(embed_zip) as z:
        z.extractall(rt)
    dlls = LOCAL_PY / "DLLs"
    ver = (LOCAL_PY / "python313.dll").exists() and sys.version.split()[0]
    if ver != PY_VERSION:
        raise SystemExit(f"the Tk files must come from Python {PY_VERSION} (this build runs {ver})")
    for f in TK_FILES:
        shutil.copy2(dlls / f, rt / f)
    shutil.copytree(LOCAL_PY / "Lib" / "tkinter", rt / "tkinter",
                    ignore=shutil.ignore_patterns("__pycache__", "test", "tests"))
    tcl = rt / "tcl"
    for d in ("tcl8", "tcl8.6", "tk8.6"):
        shutil.copytree(LOCAL_PY / "tcl" / d, tcl / d, ignore=shutil.ignore_patterns("demos", "images"))
    size = sum(f.stat().st_size for f in rt.rglob("*") if f.is_file()) // (1024 * 1024)
    print(f"runtime ready: {rt.relative_to(ROOT)} ({size} MB, Python {PY_VERSION} + Tk)")
    return rt


# ----------------------------------------------------------------------------- zip

def package_files(rt: Path):
    """(path inside the zip under AdminBar/, source file) for app/ and runtime/, sorted."""
    out = []
    for base, prefix in ((APP, "app/"), (rt, "runtime/")):
        for p in sorted(base.rglob("*")):
            if p.is_dir() or "__pycache__" in p.parts or p.suffix in (".pyc", ".log") or p.name == "package.sha256":
                continue
            out.append((prefix + p.relative_to(base).as_posix(), p))
    return out


def build_zip() -> Path:
    rt = BUILD / "runtime"
    if not (rt / "pythonw.exe").exists():
        raise SystemExit("no runtime yet: python -I adminbar/build.py runtime")
    cmd = (HERE / "Install.cmd").read_bytes()
    # cmd.exe misreads labels in files with bare LF line ends; anything but plain ASCII can break on other code pages
    if any(b > 126 or (b < 32 and b not in (10, 13)) for b in cmd) or cmd.count(b"\n") != cmd.count(b"\r\n"):
        raise SystemExit("Install.cmd must be plain ASCII with CRLF line ends")
    DIST.mkdir(exist_ok=True)
    out = DIST / "AdminBar.zip"
    tmp = out.with_suffix(".tmp")
    files = package_files(rt)
    # the installer checks every file against this list before it replaces anything
    lines = ["# sha256 size path - checked by install.py"]
    for rel, p in files:
        data = p.read_bytes()
        lines.append(f"{hashlib.sha256(data).hexdigest()} {len(data)} {rel}")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(HERE / "Install.cmd", "AdminBar/Install.cmd")
        for rel, p in files:
            z.write(p, "AdminBar/" + rel)
        z.writestr("AdminBar/app/adminbar/data/package.sha256", "\n".join(lines) + "\n")
    with zipfile.ZipFile(tmp) as z:  # read it back: every entry must decompress
        bad = z.testzip()
        if bad:
            raise SystemExit(f"zip check failed at {bad}")
    tmp.replace(out)
    print(f"wrote {out}: {len(files) + 2} files, {out.stat().st_size // 1024} KB")
    return out


def show_actions() -> None:
    sys.path.insert(0, str(APP))
    from adminbar.rules import RuleBook
    for r in RuleBook.load().rules:
        acts = " | ".join(f"{a.kind}:{a.label}" + (f" ({a.note})" if a.note else "") for a in r.actions)
        extra = f"   + {'; '.join(r.extras)}" if r.extras else ""
        print(f"{r.id:<10} {r.punishment[:60]:<60} -> {acts}{extra}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("data", "all"):
        build_data()
    if cmd in ("icon", "all"):
        build_icon()
    if cmd in ("runtime", "all"):
        build_runtime()
    if cmd == "actions":
        show_actions()
    if cmd in ("zip", "all"):
        build_zip()
