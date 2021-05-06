# MODI Firmware Updater
[![Python Versions](https://badgen.net/badge/python/3/9cf)](https://pypi.python.org/pypi/modi-firmware-updater)
[![PyPI Release (latest by date)](https://img.shields.io/github/v/release/LUXROBO/modi-firmware-updater?style=flat&color=F3E2A9)](https://pypi.python.org/pypi/modi-firmware-updater)
[![GitHub LICENSE](https://img.shields.io/github/license/LUXROBO/modi-firmware-updater?style=flat&color=pink)](https://github.com/LUXROBO/modi-firmware-updater/blob/master/LICENSE)
[![Lines of Code](https://img.shields.io/tokei/lines/github/LUXROBO/modi-firmware-updater?style=flat&color=yellowgreen)](https://github.com/LUXROBO/modi-firmware-updater/tree/master/modi_firmware_updater)

프로젝트 설명
--
`PyQt5` 프론트엔드와 `PyMODI` 백엔드를 연동하여 `GUI MODI Firmware Updater`를 구현

실행 준비
--
1. `python3`(파이썬3.6 혹은 그 이상의 버전)를 컴퓨터에 설치
2. `git clone https://github.com/LUXROBO/modi-firmware-updater.git` 커맨드를 실행하여 이 `repository`를 클론
3. `python3 -m pip install -r requirements.txt`로 의존성 패키지들을 설치

실행 방법 (개발자용)
--
`python3 main.py --mode native`로 GUI 프로그램을 실행한다.

실행파일 생성 (수동)
--
1. `python3 bootstrap.py` 커맨드를 실행하여 정의한 `spec` 파일을 기반으로 실행파일을 생성
2. `dist` 폴더 내 `modi_updater.exe` 혹은 `modi_updater.app` 실행파일이 생성된것을 확인

실행파일 생성 (자동)
--
1. 깃헙 내에 새로운 릴리즈 생성, 생성시 제목과 내용은 `v*` (e.g. v1.0.0) 의 형식으로 작성
2. 해당 릴리즈 페이지에 첨부파일로 Windows 와 macOS용 실행파일이 자동으로 첨부된것을 확인

실행파일 다운로드 (사용자용)
--
1. [릴리즈 페이지](https://github.com/LUXROBO/modi-firmware-updater/releases)에서 최신 릴리즈의 첨부파일 확인
2. 운영체제에 따라 `modi_updater.exe` 혹은 `modi_updater.app` 실행파일을 다운로드 후 실행

추천 링크
--
1. [프론트엔드로 사용된 PyQt5 문법에 대한 문서](https://wikidocs.net/book/2944)
2. [백엔드로 사용된 PyMODI 공식 페이지](https://github.com/luxrobo/pymodi)

주의 사항
--
1. 윈도우 환경에서는 권한 문제가 발생할 수 있으므로 가능하다면 관리자 권한으로 위의 프로세스를 실행
