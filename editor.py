#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
editor.py - ImgMacro 노드 그래프 에디터 (DearPyGui)

Shader Graph 처럼 노드를 만들고 선으로 흐름을 잇는다.
  [시작] -> [페이즈] --성공--> [페이즈] --성공--> [성공 종료]
                  └--실패--> [실패 종료] 등 원하는 곳으로
각 페이즈 노드:
  - 캡처 버튼으로 화면 영역을 드래그 -> 템플릿 자동 등록
  - 또는 '텍스트' 방식으로 찾을 문구 입력 (한글 OCR)
  - '성공' / '실패' 출력 포트를 다음 노드로 연결
저장하면 macro 엔진이 실행하는 .json 그래프가 만들어진다.
"""
import json
import os
import subprocess
import sys
import warnings

warnings.filterwarnings("ignore")

import dearpygui.dearpygui as dpg

TITLE = "ImgMacro Editor"


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE = base_dir()
TEMPLATES = os.path.join(BASE, "templates")
IS_FROZEN = getattr(sys, "frozen", False)

IN_PORT = {"in"}
OUT_PORT = {"success", "fail", "out"}


def restore_window(title=TITLE):
    """최소화했던 에디터 창을 다시 복원/포커스 (Windows)."""
    try:
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


class Editor:
    def __init__(self):
        self.counter = 1          # 노드 id 카운터
        self.end_counter = 1
        self.nodes = {}           # nid -> {id,type,node_tag,target,region}
        self.links = {}           # link_id -> (src_attr, dst_attr)
        self.out_link = {}        # src_attr -> link_id (출력당 1개 강제)
        self.run_proc = None
        self.project_path = None

    # ---------------------------------------------------------------- 노드
    def _new_pos(self):
        k = len(self.nodes)
        return [80 + (k % 6) * 60, 90 + (k % 6) * 50]

    def add_start_node(self, pos=None):
        with dpg.node(label="시작", tag="start", pos=pos or [40, 220],
                      parent="editor"):
            with dpg.node_attribute(tag="start.out",
                                    attribute_type=dpg.mvNode_Attr_Output):
                dpg.add_text("시작 >")
        self.nodes["start"] = {"id": "start", "type": "start", "node_tag": "start"}

    def add_phase_node(self, data=None, pos=None):
        data = data or {}
        nid = data.get("id") or f"node{self.counter}"
        self.counter += 1
        is_text = data.get("match") == "text"
        match_label = "텍스트" if is_text else "이미지"
        with dpg.node(label="페이즈", tag=nid, pos=pos or self._new_pos(),
                      parent="editor"):
            with dpg.node_attribute(tag=f"{nid}.in",
                                    attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("이전")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_text(tag=f"{nid}.name", width=210,
                                   default_value=data.get("name", "새 페이즈"),
                                   hint="페이즈 이름")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                with dpg.group(horizontal=True):
                    dpg.add_text("방식")
                    dpg.add_combo(["이미지", "텍스트"], tag=f"{nid}.match",
                                  default_value=match_label, width=150,
                                  callback=self._toggle_match, user_data=nid)
            # 이미지 행
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static,
                                    tag=f"{nid}.imgrow"):
                with dpg.group(horizontal=True):
                    dpg.add_button(label="캡처", width=80,
                                   callback=self._capture, user_data=nid)
                    lbl = os.path.basename(data.get("target", "")) if not is_text else ""
                    dpg.add_text(lbl or "(미캡처)", tag=f"{nid}.imglabel")
            # 텍스트 행
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static,
                                    tag=f"{nid}.txtrow"):
                dpg.add_input_text(tag=f"{nid}.text", width=210, hint="찾을 문구(한/영)",
                                   default_value=data.get("target", "") if is_text else "")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                with dpg.group(horizontal=True):
                    dpg.add_text("정확도")
                    dft = data.get("threshold")
                    if dft is None:
                        dft = 0.85 if is_text else 0.95
                    dpg.add_slider_float(tag=f"{nid}.thr", width=140,
                                         min_value=0.5, max_value=1.0,
                                         default_value=float(dft), format="%.2f")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_checkbox(label="클릭 후 사라짐 확인", tag=f"{nid}.verify",
                                 default_value=data.get("verify_disappear", True))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                with dpg.group(horizontal=True):
                    dpg.add_text("제한(초)")
                    dpg.add_input_float(tag=f"{nid}.timeout", width=64, step=0,
                                        default_value=float(data.get("find_timeout") or 30),
                                        format="%.0f")
                    dpg.add_text("재클릭")
                    dpg.add_input_int(tag=f"{nid}.retries", width=64, step=0,
                                      default_value=int(data.get("max_click_retries") or 3))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                with dpg.group(horizontal=True):
                    dpg.add_button(label="영역", width=64,
                                   callback=self._capture_region, user_data=nid)
                    dpg.add_text(self._region_label(data.get("region")),
                                 tag=f"{nid}.regionlabel")
            with dpg.node_attribute(tag=f"{nid}.success",
                                    attribute_type=dpg.mvNode_Attr_Output):
                dpg.add_text("성공 >")
            with dpg.node_attribute(tag=f"{nid}.fail",
                                    attribute_type=dpg.mvNode_Attr_Output):
                dpg.add_text("실패 >")
        self.nodes[nid] = {"id": nid, "type": "phase", "node_tag": nid,
                           "target": data.get("target", "") if not is_text else "",
                           "region": data.get("region")}
        self._toggle_match(None, match_label, nid)
        return nid

    def add_end_node(self, data=None, pos=None):
        data = data or {}
        nid = data.get("id") or f"end{self.end_counter}"
        self.end_counter += 1
        res_label = "실패 종료" if data.get("result") == "fail" else "성공 종료"
        with dpg.node(label="종료", tag=nid, pos=pos or self._new_pos(),
                      parent="editor"):
            with dpg.node_attribute(tag=f"{nid}.in",
                                    attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("이전")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_combo(["성공 종료", "실패 종료"], tag=f"{nid}.result",
                              default_value=res_label, width=150)
        self.nodes[nid] = {"id": nid, "type": "end", "node_tag": nid}
        return nid

    def _toggle_match(self, sender, app_data, user_data):
        nid = user_data
        is_img = (app_data or dpg.get_value(f"{nid}.match")) == "이미지"
        dpg.configure_item(f"{nid}.imgrow", show=is_img)
        dpg.configure_item(f"{nid}.txtrow", show=not is_img)

    @staticmethod
    def _region_label(region):
        if not region:
            return "영역: 전체"
        return f"영역: {region[2]}x{region[3]}"

    # ---------------------------------------------------------------- 링크
    def _attr_port(self, attr):
        return str(attr).rsplit(".", 1)[-1]

    def _on_link(self, sender, app_data):
        a = dpg.get_item_alias(app_data[0]) or str(app_data[0])
        b = dpg.get_item_alias(app_data[1]) or str(app_data[1])
        pa, pb = self._attr_port(a), self._attr_port(b)
        if pa in OUT_PORT and pb in IN_PORT:
            src, dst = a, b
        elif pb in OUT_PORT and pa in IN_PORT:
            src, dst = b, a
        else:
            return  # 출력-출력 / 입력-입력 연결 금지
        # 한 출력 포트는 한 곳으로만
        if src in self.out_link:
            old = self.out_link[src]
            if dpg.does_item_exist(old):
                dpg.delete_item(old)
            self.links.pop(old, None)
        link_id = dpg.add_node_link(src, dst, parent="editor")
        self.links[link_id] = (src, dst)
        self.out_link[src] = link_id

    def _on_delink(self, sender, app_data):
        lid = app_data
        pair = self.links.pop(lid, None)
        if pair and self.out_link.get(pair[0]) == lid:
            self.out_link.pop(pair[0], None)
        if dpg.does_item_exist(lid):
            dpg.delete_item(lid)

    def _delete_selected(self):
        for lid in dpg.get_selected_links("editor"):
            self._on_delink(None, lid)
        for node_id in dpg.get_selected_nodes("editor"):
            nid = dpg.get_item_alias(node_id) or str(node_id)
            self._delete_node(nid)

    def _delete_node(self, nid):
        if nid == "start" or nid not in self.nodes:
            return
        for lid, (s, d) in list(self.links.items()):
            if s.startswith(nid + ".") or d.startswith(nid + "."):
                self._on_delink(None, lid)
        if dpg.does_item_exist(nid):
            dpg.delete_item(nid)
        self.nodes.pop(nid, None)

    # ---------------------------------------------------------------- 캡처
    def _snip_cmd(self, extra):
        if IS_FROZEN:
            return [os.path.join(BASE, "Snip.exe")] + extra
        return [sys.executable, os.path.join(BASE, "snip.py")] + extra

    def _run_snip(self, extra):
        res = os.path.join(BASE, "_snip_result.json")
        try:
            os.remove(res)
        except OSError:
            pass
        os.makedirs(TEMPLATES, exist_ok=True)
        dpg.minimize_viewport()
        try:
            subprocess.run(self._snip_cmd(["--json", res] + extra), check=False)
        finally:
            restore_window()
        if os.path.exists(res):
            try:
                with open(res, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def _capture(self, sender, app_data, user_data):
        nid = user_data
        # 파일명: 노드 이름에서 안전한 문자만 (isalnum 은 한글도 허용)
        name = "".join(c for c in dpg.get_value(f"{nid}.name")
                       if c.isalnum() or c in "_-")[:30] or nid
        data = self._run_snip(["--out-dir", TEMPLATES, name])
        if data and not data.get("cancelled"):
            path = data["path"]
            # 프로젝트 이식성을 위해 가능하면 상대경로로 저장
            try:
                rel = os.path.relpath(path, BASE).replace("\\", "/")
                if not rel.startswith(".."):
                    path = rel
            except Exception:
                pass
            self.nodes[nid]["target"] = path
            dpg.set_value(f"{nid}.imglabel", os.path.basename(path))
            dpg.set_value(f"{nid}.match", "이미지")
            self._toggle_match(None, "이미지", nid)

    def _capture_region(self, sender, app_data, user_data):
        nid = user_data
        data = self._run_snip(["--no-save", "x"])
        if data and not data.get("cancelled"):
            self.nodes[nid]["region"] = data["region"]
            dpg.set_value(f"{nid}.regionlabel", self._region_label(data["region"]))

    # ---------------------------------------------------------------- 직렬화
    def gather_settings(self):
        psm_raw = dpg.get_value("set.ocr_psm")
        psm = [int(x) for x in psm_raw.split(",")] if "," in psm_raw else int(psm_raw)
        return {
            "monitor": dpg.get_value("set.monitor"),
            "similarity": round(dpg.get_value("set.similarity"), 3),
            "multiscale": dpg.get_value("set.multiscale"),
            "ocr_confidence": round(dpg.get_value("set.ocr_confidence"), 3),
            "ocr_scale": dpg.get_value("set.ocr_scale"),
            "ocr_psm": psm,
            "find_timeout": dpg.get_value("set.find_timeout"),
            "disappear_timeout": dpg.get_value("set.disappear_timeout"),
            "search_interval": dpg.get_value("set.search_interval"),
            "post_click_delay": dpg.get_value("set.post_click_delay"),
            "max_click_retries": dpg.get_value("set.max_click_retries"),
            "stop_key": dpg.get_value("set.stop_key"),
        }

    def serialize(self):
        nodes = []
        for nid, n in self.nodes.items():
            pos = dpg.get_item_pos(n["node_tag"]) if dpg.does_item_exist(n["node_tag"]) else [0, 0]
            entry = {"id": nid, "type": n["type"], "pos": [int(pos[0]), int(pos[1])]}
            if n["type"] == "phase":
                is_img = dpg.get_value(f"{nid}.match") == "이미지"
                thr = round(dpg.get_value(f"{nid}.thr"), 3)
                entry.update({
                    "name": dpg.get_value(f"{nid}.name"),
                    "match": "image" if is_img else "text",
                    "target": n.get("target", "") if is_img else dpg.get_value(f"{nid}.text"),
                    "region": n.get("region"),
                    "verify_disappear": dpg.get_value(f"{nid}.verify"),
                    "find_timeout": dpg.get_value(f"{nid}.timeout"),
                    "max_click_retries": dpg.get_value(f"{nid}.retries"),
                })
                if is_img:
                    entry["similarity"] = thr
                else:
                    entry["ocr_confidence"] = thr
            elif n["type"] == "end":
                entry["result"] = "fail" if dpg.get_value(f"{nid}.result") == "실패 종료" else "success"
            nodes.append(entry)
        links = []
        for lid, (src, dst) in self.links.items():
            snid, port = src.rsplit(".", 1)
            links.append({"from": snid, "port": port, "to": dst.rsplit(".", 1)[0]})
        return {"settings": self.gather_settings(), "nodes": nodes, "links": links}

    def write_json(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.serialize(), f, ensure_ascii=False, indent=2)

    # ---------------------------------------------------------------- 로드
    def clear(self):
        for nid in list(self.nodes):
            if nid != "start":
                self._delete_node(nid)
        for lid in list(self.links):
            self._on_delink(None, lid)
        if dpg.does_item_exist("start"):
            dpg.delete_item("start")
        self.nodes.clear()
        self.links.clear()
        self.out_link.clear()
        self.counter = 1
        self.end_counter = 1

    def load_dict(self, data):
        self.clear()
        s = data.get("settings", {})
        self._apply_settings(s)
        for n in data.get("nodes", []):
            t = n.get("type")
            pos = n.get("pos")
            if t == "start":
                self.add_start_node(pos)
            elif t == "end":
                self.add_end_node(n, pos)
            elif t == "phase":
                d = dict(n)
                d["threshold"] = n.get("similarity", n.get("ocr_confidence"))
                self.add_phase_node(d, pos)
        if "start" not in self.nodes:
            self.add_start_node()
        # 링크 복원
        for lk in data.get("links", []):
            src = f"{lk['from']}.{lk.get('port', 'out')}"
            dst = f"{lk['to']}.in"
            if dpg.does_item_exist(src) and dpg.does_item_exist(dst):
                lid = dpg.add_node_link(src, dst, parent="editor")
                self.links[lid] = (src, dst)
                self.out_link[src] = lid

    def _apply_settings(self, s):
        def setv(tag, val):
            if val is not None and dpg.does_item_exist(tag):
                dpg.set_value(tag, val)
        setv("set.monitor", s.get("monitor"))
        setv("set.similarity", s.get("similarity"))
        setv("set.multiscale", s.get("multiscale"))
        setv("set.ocr_confidence", s.get("ocr_confidence"))
        setv("set.ocr_scale", s.get("ocr_scale"))
        psm = s.get("ocr_psm")
        if psm is not None:
            setv("set.ocr_psm", ",".join(map(str, psm)) if isinstance(psm, list) else str(psm))
        setv("set.find_timeout", s.get("find_timeout"))
        setv("set.disappear_timeout", s.get("disappear_timeout"))
        setv("set.search_interval", s.get("search_interval"))
        setv("set.post_click_delay", s.get("post_click_delay"))
        setv("set.max_click_retries", s.get("max_click_retries"))
        setv("set.stop_key", s.get("stop_key"))

    # ---------------------------------------------------------------- 파일 다이얼로그
    def _on_save_file(self, sender, app_data):
        path = app_data["file_path_name"]
        if not path.lower().endswith(".json"):
            path += ".json"
        self.project_path = path
        self.write_json(path)
        self._status(f"저장됨: {os.path.basename(path)}")

    def _on_open_file(self, sender, app_data):
        path = app_data["file_path_name"]
        try:
            with open(path, encoding="utf-8") as f:
                self.load_dict(json.load(f))
            self.project_path = path
            self._status(f"열림: {os.path.basename(path)}")
        except Exception as e:
            self._status(f"열기 실패: {e}")

    def menu_save(self):
        if self.project_path:
            self.write_json(self.project_path)
            self._status(f"저장됨: {os.path.basename(self.project_path)}")
        else:
            dpg.show_item("savedlg")

    def menu_save_as(self):
        dpg.show_item("savedlg")

    def menu_open(self):
        dpg.show_item("opendlg")

    # ---------------------------------------------------------------- 실행
    def _runner_cmd(self, path, dry):
        if IS_FROZEN:
            cmd = [os.path.join(BASE, "ImgMacro.exe"), path]
        else:
            cmd = [sys.executable, os.path.join(BASE, "macro.py"), path]
        if dry:
            cmd.append("--dry-run")
        return cmd

    def run_macro(self, dry=False):
        if self.run_proc and self.run_proc.poll() is None:
            self._status("이미 실행 중입니다.")
            return
        if not any(n["type"] == "phase" for n in self.nodes.values()):
            self._status("페이즈 노드가 없습니다.")
            return
        if ("start", "out") not in {(s.rsplit('.', 1)[0], s.rsplit('.', 1)[1])
                                    for s, _ in self.links.values()}:
            self._status("시작 노드를 첫 페이즈에 연결하세요.")
            return
        run_path = os.path.join(BASE, "_run.json")
        self.write_json(run_path)
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_CONSOLE
        try:
            dpg.minimize_viewport()
            self.run_proc = subprocess.Popen(self._runner_cmd(run_path, dry),
                                             creationflags=flags)
            self._status("실행 중... (F12 또는 마우스 좌상단으로 정지)")
        except Exception as e:
            restore_window()
            self._status(f"실행 실패: {e}")

    def _status(self, msg):
        if dpg.does_item_exist("statusbar"):
            dpg.set_value("statusbar", msg)

    # ---------------------------------------------------------------- UI 구성
    def build_settings_window(self):
        with dpg.window(label="전역 설정", tag="settingswin", show=False,
                        width=380, height=460, pos=[300, 120]):
            dpg.add_input_int(label="모니터(1=주)", tag="set.monitor", default_value=1, width=120)
            dpg.add_checkbox(label="멀티스케일(배율 다른 PC 대응)", tag="set.multiscale",
                             default_value=True)
            dpg.add_slider_float(label="이미지 정확도", tag="set.similarity",
                                 min_value=0.5, max_value=1.0, default_value=0.95, format="%.2f")
            dpg.add_separator()
            dpg.add_text("OCR(텍스트)")
            dpg.add_slider_float(label="텍스트 정확도", tag="set.ocr_confidence",
                                 min_value=0.5, max_value=1.0, default_value=0.80, format="%.2f")
            dpg.add_input_float(label="OCR 확대배율", tag="set.ocr_scale",
                                default_value=2.0, width=120, step=0, format="%.1f")
            dpg.add_combo(["6", "11", "6,11"], label="OCR 분할모드(PSM)",
                          tag="set.ocr_psm", default_value="6", width=120)
            dpg.add_separator()
            dpg.add_input_float(label="인식 제한(초)", tag="set.find_timeout",
                                default_value=30, width=120, step=0, format="%.0f")
            dpg.add_input_float(label="사라짐 대기(초)", tag="set.disappear_timeout",
                                default_value=10, width=120, step=0, format="%.0f")
            dpg.add_input_float(label="스캔 간격(초)", tag="set.search_interval",
                                default_value=0.4, width=120, step=0, format="%.2f")
            dpg.add_input_float(label="클릭 후 대기(초)", tag="set.post_click_delay",
                                default_value=0.5, width=120, step=0, format="%.2f")
            dpg.add_input_int(label="재클릭 횟수", tag="set.max_click_retries",
                              default_value=3, width=120)
            dpg.add_input_text(label="정지 단축키", tag="set.stop_key", default_value="f12", width=120)
            dpg.add_separator()
            dpg.add_button(label="닫기", callback=lambda: dpg.hide_item("settingswin"))

    def build_dialogs(self):
        with dpg.file_dialog(directory_selector=False, show=False, tag="savedlg",
                             callback=self._on_save_file, width=640, height=420,
                             default_path=BASE, default_filename="flow"):
            dpg.add_file_extension(".json")
        with dpg.file_dialog(directory_selector=False, show=False, tag="opendlg",
                             callback=self._on_open_file, width=640, height=420,
                             default_path=BASE):
            dpg.add_file_extension(".json")

    def _setup_font(self):
        """DearPyGui 기본 폰트는 한글 글리프가 없어 깨진다. 한글 글꼴을 기본으로 바인딩.
        (DPG 2.x 는 폰트의 글리프 범위를 자동 포함하므로 등록 후 바인딩만 하면 된다.)"""
        cands = [r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\malgunsl.ttf",
                 r"C:\Windows\Fonts\gulim.ttc", r"C:\Windows\Fonts\batang.ttc",
                 r"C:\Windows\Fonts\NanumGothic.ttf"]
        path = next((p for p in cands if os.path.exists(p)), None)
        if not path:
            return
        with dpg.font_registry():
            kfont = dpg.add_font(path, 17)
        dpg.bind_font(kfont)

    def build(self):
        self._setup_font()
        self.build_dialogs()
        self.build_settings_window()

        with dpg.window(tag="main", menubar=True):
            with dpg.menu_bar():
                with dpg.menu(label="파일"):
                    dpg.add_menu_item(label="새로 만들기",
                                      callback=lambda: self.clear() or self.add_start_node())
                    dpg.add_menu_item(label="열기...", callback=self.menu_open)
                    dpg.add_menu_item(label="저장", callback=self.menu_save)
                    dpg.add_menu_item(label="다른 이름으로 저장...", callback=self.menu_save_as)
                with dpg.menu(label="추가"):
                    dpg.add_menu_item(label="+ 페이즈 노드", callback=lambda: self.add_phase_node())
                    dpg.add_menu_item(label="+ 종료 노드", callback=lambda: self.add_end_node())
                dpg.add_menu_item(label="전역 설정", callback=lambda: dpg.show_item("settingswin"))
                dpg.add_menu_item(label="선택 삭제(Del)", callback=self._delete_selected)
                dpg.add_menu_item(label="▶ 실행", callback=lambda: self.run_macro(False))
                dpg.add_menu_item(label="미리보기(클릭 없음)", callback=lambda: self.run_macro(True))

            dpg.add_text("준비됨. [추가] 메뉴로 노드를 만들고 포트를 선으로 연결하세요.",
                         tag="statusbar")
            with dpg.node_editor(tag="editor", callback=self._on_link,
                                 delink_callback=self._on_delink, minimap=True,
                                 minimap_location=dpg.mvNodeMiniMap_Location_BottomRight):
                self.add_start_node()

        with dpg.handler_registry():
            dpg.add_key_press_handler(dpg.mvKey_Delete, callback=self._delete_selected)

    def main_loop(self):
        while dpg.is_dearpygui_running():
            if self.run_proc and self.run_proc.poll() is not None:
                self.run_proc = None
                restore_window()
                self._status("실행 종료. (로그 창에서 결과 확인)")
            dpg.render_dearpygui_frame()


def selftest():
    """UI 구성이 오류 없이 되는지 자동 점검 (창 표시 후 몇 프레임 렌더).
    windowed exe 는 콘솔이 없으므로 결과를 파일에도 기록한다."""
    result_path = os.path.join(BASE, "_selftest_result.txt")
    try:
        dpg.create_context()
        ed = Editor()
        ed.build()
        n1 = ed.add_phase_node({"name": "테스트", "match": "text", "target": "확인"})
        ed.add_end_node({"result": "success"})
        dpg.create_viewport(title=TITLE, width=900, height=600)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("main", True)
        for _ in range(5):
            dpg.render_dearpygui_frame()
        # 링크 생성 경로까지 점검
        ed._on_link(None, (dpg.get_alias_id("start.out"), dpg.get_alias_id(f"{n1}.in")))
        data = ed.serialize()
        dpg.destroy_context()
        assert any(x["id"] == n1 for x in data["nodes"])
        assert any(x["type"] == "end" for x in data["nodes"])
        assert len(data["links"]) == 1
        msg = "SELFTEST OK nodes=%d links=%d" % (len(data["nodes"]), len(data["links"]))
    except Exception as e:
        import traceback
        msg = "SELFTEST FAIL: %r\n%s" % (e, traceback.format_exc())
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(msg)
    print(msg)


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    dpg.create_context()
    ed = Editor()
    ed.build()
    dpg.create_viewport(title=TITLE, width=1200, height=760)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("main", True)
    ed.main_loop()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
