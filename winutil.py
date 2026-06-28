#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
winutil.py - 창/프로세스 열거 및 창 좌표 조회 (순수 ctypes, 추가 의존성 없음)

대상 창을 프로세스(exe) + 제목으로 지정해, 그 창의 클라이언트 영역만
캡처/클릭하기 위한 도구.
"""
import ctypes
import os
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 64비트에서 핸들이 잘리지 않도록 시그니처 지정
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SW_RESTORE = 9


def _exe_name(pid):
    h = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(512)
        buf = ctypes.create_unicode_buffer(512)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(h)
    return ""


def list_windows():
    """보이는 최상위 창 목록(제목 있는 것만): [{hwnd, title, pid, exe}] (중복 exe 정리)."""
    out = []

    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append({"hwnd": int(hwnd), "title": title,
                    "pid": int(pid.value), "exe": _exe_name(pid.value)})
        return True

    user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return out


def find_window(exe=None, title=None):
    """exe(파일명) / 제목(부분일치)로 창 hwnd 검색. 제목이 정확히 일치하면 우선."""
    exe_l = (exe or "").lower().strip()
    title_l = (title or "").lower().strip()
    cands = list_windows()
    if exe_l:
        cands = [w for w in cands if w["exe"].lower() == exe_l]
    if title_l:
        exact = [w for w in cands if w["title"].lower() == title_l]
        partial = [w for w in cands if title_l in w["title"].lower()]
        cands = exact or partial
    return cands[0]["hwnd"] if cands else None


def client_rect(hwnd):
    """창 '클라이언트 영역'을 화면 좌표 (left, top, width, height) 로 반환(테두리 제외)."""
    r = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    return (pt.x, pt.y, w, h)


def is_minimized(hwnd):
    return bool(user32.IsIconic(hwnd))


def activate(hwnd):
    """창을 복원하고 앞으로 가져온다."""
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, _SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    # 점검용: 현재 창 목록 출력
    import json
    print(json.dumps(list_windows(), ensure_ascii=False, indent=2))
