#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
snip.py - 템플릿 이미지 캡처 도구

화면에서 드래그로 영역을 선택하면 templates/<이름>.png 로 저장한다.
이 이미지를 config.yaml 의 image 페이즈 target 으로 사용한다.

사용법:
    python snip.py start_button
    python snip.py            # 이름을 입력받음

선택한 영역의 좌표(region 으로 쓸 수 있는 [x, y, w, h])도 함께 출력한다.
"""

import ctypes
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")

# 화면 캡처/좌표를 물리 픽셀 기준으로 맞춤 (macro.py 와 동일)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import tkinter as tk
import mss
import numpy as np
import cv2


def imwrite_u(path, img):
    """한글/유니코드 경로에서도 저장되도록 imencode + tofile 사용."""
    ext = os.path.splitext(path)[1] or ".png"
    ok, buf = cv2.imencode(ext, img)
    if ok:
        buf.tofile(path)
    return bool(ok)


def select_region():
    """반투명 전체화면 오버레이에서 드래그로 영역 선택. (x, y, w, h) 반환(물리픽셀)."""
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.25)
    root.configure(bg="black")
    root.attributes("-topmost", True)
    root.config(cursor="cross")

    canvas = tk.Canvas(root, highlightthickness=0, bg="black")
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        root.winfo_screenwidth() // 2, 40,
        text="드래그하여 영역 선택  (ESC: 취소)",
        fill="white", font=("맑은 고딕", 18),
    )

    state = {"x0": 0, "y0": 0, "rect": None, "result": None}

    def on_press(e):
        state["x0"], state["y0"] = e.x_root, e.y_root
        state["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y,
                                                 outline="red", width=2)

    def on_drag(e):
        if state["rect"] is not None:
            x0 = state["x0"] - root.winfo_rootx()
            y0 = state["y0"] - root.winfo_rooty()
            canvas.coords(state["rect"], x0, y0, e.x, e.y)

    def on_release(e):
        x1, y1 = state["x0"], state["y0"]
        x2, y2 = e.x_root, e.y_root
        x, y = min(x1, x2), min(y1, y2)
        w, h = abs(x2 - x1), abs(y2 - y1)
        if w > 3 and h > 3:
            state["result"] = (x, y, w, h)
        root.destroy()

    def on_escape(_):
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", on_escape)
    root.mainloop()
    return state["result"]


def main():
    import argparse
    import json
    ap = argparse.ArgumentParser(description="템플릿 영역 캡처 도구")
    ap.add_argument("name", nargs="?", default="", help="저장 파일명(확장자 생략 가능)")
    ap.add_argument("--out-dir", default="templates", help="저장 폴더")
    ap.add_argument("--json", dest="json_out", default="",
                    help="결과를 이 JSON 파일에 기록(GUI 연동용, 콘솔 프롬프트 없음)")
    ap.add_argument("--no-save", dest="no_save", action="store_true",
                    help="이미지는 저장하지 않고 영역 좌표만 반환")
    args = ap.parse_args()

    headless = bool(args.json_out)  # GUI 가 호출한 경우: 프롬프트/print 대신 JSON 기록
    name = args.name
    if not name:
        name = input("템플릿 이름: ").strip() if not headless else \
            "capture_" + time.strftime("%H%M%S")
    if name and not name.lower().endswith(".png"):
        name += ".png"

    def finish(result):
        if headless:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
        return result

    if not headless:
        print("잠시 후 화면이 어두워지면 대상 영역을 드래그하세요...")
    region = select_region()
    if not region:
        if not headless:
            print("취소되었습니다.")
        finish({"cancelled": True})
        return

    x, y, w, h = region
    path = None
    if not args.no_save:
        with mss.MSS() as sct:
            shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
            img = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
        os.makedirs(args.out_dir, exist_ok=True)
        path = os.path.join(args.out_dir, name)
        imwrite_u(path, img)
    finish({"cancelled": False, "path": path, "region": [x, y, w, h],
            "name": name})
    if not headless:
        if path:
            print(f"저장 완료: {path}  ({w}x{h})")
        print(f"region: [{x}, {y}, {w}, {h}]")


if __name__ == "__main__":
    main()
