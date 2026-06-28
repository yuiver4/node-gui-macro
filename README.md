# node-gui-macro

윈도우용 **이미지/텍스트 인식 자동 클릭 매크로**.
화면에서 버튼 이미지나 글자(한글/영문 OCR)를 인식해 클릭하고, 클릭 후 그 대상이
사라졌는지 검증한 뒤 다음 단계로 넘어갑니다. 흐름은 **노드 그래프 GUI**(Shader
Graph 스타일)로 만들고, 각 단계의 **성공/실패 분기**를 선으로 연결합니다.

> 코드 한 줄, 명령어 한 줄 없이 GUI로 매크로를 만들 수 있습니다.
> 한글 OCR 엔진(Tesseract)을 내장해 **설치 없이 다른 PC에서도** 동작합니다.

```
[시작] ─▶ [로그인 버튼]──성공──▶ [확인]──성공──▶ [완료(성공 종료)]
                  └────실패────▶ [에러 닫기]──성공──▶ [로그인 버튼] (재시도)
```

## ✨ 특징

- **노드 그래프 에디터** — 페이즈를 노드로 만들고 선으로 흐름 연결, 성공/실패 분기
- **노드 종류** — 페이즈(인식·클릭) / **반복(횟수)** / **딜레이** / 종료
- **대상 창/프로세스 지정** — 전체화면 대신 특정 창만 캡처·클릭(가볍고 정확, 창 따라감)
- **키매핑(앱플레이어식)** — 단축키를 누르면 대상 창의 비율 위치를 클릭 (매크로와 별개)
- **두 가지 인식 방식**
  - 이미지: OpenCV 템플릿 매칭(유사도 임계값)
  - 텍스트: Tesseract OCR 로 한글/영문 인식 (찾을 문구만 입력)
- **클릭 → 사라짐 검증 → 다음** 자동 흐름 (실패 시 지정한 노드로 우회/재시도)
- **드래그 캡처** — 노드의 캡처 버튼으로 화면 영역을 끌어 템플릿 자동 등록
- **실행 중 현재 노드 하이라이트** — 에디터를 켜둔 채 진행 상황을 색으로 표시
- **편의 기능** — 우클릭으로 노드 추가, 마우스 휠 확대/축소, `[한글]` 입력 버튼
- **배율이 다른 PC 대응** — 이미지는 멀티스케일 매칭, 텍스트는 OCR(배율 무관)
- **한글 경로/파일명 지원** — 유니코드 안전 이미지 IO
- **비상 정지** — 마우스 좌상단 이동 / F12 / Ctrl+C
- **단독 실행 exe** — 파이썬·Tesseract 설치 불필요(한글 OCR 내장)

> 참고: DearPyGui 입력칸은 한글 IME 직접 타이핑이 깨질 수 있어, 노드의 `[한글]`
> 버튼으로 네이티브 입력창을 띄워 한글을 입력합니다.

## 🚀 빠른 시작 (배포용 exe)

빌드된 배포본(`release/ImgMacro.zip`)의 압축을 풀고 **`Editor.exe`** 를 실행하세요.

1. `Editor.exe` 실행 → 빈 곳 **우클릭**(또는 상단 `[추가]`)으로 노드 추가
2. 노드에서 인식 방식 선택
   - **이미지**: `캡처` 누르고 화면에서 버튼을 드래그
   - **텍스트**: 찾을 문구 입력 (한글은 `[한글]` 버튼)
3. **성공 > / 실패 >** 포트를 다음 노드의 **이전** 으로 드래그해 연결
4. 종료 노드로 성공/실패 종료점 배치 (필요하면 반복·딜레이 노드도)
5. **[파일] → 저장**(.json) 후 상단 **[▶ 실행]** (3초 뒤 시작, 현재 노드 하이라이트)

> exe 는 용량(약 213MB, OCR 엔진 포함) 때문에 저장소에 포함하지 않습니다.
> 아래 **빌드** 절을 따라 직접 만들거나, GitHub Releases 에서 받으세요.
> 사용자용 상세 안내는 [`README_release.txt`](README_release.txt) 참고.

## 🧩 노드와 흐름

각 **페이즈 노드**는 다음을 수행합니다.

```
대상 인식 ──▶ 클릭 ──▶ (옵션) 사라졌는지 확인
   성공 → '성공 ▶' 선을 따라 다음 노드
   실패 → '실패 ▶' 선을 따라 지정한 노드 (다른 페이즈 / 재시도 / 종료)
```

노드별 설정: 정확도, 사라짐 확인 여부, 제한시간(초), 재클릭 횟수, 🔲 검색영역 제한.
**종료 노드**(성공/실패)에 도달하면 매크로가 끝납니다.

## 🛠 소스로 실행 (개발자)

```bash
pip install -r requirements.txt
python editor.py            # GUI 에디터
python macro.py flow.json   # 저장한 흐름 직접 실행
```

텍스트(OCR) 인식을 쓰려면 [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki)
설치가 필요합니다(설치 시 **Korean** 언어 데이터 체크). exe 빌드본에는 내장됩니다.

### CLI (`macro.py`)

```bash
python macro.py flow.json            # 그래프(.json) 실행
python macro.py config.yaml          # 선형(.yaml) 실행 (구버전 호환)
python macro.py flow.json --dry-run  # 클릭 없이 인식만 확인
python macro.py --save-debug         # 인식 위치를 debug/ 에 그려 저장
python macro.py --list-monitors      # 모니터 번호 확인
python macro.py --probe-text "확인"   # 화면 텍스트 인식 점수 확인
python macro.py --probe-image t.png  # 이미지 인식 점수 확인
```

## 📦 빌드 (단독 실행 exe 만들기)

PyInstaller 로 3개 exe(Editor/ImgMacro/Snip)를 만들고 한글 OCR 을 내장합니다.

**1) 번들용 Tesseract 준비 (최초 1회)** — `_build/tesseract/` 에 아래를 둡니다.
- [UB Mannheim 설치본](https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.4.0.20240606.exe)
  설치 후 설치 폴더에서 `tesseract.exe` + 모든 `*.dll` + `tessdata/eng.traineddata` 복사
- 한글 데이터를 `_build/tesseract/tessdata/kor.traineddata` 로 저장
  ([tessdata_fast/kor](https://github.com/tesseract-ocr/tessdata_fast/raw/main/kor.traineddata))

**2) 빌드**
```bash
pip install pyinstaller
python build.py
```
→ `release/ImgMacro/` 폴더와 `release/ImgMacro.zip` 생성.

## 🗂 프로젝트 구조

```
.
├── editor.py          # 노드 그래프 GUI 에디터 (DearPyGui) — 메인
├── macro.py           # 매크로 실행 엔진 (그래프 .json + 선형 .yaml)
├── snip.py            # 화면 영역 캡처 도구
├── build.py           # 3개 exe 빌드 + 배포본 패키징
├── rthook_silence.py  # PyInstaller 런타임 훅(경고 억제)
├── config.yaml        # 선형(.yaml) 설정 예시
├── requirements.txt
├── README_release.txt # 배포본 동봉 사용 설명
└── templates/         # 캡처 이미지 저장 폴더(.gitkeep)
```

> `_build/`, `release/`, `templates/*.png` 등 대용량 산출물·사용자 데이터는
> [`.gitignore`](.gitignore) 로 제외됩니다.

## ⚙️ 동작 방식 요약

| 구성 | 사용 기술 |
|---|---|
| 화면 캡처 | `mss` (멀티모니터, DPI 인식) |
| 이미지 매칭 | `OpenCV` `matchTemplate` + 멀티스케일 |
| 텍스트 인식 | `Tesseract OCR` (`kor+eng`, PSM 6) |
| 클릭 | `pyautogui` (FAILSAFE on) |
| GUI | `DearPyGui` 노드 에디터 |
| 패키징 | `PyInstaller` (onefile) |

## 📄 라이선스

[MIT](LICENSE) © yuiver4
