"""포터블 exe 묶기: .venv\\Scripts\\python.exe scripts\\build_exe.py

1. assets/xetical.ico를 앱 아이콘(widgets.app_icon)으로 만든다(16~256px PNG를 담은 ICO).
2. PyInstaller로 한 폴더(onedir) 묶음을 dist/Xetical/에 만든다. 한 파일(onefile)은 실행할 때마다
   임시 폴더에 풀어서 느리고 백신이 의심하는 경우가 많아서 쓰지 않는다.
3. portable.txt를 넣어 데이터가 exe 옆 data/에 저장되게 하고, dist/Xetical-<버전>-portable.zip으로 묶는다.
4. Inno Setup 6이 있으면 installer/xetical.iss로 dist/Xetical-<버전>-setup.exe도 만든다.

준비: .venv\\Scripts\\python.exe -m pip install -r requirements-build.txt
"""
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def make_icon(path):
    """ICO 파일 안에 크기별 PNG를 넣는다(Windows Vista 이상이 읽는 형식)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtWidgets import QApplication

    from xetical.widgets import app_icon
    app = QApplication.instance() or QApplication([])  # QPixmap을 그리려면 있어야 한다
    icon = app_icon(show_number=False)
    images = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.WriteOnly)
        icon.pixmap(size, size).save(buf, "PNG")
        images.append((size, bytes(data)))
    offset = 6 + 16 * len(images)
    head = struct.pack("<HHH", 0, 1, len(images))
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size  # 256은 0으로 적는다
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blobs += png
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(head + entries + blobs)
    del app


# 묶음에 딸려 오지만 Xetical은 쓰지 않는 것(지우고 나서 exe를 띄워 확인했다)
UNUSED = [
    # 화상 키보드 플러그인이 Qt Quick/QML을 통째로 끌고 온다
    "PySide6/plugins/platforminputcontexts", "PySide6/Qt6VirtualKeyboard.dll", "PySide6/Qt6Quick.dll",
    "PySide6/Qt6Qml.dll", "PySide6/Qt6QmlMeta.dll", "PySide6/Qt6QmlModels.dll", "PySide6/Qt6QmlWorkerScript.dll",
    "PySide6/Qt6Network.dll", "PySide6/Qt6OpenGL.dll",
    "PySide6/opengl32sw.dll",                         # 그래픽 카드가 없을 때 쓰는 소프트웨어 OpenGL
    "PySide6/plugins/imageformats/qpdf.dll", "PySide6/Qt6Pdf.dll",
    "PySide6/plugins/platforms/qdirect2d.dll", "PySide6/plugins/platforms/qminimal.dll",
    "PySide6/plugins/platforms/qoffscreen.dll",
    "PySide6/translations",                           # Qt 번역(QTranslator를 쓰지 않는다)
]
KEEP_DOCS = {"calendar.v3.json", "tasks.v1.json", "drive.v3.json"}  # 드라이브: 트래커 동기화


def prune(internal):
    for rel in UNUSED:
        p = internal / rel
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    # googleapiclient 훅이 구글 API 설명 600개(111MB)를 모두 넣는다 → 쓰는 것만 남긴다
    for f in (internal / "googleapiclient" / "discovery_cache" / "documents").glob("*.json"):
        if f.name not in KEEP_DOCS:
            f.unlink()


def main():
    from xetical.config import APP_VERSION
    ico = ROOT / "assets" / "xetical.ico"
    make_icon(ico)

    import googleapiclient
    docs = Path(googleapiclient.__file__).parent / "discovery_cache" / "documents"
    dest = "googleapiclient/discovery_cache/documents"
    sep = os.pathsep  # PyInstaller --add-data는 Windows에서 ';'
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
           "--name", "Xetical", "--icon", str(ico),
           "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
           "--add-data", f"{ROOT / 'README.ko.md'}{sep}.",
           # 구글 API 설명 파일은 600개(111MB)가 있지만 Calendar·Tasks·Drive만 쓴다
           "--add-data", f"{docs / 'calendar.v3.json'}{sep}{dest}",
           "--add-data", f"{docs / 'tasks.v1.json'}{sep}{dest}",
           "--add-data", f"{docs / 'drive.v3.json'}{sep}{dest}",
           "--exclude-module", "PySide6.QtNetwork", "--exclude-module", "PySide6.QtQml",
           "--exclude-module", "PySide6.QtQuick", "--exclude-module", "PySide6.QtWebEngineCore",
           str(ROOT / "main.pyw")]
    subprocess.run(cmd, cwd=ROOT, check=True)

    app_dir = ROOT / "dist" / "Xetical"
    prune(app_dir / "_internal")
    (app_dir / "portable.txt").write_text(
        "이 파일이 있으면 Xetical은 설정·로그인 정보를 이 폴더의 data\\ 안에 저장합니다(포터블).\r\n"
        "지우면 %APPDATA%\\Xetical에 저장합니다.\r\n", encoding="utf-8")
    shutil.copy(ROOT / "README.ko.md", app_dir / "README.ko.md")

    zip_path = ROOT / "dist" / f"Xetical-{APP_VERSION}-portable.zip"
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(app_dir.rglob("*")):
            if f.is_file():
                z.write(f, Path("Xetical") / f.relative_to(app_dir))
    size = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file())
    print(f"\n완료: {zip_path}  (zip {zip_path.stat().st_size / 1e6:.1f}MB, 풀면 {size / 1e6:.1f}MB)")

    iscc = find_iscc()
    if iscc is None:
        print("설치 프로그램: Inno Setup 6(ISCC.exe)이 없어 건너뜀 (winget install JRSoftware.InnoSetup)")
        return
    subprocess.run([str(iscc), "/Q", f"/DMyAppVersion={APP_VERSION}", str(ROOT / "installer" / "xetical.iss")],
                   cwd=ROOT, check=True)
    setup = ROOT / "dist" / f"Xetical-{APP_VERSION}-setup.exe"
    print(f"완료: {setup}  ({setup.stat().st_size / 1e6:.1f}MB)")


def find_iscc():
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("ProgramFiles", "")):
        p = Path(base) / ("Programs" if base == os.environ.get("LOCALAPPDATA") else "") / "Inno Setup 6" / "ISCC.exe"
        if base and p.exists():
            return p
    return shutil.which("ISCC")


if __name__ == "__main__":
    main()
