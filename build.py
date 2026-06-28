#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py - ImgMacro 를 단독 실행 exe + 번들 OCR 형태로 패키징한다.

산출물 (release/ImgMacro/):
  Editor.exe     노드 그래프 GUI 에디터 (일반 사용자용 메인)
  ImgMacro.exe   매크로 실행 엔진 (에디터가 호출)
  Snip.exe       화면 영역 캡처 도구 (에디터가 호출)
  tesseract/     한글 OCR 엔진(내장)
  config.yaml    (구버전) 선형 설정 예시
  templates/     캡처 이미지 저장 폴더

전제: 번들용 Tesseract 가 _build/tesseract/ 에 있어야 한다(README '재빌드' 참고).
실행:  python build.py
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(ROOT, "_build")
DIST = os.path.join(BUILD, "dist")
TESS_SRC = os.path.join(BUILD, "tesseract")
REL = os.path.join(ROOT, "release", "ImgMacro")
RTHOOK = os.path.join(ROOT, "rthook_silence.py")

COMMON = [
    "--noconfirm", "--clean", "--onefile",
    "--paths", ROOT,
    "--runtime-hook", RTHOOK,
    "--exclude-module", "matplotlib",
    "--exclude-module", "scipy",
    "--exclude-module", "pandas",
    "--distpath", DIST,
    "--workpath", os.path.join(BUILD, "work"),
    "--specpath", BUILD,
]

# (스크립트, exe이름, 추가옵션)
TARGETS = [
    ("macro.py", "ImgMacro", ["--console", "--collect-all", "numpy",
                              "--collect-submodules", "pynput", "--hidden-import", "winutil"]),
    ("snip.py", "Snip", ["--windowed", "--collect-all", "numpy"]),
    ("editor.py", "Editor", ["--windowed", "--collect-all", "dearpygui",
                             "--hidden-import", "winutil"]),
]


def pyinstaller(script, name, extra):
    cmd = [sys.executable, "-m", "PyInstaller", "--name", name] + COMMON + extra + [script]
    print(">>", name)
    subprocess.check_call(cmd)


def main():
    if not os.path.exists(os.path.join(TESS_SRC, "tesseract.exe")):
        print("오류: 번들용 Tesseract 가 없습니다 ->", TESS_SRC)
        print("README.md 의 '재빌드' 절을 참고해 _build/tesseract 를 준비하세요.")
        sys.exit(1)

    for script, name, extra in TARGETS:
        pyinstaller(script, name, extra)

    # 배포 폴더 조립
    if os.path.exists(os.path.dirname(REL)):
        shutil.rmtree(os.path.dirname(REL))
    os.makedirs(os.path.join(REL, "templates"), exist_ok=True)
    for exe in ("Editor.exe", "ImgMacro.exe", "Snip.exe"):
        shutil.copy2(os.path.join(DIST, exe), REL)
    if os.path.exists(os.path.join(ROOT, "README_release.txt")):
        shutil.copy2(os.path.join(ROOT, "README_release.txt"),
                     os.path.join(REL, "사용법.txt"))
    shutil.copytree(TESS_SRC, os.path.join(REL, "tesseract"))
    open(os.path.join(REL, "templates", "캡처이미지_저장폴더.txt"),
         "w", encoding="utf-8").close()

    # zip 패키징
    zip_path = os.path.join(ROOT, "release", "ImgMacro.zip")
    base = os.path.dirname(REL)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(REL):
            for fn in files:
                full = os.path.join(root, fn)
                z.write(full, os.path.relpath(full, base))

    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(REL) for f in fs)
    print("\n완료!")
    print("  폴더:", REL, f"({size/1e6:.0f} MB)")
    print("  zip :", zip_path, f"({os.path.getsize(zip_path)/1e6:.0f} MB)")


if __name__ == "__main__":
    main()
