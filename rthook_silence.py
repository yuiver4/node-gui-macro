# PyInstaller 런타임 훅: 부트스트랩 단계에서 발생하는 비핵심 경고(pkg_resources 등)를
# 가장 먼저 차단한다. (자동 생성 훅보다 먼저 실행됨)
import warnings
warnings.filterwarnings("ignore")
