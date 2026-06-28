#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ImgMacro - 윈도우용 이미지/텍스트 인식 자동 클릭 매크로

동작 개요
  config.yaml 에 정의한 페이즈를 위에서부터 순서대로 실행한다.
  각 페이즈:  대상 인식  ->  클릭  ->  대상이 사라졌는지 검증  ->  다음 페이즈
  action: end 페이즈는 대상을 인식하면 매크로를 종료한다.

대상 인식 방식 (페이즈별로 선택)
  - image : 템플릿 이미지(png)를 화면에서 찾음. 유사도(0~1) 임계값으로 판정.
  - text  : Tesseract OCR 로 화면의 한글/영문 텍스트를 읽어 대상 문구와 비교.

비상 정지
  - 마우스를 화면 좌상단(0,0) 끝으로 빠르게 이동  (pyautogui FAILSAFE)
  - 또는 설정한 단축키(stop_key, 기본 f12) 누르기
  - 콘솔에서 Ctrl+C
"""

import argparse
import ctypes
import json
import logging
import os
import random
import sys
import threading
import time
import warnings
from dataclasses import dataclass, field
from difflib import SequenceMatcher

warnings.filterwarnings("ignore")  # pkg_resources 등 라이브러리의 비핵심 경고 숨김

import cv2
import numpy as np
import yaml

# ----------------------------------------------------------------------------
# DPI 인식 설정: 디스플레이 배율(125%/150% 등)이 적용돼 있어도 화면 캡처(mss)와
# 클릭 좌표(pyautogui)가 동일한 '물리 픽셀' 기준으로 동작하도록 맞춘다.
# pyautogui / mss import 보다 먼저 호출해야 한다.
# ----------------------------------------------------------------------------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import mss          # noqa: E402
import pyautogui    # noqa: E402

pyautogui.FAILSAFE = True   # 마우스를 좌상단 끝으로 옮기면 즉시 중단
pyautogui.PAUSE = 0.0       # 자체 지연은 우리가 직접 제어

log = logging.getLogger("imgmacro")


def app_base():
    """실행 기준 디렉터리. PyInstaller exe면 exe가 있는 폴더, 아니면 스크립트 폴더."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def is_frozen():
    return getattr(sys, "frozen", False)


# OpenCV 는 Windows 에서 한글/유니코드 경로의 imread/imwrite 가 실패하므로
# numpy fromfile/tofile 로 우회한다. (예: C:\사용자\..., templates\확인.png)
def imread_u(path, flags=cv2.IMREAD_COLOR):
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, flags)
    except Exception:
        return None


def imwrite_u(path, img):
    try:
        ext = os.path.splitext(path)[1] or ".png"
        ok, buf = cv2.imencode(ext, img)
        if ok:
            buf.tofile(path)
        return bool(ok)
    except Exception:
        return False


# ============================================================================
# 설정 데이터 구조
# ============================================================================
@dataclass
class Settings:
    similarity: float = 0.95         # 이미지 매칭 기본 임계값 (0~1, 0.95 = 95%)
    multiscale: bool = True          # 배율이 다른 PC 대응: 템플릿을 여러 크기로 매칭
    image_scales: list = field(default_factory=lambda: [
        1.0, 1.25, 1.5, 1.75, 2.0, 0.8, 0.67, 0.5])  # 멀티스케일 배율 목록
    ocr_confidence: float = 0.80     # 텍스트 매칭 기본 임계값 (유사도 0~1)
    ocr_lang: str = "kor+eng"        # Tesseract 언어 (한글+영문)
    ocr_scale: float = 2.0           # OCR 정확도 향상을 위한 캡처 확대 배율
    ocr_psm: object = 6              # 페이지 분할 모드. 6=균일 블록(권장), 11=흩어진 텍스트.
                                     # 리스트로 폴백 지정 가능: [6, 11]
    search_interval: float = 0.4     # 인식 재시도 간격(초)
    find_timeout: float = 30.0       # 대상을 찾을 때까지 최대 대기(초)
    disappear_timeout: float = 10.0  # 클릭 후 대상이 사라질 때까지 최대 대기(초)
    post_click_delay: float = 0.5    # 클릭 직후 검증 시작 전 대기(초)
    max_click_retries: int = 3       # 사라지지 않을 때 재클릭 횟수
    move_duration: float = 0.15      # 마우스 이동 시간(초). 0이면 즉시 이동
    monitor: int = 1                 # 캡처 대상 모니터 (1=주모니터, 0=전체 가상화면)
    capture_mode: str = "monitor"    # monitor(전체화면) | window(특정 창)
    target_exe: str = ""             # window 모드: 대상 프로세스 exe (예: MabinogiMobile.exe)
    target_title: str = ""           # window 모드: 대상 창 제목(부분일치, 선택)
    activate_window: bool = True     # 실행 시 대상 창을 앞으로 가져오기
    stop_key: str = "f12"            # 비상 정지 단축키
    tesseract_cmd: str = ""          # tesseract.exe 경로(비우면 자동 탐색)
    on_fail: str = "abort"           # 페이즈 실패 시: abort(중단) | skip(건너뜀)


@dataclass
class Phase:
    name: str
    type: str = "image"             # image | text
    target: str = ""                # image면 png 경로, text면 찾을 문구
    action: str = "click"           # click | end
    region: list = None             # [x, y, w, h] (해당 모니터 좌상단 기준) 또는 None=전체
    similarity: float = None        # 이미지 임계값 개별 지정
    ocr_confidence: float = None    # 텍스트 임계값 개별 지정
    verify_disappear: bool = True   # 클릭 후 사라짐 검증 여부
    click_on_end: bool = False      # end 페이즈에서도 클릭할지
    click_offset: tuple = (0, 0)    # 중심에서 클릭 위치 보정(px)
    find_timeout: float = None      # 개별 타임아웃
    max_click_retries: int = None   # 개별 재클릭 횟수(None이면 settings 값)
    click_enabled: bool = True      # False면 '감지만'(클릭/사라짐검증 안 함, 분기용)
    scan_interval: float = None     # 이 노드의 검사 간격(초). None/0이면 settings 값
    click_random: int = 0           # 클릭 시 ±N px 랜덤 오프셋
    template: object = field(default=None, repr=False)  # 로드된 템플릿 이미지(런타임)


class AbortException(Exception):
    """사용자 비상 정지 요청"""


# ============================================================================
# 비상 정지 리스너 (전역 단축키)
# ============================================================================
_stop_event = threading.Event()


def _start_stop_listener(stop_key: str):
    try:
        from pynput import keyboard
    except Exception:
        log.warning("pynput 미설치 - 단축키 정지 비활성화 (마우스 좌상단/Ctrl+C 사용 가능)")
        return None

    def to_key(name):
        name = (name or "").strip().lower()
        if hasattr(keyboard.Key, name):
            return getattr(keyboard.Key, name)
        if len(name) == 1:
            return keyboard.KeyCode.from_char(name)
        return None

    target = to_key(stop_key)

    def on_press(key):
        try:
            if key == target or (hasattr(key, "char") and key.char and
                                 len(stop_key) == 1 and key.char == stop_key):
                log.warning("정지 단축키 감지 -> 중단합니다.")
                _stop_event.set()
        except Exception:
            pass

    listener = keyboard.Listener(on_press=on_press)
    listener.daemon = True
    listener.start()
    return listener


def check_abort():
    if _stop_event.is_set():
        raise AbortException()


# ============================================================================
# 화면 캡처
# ============================================================================
class Screen:
    """모니터 또는 특정 창(클라이언트 영역)을 BGR 이미지로 캡처한다.
    창 모드는 매 grab 마다 위치를 다시 조회해 창이 움직여도 따라간다."""

    def __init__(self, monitor_index=1, window=None, activate=False):
        self._sct = mss.MSS()
        self.window = window  # {"exe","title"} 또는 None
        self.hwnd = None
        if window:
            import winutil
            self.hwnd = winutil.find_window(window.get("exe"), window.get("title"))
            if not self.hwnd:
                raise RuntimeError(
                    "대상 창을 찾을 수 없습니다 (exe=%s, title=%s). 창이 열려 있는지 확인하세요."
                    % (window.get("exe"), window.get("title")))
            if activate:
                winutil.activate(self.hwnd)
                time.sleep(0.3)
            rect = winutil.client_rect(self.hwnd)
            if rect is None:
                raise RuntimeError("대상 창이 최소화되어 있거나 크기를 읽을 수 없습니다.")
            self._set_rect(rect)
            log.info("캡처 대상 창: %s '%s'  영역=%dx%d  오프셋=%s",
                     window.get("exe"), window.get("title"), rect[2], rect[3], self.offset)
        else:
            mons = self._sct.monitors
            if monitor_index < 0 or monitor_index >= len(mons):
                log.warning("모니터 인덱스 %d 가 범위를 벗어나 1로 대체합니다.", monitor_index)
                monitor_index = 1 if len(mons) > 1 else 0
            self.mon = mons[monitor_index]
            self.offset = (self.mon["left"], self.mon["top"])
            log.info("캡처 모니터 #%d  영역=%sx%s  오프셋=%s",
                     monitor_index, self.mon["width"], self.mon["height"], self.offset)

    def _set_rect(self, rect):
        self.mon = {"left": rect[0], "top": rect[1], "width": rect[2], "height": rect[3]}
        self.offset = (rect[0], rect[1])

    def _refresh_rect(self):
        """창 모드면 현재 위치/크기를 다시 조회. 못 잡으면 False."""
        if not self.window:
            return True
        import winutil
        rect = winutil.client_rect(self.hwnd)
        if rect is None:  # 최소화/닫힘/이동 -> 창 재탐색
            hw = winutil.find_window(self.window.get("exe"), self.window.get("title"))
            if hw:
                self.hwnd = hw
                rect = winutil.client_rect(hw)
        if rect is None:
            return False
        self._set_rect(rect)
        return True

    def grab(self):
        if not self._refresh_rect():
            return np.zeros((4, 4, 3), np.uint8)  # 못 잡으면 빈 화면(이번 탐지 실패)
        shot = self._sct.grab(self.mon)
        return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)

    def current_rect(self):
        """현재 캡처 대상의 (left, top, width, height)."""
        self._refresh_rect()
        return (self.offset[0], self.offset[1], self.mon["width"], self.mon["height"])


def make_screen(st):
    """설정에 따라 모니터/창 캡처 Screen 을 만든다."""
    if getattr(st, "capture_mode", "monitor") == "window":
        return Screen(window={"exe": st.target_exe, "title": st.target_title},
                      activate=st.activate_window)
    return Screen(st.monitor)


# ============================================================================
# 매칭 함수
# ============================================================================
def match_image(scene_bgr, template_bgr, threshold, scales=None):
    """
    템플릿 매칭. scales 가 여러 개면 템플릿을 각 배율로 리사이즈해 가장 높은
    점수를 채택한다(배율이 다른 PC 대응). 반환: (found, (cx,cy)|None, best_score)
    """
    if not scales:
        scales = [1.0]
    sh, sw = scene_bgr.shape[:2]
    best_score, best_center = 0.0, None
    for s in scales:
        if s == 1.0:
            tpl = template_bgr
        else:
            interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_CUBIC
            tpl = cv2.resize(template_bgr, None, fx=s, fy=s, interpolation=interp)
        th, tw = tpl.shape[:2]
        if th < 8 or tw < 8 or th > sh or tw > sw:
            continue
        res = cv2.matchTemplate(scene_bgr, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val > best_score:
            best_score = max_val
            best_center = (max_loc[0] + tw // 2, max_loc[1] + th // 2)
    if best_center is None:
        return False, None, 0.0
    return (best_score >= threshold), best_center, float(best_score)


def _norm(s: str) -> str:
    return "".join((s or "").split()).lower()


def _scan_ocr(data, target, eff_scale):
    """OCR 결과(image_to_data)에서 target 과 가장 비슷한 단어 묶음을 찾는다."""
    n = len(data["text"])
    lines = {}
    for i in range(n):
        if data["text"][i].strip():
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append(i)

    tgt = _norm(target)
    best_ratio, best_box = 0.0, None
    for idxs in lines.values():
        words = [data["text"][i].strip() for i in idxs]
        for a in range(len(idxs)):
            concat = ""
            for b in range(a, len(idxs)):
                concat += words[b]
                ratio = SequenceMatcher(None, _norm(concat), tgt).ratio()
                if ratio > best_ratio:
                    sel = idxs[a:b + 1]
                    x1 = min(data["left"][i] for i in sel)
                    y1 = min(data["top"][i] for i in sel)
                    x2 = max(data["left"][i] + data["width"][i] for i in sel)
                    y2 = max(data["top"][i] + data["height"][i] for i in sel)
                    best_ratio, best_box = ratio, (x1, y1, x2, y2)
                if len(_norm(concat)) > len(tgt) * 2 + 4:
                    break  # 대상보다 훨씬 길어지면 의미 없음

    if best_box is None:
        return None, 0.0
    cx = (best_box[0] + best_box[2]) / 2.0 / eff_scale
    cy = (best_box[1] + best_box[3]) / 2.0 / eff_scale
    return (int(cx), int(cy)), float(best_ratio)


def match_text(scene_bgr, target, threshold, lang, scale, psm=6):
    """
    OCR 로 화면 텍스트를 읽어 target 과 가장 비슷한 위치를 찾는다.
    psm 은 정수 또는 리스트(예: [6, 11]) — 리스트면 찾을 때까지 순서대로 시도(폴백).
    반환: (found, (cx,cy)|None, best_ratio)
    """
    import pytesseract
    from pytesseract import Output

    psms = list(psm) if isinstance(psm, (list, tuple)) else [psm]

    # 확대 배율 적용. 단, 과도한 해상도(긴 변 4000px 초과)는 속도 저하가 커서 상한을 둔다.
    eff = float(scale or 1.0)
    longest = max(scene_bgr.shape[:2])
    if eff > 1.0 and longest * eff > 4000:
        eff = max(1.0, 4000.0 / longest)
    if abs(eff - 1.0) > 1e-3:
        interp = cv2.INTER_CUBIC if eff > 1.0 else cv2.INTER_AREA
        img = cv2.resize(scene_bgr, None, fx=eff, fy=eff, interpolation=interp)
    else:
        eff, img = 1.0, scene_bgr
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    best_pt, best_ratio = None, 0.0
    for pm in psms:
        data = pytesseract.image_to_data(rgb, lang=lang, config=f"--psm {pm}",
                                         output_type=Output.DICT)
        pt, ratio = _scan_ocr(data, target, eff)
        if ratio > best_ratio:
            best_pt, best_ratio = pt, ratio
        if best_ratio >= threshold:
            break  # 폴백 불필요
    return (best_ratio >= threshold), best_pt, best_ratio


# ============================================================================
# 탐지 (페이즈 1회 인식)
# ============================================================================
def detect(phase: Phase, st: Settings, screen: Screen):
    """현재 화면에서 페이즈 대상을 1회 탐지. 반환: (found, screen_point|None, score)"""
    bgr = screen.grab()
    rx, ry = 0, 0
    if phase.region:
        rx, ry, rw, rh = phase.region
        bgr = bgr[ry:ry + rh, rx:rx + rw]
        if bgr.size == 0:
            log.error("[%s] region %s 가 캡처 영역을 벗어났습니다.", phase.name, phase.region)
            return False, None, 0.0

    if phase.type == "image":
        thr = phase.similarity if phase.similarity is not None else st.similarity
        scales = st.image_scales if st.multiscale else [1.0]
        found, pt, score = match_image(bgr, phase.template, thr, scales)
    else:
        thr = phase.ocr_confidence if phase.ocr_confidence is not None else st.ocr_confidence
        found, pt, score = match_text(bgr, phase.target, thr, st.ocr_lang,
                                      st.ocr_scale, st.ocr_psm)

    if pt is None:
        return False, None, score
    sx = pt[0] + rx + screen.offset[0]
    sy = pt[1] + ry + screen.offset[1]
    return found, (sx, sy), score


def wait_present(phase, st, screen, timeout):
    """대상이 나타날 때까지 대기. 반환: (point|None, last_score).
    검사 간격은 phase.scan_interval(있으면) -> 없으면 settings.search_interval."""
    interval = phase.scan_interval if phase.scan_interval else st.search_interval
    deadline = time.monotonic() + timeout
    last = 0.0
    while True:
        check_abort()
        found, pt, score = detect(phase, st, screen)
        last = score
        if found:
            return pt, score
        if time.monotonic() >= deadline:
            return None, last
        # 검사 간격만큼 대기(중간에 정지 키 반응하도록 잘게 나눔)
        slept = 0.0
        while slept < interval and time.monotonic() < deadline:
            check_abort()
            step = min(0.2, interval - slept)
            time.sleep(step)
            slept += step


def wait_absent(phase, st, screen, timeout):
    """대상이 사라질 때까지 대기. 사라지면 True."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_abort()
        found, _, score = detect(phase, st, screen)
        if not found:
            return True
        time.sleep(st.search_interval)
    return False


def do_click(point, phase, st):
    x = point[0] + phase.click_offset[0]
    y = point[1] + phase.click_offset[1]
    if phase.click_random:
        r = int(phase.click_random)
        x += random.randint(-r, r)
        y += random.randint(-r, r)
    pyautogui.moveTo(x, y, duration=st.move_duration)
    pyautogui.click()
    log.info("    클릭 -> (%d, %d)%s", x, y,
             f"  (랜덤±{phase.click_random})" if phase.click_random else "")


# ============================================================================
# 디버그 스크린샷 저장
# ============================================================================
def save_debug(screen, phase, point, score, tag):
    os.makedirs("debug", exist_ok=True)
    bgr = screen.grab()
    if point is not None:
        px = point[0] - screen.offset[0]
        py = point[1] - screen.offset[1]
        cv2.drawMarker(bgr, (px, py), (0, 0, 255), cv2.MARKER_CROSS, 40, 3)
        cv2.circle(bgr, (px, py), 25, (0, 0, 255), 3)
    label = f"{phase.name} {tag} score={score:.3f}"
    cv2.putText(bgr, label, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
    ts = time.strftime("%H%M%S")
    path = os.path.join("debug", f"{ts}_{phase.name}_{tag}.png")
    imwrite_u(path, bgr)
    log.info("    디버그 저장: %s", path)


# ============================================================================
# 설정 로드
# ============================================================================
def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    st = Settings(**{k: v for k, v in (raw.get("settings") or {}).items()
                     if k in Settings.__dataclass_fields__})

    phases = []
    for i, p in enumerate(raw.get("phases") or []):
        allowed = {k: v for k, v in p.items() if k in Phase.__dataclass_fields__}
        if "name" not in allowed:
            allowed["name"] = f"phase{i + 1}"
        if isinstance(allowed.get("click_offset"), list):
            allowed["click_offset"] = tuple(allowed["click_offset"])
        phases.append(Phase(**allowed))

    if not phases:
        raise ValueError("config 에 phases 가 비어 있습니다.")
    return st, phases


def prepare_phases(phases, st):
    """이미지 템플릿 로드 및 OCR 가용성 점검."""
    needs_ocr = any(p.type == "text" for p in phases)
    if needs_ocr:
        _setup_tesseract(st)

    for p in phases:
        if p.type == "image":
            if not p.target or not os.path.exists(p.target):
                raise FileNotFoundError(f"[{p.name}] 템플릿 이미지를 찾을 수 없습니다: {p.target}")
            tpl = imread_u(p.target, cv2.IMREAD_COLOR)
            if tpl is None:
                raise ValueError(f"[{p.name}] 이미지를 읽지 못했습니다: {p.target}")
            p.template = tpl
        elif p.type == "text":
            if not p.target:
                raise ValueError(f"[{p.name}] text 페이즈에 target 문구가 필요합니다.")
        else:
            raise ValueError(f"[{p.name}] 알 수 없는 type: {p.type}")


def _setup_tesseract(st):
    import pytesseract
    bundled = os.path.join(app_base(), "tesseract", "tesseract.exe")
    bundled_data = os.path.join(app_base(), "tesseract", "tessdata")
    if st.tesseract_cmd and os.path.exists(st.tesseract_cmd):
        # 사용자가 명시한 경로 최우선
        pytesseract.pytesseract.tesseract_cmd = st.tesseract_cmd
    elif os.path.exists(bundled):
        # exe 옆에 함께 배포된 번들 Tesseract (다른 PC에서도 설치 없이 동작)
        pytesseract.pytesseract.tesseract_cmd = bundled
        if os.path.isdir(bundled_data):
            os.environ["TESSDATA_PREFIX"] = bundled_data
    else:
        # 윈도우 기본 설치 경로 자동 탐색
        for cand in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                     r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
            if os.path.exists(cand):
                pytesseract.pytesseract.tesseract_cmd = cand
                break
    try:
        ver = pytesseract.get_tesseract_version()
        log.info("Tesseract OCR 버전: %s  (언어: %s)", ver, st.ocr_lang)
    except Exception as e:
        raise RuntimeError(
            "Tesseract OCR 엔진을 찾을 수 없습니다. 텍스트(text) 인식을 쓰려면 설치가 필요합니다.\n"
            "  1) https://github.com/UB-Mannheim/tesseract/wiki 에서 설치\n"
            "  2) 설치 중 'Korean' 언어 데이터 체크\n"
            "  3) 설치 경로가 다르면 config 의 settings.tesseract_cmd 에 지정\n"
            f"  세부: {e}"
        )


# ============================================================================
# 페이즈 1개 실행 (인식 -> 클릭 -> 사라짐 검증).  성공 True / 실패 False
# 선형 모드와 그래프 모드가 공통으로 사용.
# ============================================================================
def execute_phase(phase, st, screen, dry_run=False, save_dbg=False):
    find_to = phase.find_timeout if phase.find_timeout is not None else st.find_timeout
    retries = phase.max_click_retries if phase.max_click_retries is not None else st.max_click_retries

    point, score = wait_present(phase, st, screen, find_to)
    if point is None:
        log.error("    실패: %.0f초 내 대상을 찾지 못함 (최고 점수 %.3f)", find_to, score)
        if save_dbg:
            save_debug(screen, phase, None, score, "notfound")
        return False
    log.info("    인식 성공 (점수 %.3f) 위치=(%d,%d)", score, point[0], point[1])
    if save_dbg:
        save_debug(screen, phase, point, score, "found")

    if not phase.click_enabled:
        log.info("    감지만(클릭 안 함) -> 성공")
        return True

    if dry_run:
        log.info("    [DRY-RUN] 클릭 생략")
        return True

    for attempt in range(1, max(1, retries) + 1):
        do_click(point, phase, st)
        time.sleep(st.post_click_delay)

        if not phase.verify_disappear:
            return True
        if wait_absent(phase, st, screen, st.disappear_timeout):
            log.info("    검증 성공: 대상이 사라짐")
            return True

        log.warning("    대상이 아직 남아있음 (시도 %d/%d) - 좌표 갱신 후 재클릭",
                    attempt, max(1, retries))
        new_pt, _ = wait_present(phase, st, screen, st.search_interval * 3)
        if new_pt:
            point = new_pt

    log.error("    실패: 클릭 후에도 대상이 사라지지 않음")
    if save_dbg:
        save_debug(screen, phase, point, score, "notgone")
    return False


# ============================================================================
# 선형(리스트) 실행 루프 - 기존 config.yaml 호환
# ============================================================================
def run(st, phases, dry_run=False, save_dbg=False):
    screen = make_screen(st)
    _start_stop_listener(st.stop_key)

    log.info("=" * 60)
    log.info("매크로 시작  (페이즈 %d개)", len(phases))
    log.info("비상정지: 마우스를 화면 좌상단 끝으로 이동 / '%s' 키 / Ctrl+C", st.stop_key)
    if dry_run:
        log.info(">> DRY-RUN 모드: 실제 클릭하지 않고 탐지만 확인합니다.")
    log.info("=" * 60)

    for idx, phase in enumerate(phases, 1):
        log.info("[페이즈 %d/%d] '%s'  type=%s  action=%s",
                 idx, len(phases), phase.name, phase.type, phase.action)

        if phase.action == "end":
            find_to = phase.find_timeout if phase.find_timeout is not None else st.find_timeout
            point, score = wait_present(phase, st, screen, find_to)
            if point is None:
                log.error("    종료 트리거를 찾지 못함")
                return False
            if phase.click_on_end and not dry_run:
                do_click(point, phase, st)
            log.info("    종료 트리거 감지 -> 매크로 정상 종료")
            return True

        ok = execute_phase(phase, st, screen, dry_run, save_dbg)
        if not ok:
            if st.on_fail == "skip":
                log.warning("    on_fail=skip -> 다음 페이즈로 진행")
                continue
            log.error("    on_fail=abort -> 매크로 중단")
            return False

    log.info("모든 페이즈 완료 -> 매크로 종료")
    return True


# ============================================================================
# 그래프(노드) 실행 - GUI 에디터(.json)용 상태 머신
#   start 노드에서 시작 -> 각 phase 노드 성공 시 'success' 링크, 실패 시 'fail' 링크
#   -> end 노드 도달 시 종료(result: success/fail)
# ============================================================================
def _node_to_phase(node):
    return Phase(
        name=node.get("name", node.get("id", "?")),
        type=node.get("match", "image"),
        target=node.get("target", ""),
        action="click",
        region=node.get("region"),
        similarity=node.get("similarity"),
        ocr_confidence=node.get("ocr_confidence"),
        verify_disappear=node.get("verify_disappear", True),
        click_offset=tuple(node.get("click_offset", (0, 0))),
        find_timeout=node.get("find_timeout"),
        max_click_retries=node.get("max_click_retries"),
        click_enabled=node.get("click", True),
        scan_interval=node.get("scan_interval") or None,
        click_random=int(node.get("click_random", 0) or 0),
    )


def load_graph(path):
    """GUI 에디터가 저장한 .json 그래프를 읽어 (settings, nodes, edges) 반환."""
    import json
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    st = Settings(**{k: v for k, v in (data.get("settings") or {}).items()
                     if k in Settings.__dataclass_fields__})
    nodes = {n["id"]: n for n in data.get("nodes", [])}
    edges = {}
    for lk in data.get("links", []):
        edges[(lk["from"], lk.get("port", "out"))] = lk["to"]
    return st, nodes, edges


def _load_settings_any(path):
    """yaml/json 어느 형식이든 settings 만 안전하게 읽는다(진단 모드용)."""
    if not path or not os.path.exists(path):
        return Settings()
    try:
        if str(path).lower().endswith(".json"):
            return load_graph(path)[0]
        return load_config(path)[0]
    except Exception:
        return Settings()


def prepare_graph(nodes, st):
    """그래프의 이미지 템플릿을 미리 로드/검증하고 OCR 가용성 점검. id->template 반환."""
    phase_nodes = [n for n in nodes.values() if n.get("type") == "phase"]
    if not any(n.get("type") == "start" for n in nodes.values()):
        raise ValueError("시작(start) 노드가 없습니다.")
    if any(n.get("match") == "text" for n in phase_nodes):
        _setup_tesseract(st)

    templates = {}
    for n in phase_nodes:
        if n.get("match", "image") == "image":
            p = n.get("target", "")
            if not p or not os.path.exists(p):
                raise FileNotFoundError(f"[{n.get('name', n['id'])}] 템플릿 이미지 없음: {p}")
            tpl = imread_u(p, cv2.IMREAD_COLOR)
            if tpl is None:
                raise ValueError(f"[{n.get('name', n['id'])}] 이미지를 읽지 못함: {p}")
            templates[n["id"]] = tpl
        elif not n.get("target"):
            raise ValueError(f"[{n.get('name', n['id'])}] 텍스트 노드에 찾을 문구가 없습니다.")
    return templates


def _write_progress(path, data):
    """현재 실행 중인 노드를 파일로 알린다(에디터 하이라이트용). 실패해도 무시."""
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _interruptible_sleep(seconds):
    end_t = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < end_t:
        check_abort()
        time.sleep(min(0.1, end_t - time.monotonic()))


def run_graph(st, nodes, edges, templates=None, dry_run=False, save_dbg=False,
              progress_path=None):
    templates = templates or {}
    screen = make_screen(st)
    _start_stop_listener(st.stop_key)

    start = next(n for n in nodes.values() if n.get("type") == "start")
    log.info("=" * 60)
    log.info("매크로 시작 (그래프 모드, 노드 %d개)", len(nodes))
    log.info("비상정지: 마우스를 화면 좌상단 끝으로 이동 / '%s' 키 / Ctrl+C", st.stop_key)
    if dry_run:
        log.info(">> DRY-RUN 모드: 실제 클릭하지 않고 탐지만 확인합니다.")
    log.info("=" * 60)

    current = edges.get((start["id"], "out"))
    if current is None:
        log.error("시작 노드가 어떤 노드에도 연결되어 있지 않습니다.")
        return False

    loop_counts = {}
    steps = 0
    result = False
    while current is not None:
        check_abort()
        steps += 1
        if steps > 10_000_000:
            log.error("최대 실행 단계 초과 - 무한 루프로 판단해 중단합니다.")
            result = False
            break

        node = nodes.get(current)
        if node is None:
            log.error("연결된 노드 '%s' 를 찾을 수 없습니다.", current)
            result = False
            break

        ntype = node.get("type")
        name = node.get("name", current)
        _write_progress(progress_path, {"current": current, "name": name, "type": ntype})

        if ntype == "end":
            result = (node.get("result", "success") == "success")
            log.info("종료 노드 '%s' 도달 -> %s", name, "성공 종료" if result else "실패 종료")
            break

        elif ntype == "delay":
            smin = float(node.get("seconds", 1) or 0)
            smax = float(node.get("seconds_max", 0) or 0)
            secs = random.uniform(smin, smax) if smax > smin else smin
            log.info("[딜레이] '%s'  %.2f초 대기%s", name, secs,
                     f" (랜덤 {smin}~{smax})" if smax > smin else "")
            if not dry_run:
                _interruptible_sleep(secs)
            current = edges.get((current, "next"))
            if current is None:
                result = True
                break

        elif ntype == "move":
            log.info("[이동] '%s'  화면 중앙으로 마우스 이동", name)
            if not dry_run:
                left, top, w, h = screen.current_rect()
                pyautogui.moveTo(left + w // 2, top + h // 2, duration=st.move_duration)
            current = edges.get((current, "next"))
            if current is None:
                result = True
                break

        elif ntype == "loop":
            cnt = int(node.get("count", 1) or 0)
            c = loop_counts.get(node["id"], 0)
            if c < cnt:
                loop_counts[node["id"]] = c + 1
                log.info("[반복] '%s'  %d/%d 회차", name, c + 1, cnt)
                nxt = edges.get((current, "loop"))
            else:
                loop_counts[node["id"]] = 0  # 바깥 루프 재진입 대비 초기화
                log.info("[반복] '%s'  %d회 완료", name, cnt)
                nxt = edges.get((current, "done"))
            if nxt is None:
                result = True
                break
            current = nxt

        elif ntype == "phase":
            phase = _node_to_phase(node)
            phase.template = templates.get(node["id"])
            tgt = phase.target if phase.type == "text" else os.path.basename(phase.target)
            log.info("[노드] '%s'  type=%s  대상='%s'", phase.name, phase.type, tgt)
            ok = execute_phase(phase, st, screen, dry_run, save_dbg)
            port = "success" if ok else "fail"
            nxt = edges.get((current, port))
            if nxt is None:
                log.info("    '%s' 의 [%s] 출력이 연결되지 않음 -> 매크로 종료(%s)",
                         phase.name, "성공" if ok else "실패", ok)
                result = ok
                break
            log.info("    -> [%s] 경로로 이동", "성공" if ok else "실패")
            current = nxt

        else:
            log.error("알 수 없는 노드 타입: %s", ntype)
            result = False
            break

    _write_progress(progress_path, {"current": None, "done": True, "result": result})
    return result


# ============================================================================
# 키매핑 (앱플레이어식: 단축키 -> 대상 창의 비율 위치 클릭)
# ============================================================================
def _key_to_str(key):
    try:
        from pynput import keyboard
        if isinstance(key, keyboard.KeyCode) and key.char:
            return key.char.lower()
        if isinstance(key, keyboard.Key):
            return key.name.lower()
    except Exception:
        pass
    return str(key).lower()


def run_keymap(st, keymaps):
    from pynput import keyboard
    # 키매핑 비율은 '대상 창' 기준이므로 대상 창이 있으면 창 모드 강제
    if st.target_exe and st.capture_mode != "window":
        st.capture_mode = "window"
    screen = make_screen(st)
    mapping = {}
    for km in keymaps:
        k = str(km.get("key", "")).strip().lower()
        if k:
            mapping[k] = km
    if not mapping:
        log.error("키매핑이 비어 있습니다.")
        return
    stop = st.stop_key.strip().lower()
    log.info("=" * 60)
    log.info("키매핑 모드 시작 (%d개). 정지: '%s' 키 또는 Ctrl+C", len(mapping), stop)
    for k, km in mapping.items():
        log.info("  [%s] -> 비율(%.3f, %.3f) %s", k, float(km.get("x", 0)),
                 float(km.get("y", 0)), km.get("button", "left"))
    log.info("=" * 60)

    def on_press(key):
        ks = _key_to_str(key)
        if ks == stop:
            log.info("정지 키 감지 -> 종료")
            return False
        km = mapping.get(ks)
        if not km:
            return
        left, top, w, h = screen.current_rect()
        x = int(left + float(km.get("x", 0)) * w)
        y = int(top + float(km.get("y", 0)) * h)
        try:
            pyautogui.click(x, y, button=km.get("button", "left"))
            log.info("키 '%s' -> 클릭 (%d, %d)", ks, x, y)
        except Exception as e:
            log.error("클릭 실패: %s", e)

    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()


# ============================================================================
# 보조 모드 (모니터 목록 / 즉석 탐지 테스트)
# ============================================================================
def list_monitors():
    with mss.MSS() as sct:
        for i, m in enumerate(sct.monitors):
            tag = "전체 가상화면" if i == 0 else f"모니터 #{i}"
            print(f"[{i}] {tag}  left={m['left']} top={m['top']} "
                  f"width={m['width']} height={m['height']}")


def probe(st, image_path=None, text=None):
    """현재 화면에서 한 번 탐지해 결과를 출력 (임계값 튜닝용)."""
    screen = make_screen(st)
    if image_path:
        tpl = imread_u(image_path, cv2.IMREAD_COLOR)
        if tpl is None:
            print("이미지를 읽지 못했습니다:", image_path)
            return
        scales = st.image_scales if st.multiscale else [1.0]
        found, pt, score = match_image(screen.grab(), tpl, st.similarity, scales)
        print(f"[이미지] 점수={score:.3f}  임계값={st.similarity}  "
              f"{'발견' if found else '미발견'}  위치={pt}  (멀티스케일={st.multiscale})")
    if text:
        _setup_tesseract(st)
        found, pt, score = match_text(screen.grab(), text, st.ocr_confidence,
                                      st.ocr_lang, st.ocr_scale, st.ocr_psm)
        sp = None
        if pt:
            sp = (pt[0] + screen.offset[0], pt[1] + screen.offset[1])
        print(f"[텍스트] 유사도={score:.3f}  임계값={st.ocr_confidence}  "
              f"{'발견' if found else '미발견'}  위치={sp}")


# ============================================================================
def main():
    ap = argparse.ArgumentParser(description="이미지/텍스트 인식 자동 클릭 매크로")
    ap.add_argument("config", nargs="?", default="config.yaml", help="설정 파일 경로")
    ap.add_argument("--dry-run", action="store_true", help="클릭 없이 탐지만 확인")
    ap.add_argument("--save-debug", action="store_true", help="탐지 결과 스크린샷 저장")
    ap.add_argument("--list-monitors", action="store_true", help="모니터 목록 출력")
    ap.add_argument("--probe-image", metavar="PNG", help="이미지 1회 탐지 테스트")
    ap.add_argument("--probe-text", metavar="STR", help="텍스트 1회 탐지 테스트")
    ap.add_argument("--ocr-image", metavar="IMG", help="이미지 파일을 OCR 해 인식 텍스트 출력(번들 OCR 점검용)")
    ap.add_argument("--progress", metavar="JSON", help="현재 실행 노드를 이 파일에 기록(에디터 하이라이트용)")
    ap.add_argument("--keymap", action="store_true", help="키매핑 모드로 실행(매크로 대신 단축키->클릭)")
    args = ap.parse_args()

    # 윈도우 콘솔에서 한글이 깨지지 않도록 UTF-8 로 출력
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # exe(frozen)면 실행 파일 폴더 기준으로 동작 (config/templates/로그/번들 OCR 경로 일치)
    if is_frozen():
        try:
            os.chdir(app_base())
        except Exception:
            pass

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler("macro.log", encoding="utf-8")],
    )

    if args.list_monitors:
        list_monitors()
        return

    if args.ocr_image:
        st = _load_settings_any(args.config)
        _setup_tesseract(st)
        import pytesseract
        img = imread_u(args.ocr_image, cv2.IMREAD_COLOR)
        if img is None:
            print("이미지를 읽지 못했습니다:", args.ocr_image)
            return
        txt = pytesseract.image_to_string(
            cv2.cvtColor(img, cv2.COLOR_BGR2RGB), lang=st.ocr_lang,
            config=f"--psm {st.ocr_psm}")
        print("=== OCR 결과 (lang=%s) ===" % st.ocr_lang)
        print(txt.strip())
        return

    # probe 는 config 의 settings 만 사용
    if args.probe_image or args.probe_text:
        st = _load_settings_any(args.config)
        probe(st, args.probe_image, args.probe_text)
        return

    # 키매핑 모드 (매크로 실행 대신)
    if args.keymap:
        st = _load_settings_any(args.config)
        try:
            with open(args.config, encoding="utf-8") as f:
                keymaps = json.load(f).get("keymaps", [])
        except Exception as e:
            log.error("키매핑 로드 실패: %s", e)
            sys.exit(1)
        if not keymaps:
            log.error("키매핑(keymaps)이 없습니다. 에디터의 [키매핑]에서 추가하세요.")
            sys.exit(1)
        try:
            run_keymap(st, keymaps)
        except (AbortException, KeyboardInterrupt):
            log.warning("키매핑 중단됨")
        except pyautogui.FailSafeException:
            log.warning("FAILSAFE 로 중단됨")
        return

    is_graph = str(args.config).lower().endswith(".json")
    try:
        if is_graph:
            st, nodes, edges = load_graph(args.config)
            templates = prepare_graph(nodes, st)
        else:
            st, phases = load_config(args.config)
            prepare_phases(phases, st)
    except Exception as e:
        log.error("설정 오류: %s", e)
        sys.exit(1)

    log.info("3초 후 시작합니다. 대상 창을 활성화하세요...")
    time.sleep(3)

    try:
        if is_graph:
            ok = run_graph(st, nodes, edges, templates,
                           dry_run=args.dry_run, save_dbg=args.save_debug,
                           progress_path=args.progress)
        else:
            ok = run(st, phases, dry_run=args.dry_run, save_dbg=args.save_debug)
        sys.exit(0 if ok else 2)
    except AbortException:
        log.warning("사용자 요청으로 중단되었습니다.")
        sys.exit(130)
    except pyautogui.FailSafeException:
        log.warning("FAILSAFE(마우스 좌상단) 로 중단되었습니다.")
        sys.exit(130)
    except KeyboardInterrupt:
        log.warning("Ctrl+C 로 중단되었습니다.")
        sys.exit(130)


if __name__ == "__main__":
    main()
