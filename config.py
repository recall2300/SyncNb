"""
config.py — 환경변수 로드 및 전역 설정

.env 파일 또는 환경변수에서 값을 읽는다.
Docker 배포 시에는 docker-compose.yml의 env_file 또는 environment 섹션으로 주입한다.

필수 설정 (.env에 반드시 지정):
  STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET  — Strava API 앱 자격증명
  FLASK_SECRET_KEY                        — 세션 서명 키 (랜덤 긴 문자열)

선택 설정 (기본값 있음):
  STRAVA_REDIRECT_URI  — 외부 배포 시 실제 도메인으로 변경 필요
  ADMIN_USERNAME/PASSWORD — 첫 실행 시 생성될 관리자 계정
  HTTPS_ENABLED        — HTTPS 환경에서 true로 설정 (세션 쿠키 Secure 플래그)
  ALLOW_REGISTRATION   — false로 설정 시 /register 비활성화
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 프로젝트 루트의 .env 파일을 자동으로 로드
load_dotenv()

# ── 경로 설정 ───────────────────────────────────────────────────────────────────

# 이 파일이 있는 디렉토리 = 프로젝트 루트
BASE_DIR = Path(__file__).resolve().parent

# SQLite DB 파일 경로. Docker 배포 시 볼륨으로 마운트해 데이터를 유지한다.
DB_PATH = BASE_DIR / "syncnb.db"

# Garmin 세션 토큰 저장 루트 디렉토리.
# 실제 저장 경로는 {GARMIN_TOKEN_STORE}/{user_id}/ (garmin_client.py 참조).
# Docker 배포 시 named volume을 이 경로로 마운트해 컨테이너 재시작 후에도 로그인 유지.
# 환경변수 GARMIN_TOKEN_STORE로 경로를 변경할 수 있다 (기본: ~/.garminconnect).
GARMIN_TOKEN_STORE = os.getenv(
    "GARMIN_TOKEN_STORE",
    str(Path.home() / ".garminconnect"),
)

# ── Strava API 설정 ─────────────────────────────────────────────────────────────
# 앱 등록: https://www.strava.com/settings/api

STRAVA_CLIENT_ID     = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")

# 외부 배포 시 반드시 실제 도메인으로 변경:
#   STRAVA_REDIRECT_URI=https://yourdomain.com/strava/callback
# Strava 앱 설정의 "Authorization Callback Domain"도 같은 도메인으로 설정해야 함.
STRAVA_REDIRECT_URI = os.getenv("STRAVA_REDIRECT_URI", "http://localhost:5000/strava/callback")

# Strava API 엔드포인트
# 2027-06-01부터 API base URL이 https://www.api-v3.strava.com 으로 변경됨.
# 전환 시 STRAVA_API_BASE 값만 바꾸면 모든 엔드포인트에 반영된다.
STRAVA_AUTH_URL  = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE  = "https://www.strava.com/api/v3"
STRAVA_UPLOAD_URL = f"{STRAVA_API_BASE}/uploads"

# ── Flask 설정 ──────────────────────────────────────────────────────────────────

# 세션 쿠키 서명 키. 반드시 랜덤한 긴 문자열로 변경해야 한다.
# 생성 방법: python -c "import secrets; print(secrets.token_hex(32))"
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "syncnb-dev-key-change-me")

# ── 동기화 동작 설정 ────────────────────────────────────────────────────────────

# /activities API 기본 반환 수
GARMIN_ACTIVITY_LIMIT = 30

# Strava 업로드 완료 폴링 설정
# 최대 시도 횟수 × 간격(초) = 최대 대기 시간: 15 × 2.0 = 30초
STRAVA_POLL_RETRIES  = 15
STRAVA_POLL_INTERVAL = 2.0  # 초

# ── 관리자 계정 설정 ────────────────────────────────────────────────────────────
# DB에 사용자가 없을 때 첫 실행 시 이 값으로 관리자 계정을 자동 생성한다.
# 반드시 .env에서 강한 비밀번호로 변경할 것.

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "syncnb-admin")

# ── 보안 설정 ───────────────────────────────────────────────────────────────────

# HTTPS 배포 시 true로 설정: 세션 쿠키에 Secure 플래그가 추가돼 HTTPS에서만 전송된다.
# 로컬 개발(HTTP)에서 true로 설정하면 로그인이 작동하지 않으니 주의.
HTTPS_ENABLED = os.getenv("HTTPS_ENABLED", "false").lower() == "true"

# false 설정 시 /register 엔드포인트가 403을 반환한다.
# 공개 인터넷 배포 시 false 권장 — 관리자 패널에서 직접 계정을 생성한다.
ALLOW_REGISTRATION = os.getenv("ALLOW_REGISTRATION", "false").lower() == "true"
