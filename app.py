"""
app.py — SyncNb 메인 Flask 애플리케이션

모든 HTTP 라우트와 인증 데코레이터를 정의한다.
Garmin→Strava 동기화 API, 사용자 인증, 관리자 패널 API를 포함한다.

실행 방법:
  개발: python app.py  (debug=False, 포트 5000)
  프로덕션: gunicorn app:app  (Dockerfile CMD 참조)
"""

import logging
import os
import re
import secrets
import traceback
import urllib.parse
from functools import wraps

from flask import Flask, jsonify, redirect, render_template, request, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import config
import garmin_client
import scheduler
import strava_client
import sync_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY
if config.FLASK_SECRET_KEY == "syncnb-dev-key-change-me":
    logging.critical(
        "[보안 위험] FLASK_SECRET_KEY가 기본값입니다! 세션 쿠키 위조 공격에 취약합니다. "
        ".env에 다음 명령 결과를 설정하세요: "
        "python -c \"import secrets; print(secrets.token_hex(32))\""
    )

# ── ProxyFix — Nginx 역방향 프록시 뒤에서 실제 클라이언트 IP 복원 ──────────────
# Nginx → Flask 연결 시 Flask가 보는 remote_addr은 Nginx 내부 IP (172.x.x.x).
# ProxyFix를 적용해야 X-Forwarded-For 헤더에서 실제 IP를 읽어 rate limiting이
# 정상 작동한다. x_for=1 은 신뢰할 프록시 홉 수 (Nginx 1개).
# 직접 실행(Nginx 없음)에서는 아무 영향 없음.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ── 세션 쿠키 보안 설정 ────────────────────────────────────────────────────────
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,               # JS에서 document.cookie 접근 불가 (XSS 피해 최소화)
    SESSION_COOKIE_SAMESITE="Lax",              # 외부 사이트 요청 시 쿠키 전송 제한 (CSRF 부분 방어)
    SESSION_COOKIE_SECURE=config.HTTPS_ENABLED, # HTTPS 환경에서만 쿠키 전송. .env에서 HTTPS_ENABLED=true 설정
    PERMANENT_SESSION_LIFETIME=86400,           # 세션 만료: 24시간 (초)
    # 요청 바디 크기 제한: Nginx 없이 직접 실행 시에도 메모리 OOM 방지
    # FIT 파일은 보통 1~5MB. 16MB면 충분하고 대용량 payload 폭탄 차단.
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,        # 16 MB
)

# ── Rate Limiter (브루트포스 / 스팸 방지) ────────────────────────────────────────
# storage_uri="memory://": 단일 프로세스 내 메모리 저장소 사용
# 주의: Gunicorn workers=1 이상이면 프로세스 간 공유가 안 돼 제한이 희석됨.
#       Dockerfile에서 workers=1로 고정해 이 문제를 방지한다.
limiter = Limiter(
    app=app,
    key_func=get_remote_address,  # IP 주소 기준으로 요청 수 카운트
    default_limits=[],            # 기본 제한 없음 — 각 엔드포인트에 명시
    storage_uri="memory://",
)

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"error": f"요청이 너무 많습니다. 잠시 후 다시 시도해주세요. ({e.description})"}), 429

sync_db.init_db()

# 로그인 타이밍 공격 방어용 더미 해시.
# 존재하지 않는 사용자명 요청 시에도 항상 bcrypt 계산을 수행해
# 응답 시간으로 유효 사용자명을 열거하는 공격을 막는다.
_DUMMY_HASH = generate_password_hash("__dummy_syncnb__")

# Werkzeug reloader는 프로세스를 두 개(부모/자식) 띄운다.
# 자식 프로세스(WERKZEUG_RUN_MAIN=true)에서만 스케줄러를 시작해 중복 실행을 방지.
if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    scheduler.start()

# 첫 실행 시 기본 관리자 계정 자동 생성 (DB에 사용자가 하나도 없을 때만)
if not sync_db.user_exists():
    sync_db.create_user(
        username=config.ADMIN_USERNAME,
        password_hash=generate_password_hash(config.ADMIN_PASSWORD),
        is_admin=True,
    )
    logging.info("기본 관리자 계정 생성: %s", config.ADMIN_USERNAME)


# ── 보안 응답 헤더 ──────────────────────────────────────────────────────────────

@app.after_request
def add_security_headers(response):
    """
    모든 HTTP 응답에 보안 헤더를 추가한다.

    - CSP: XSS 공격 차단. 허용된 출처 이외의 스크립트/스타일 실행을 막는다.
    - X-Content-Type-Options: 브라우저의 MIME 타입 추측(sniffing)을 막는다.
    - X-Frame-Options: 다른 사이트에서 iframe으로 임베드(클릭재킹) 방지.
    - Referrer-Policy: 외부 링크 클릭 시 노출되는 URL 정보를 제한한다.
    """
    response.headers.setdefault("Content-Security-Policy", (
        "default-src 'self'; "
        # 'unsafe-inline' 허용: 현재 HTML 템플릿에 인라인 <script>/<style>이 있어 필요
        # 향후 외부 .js 파일로 이전하면 'unsafe-inline' 제거 가능
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        # unpkg.com: Leaflet CSS 로드에 필요 (leaflet.css)
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        # OpenStreetMap 타일: Leaflet 지도 표시용
        "img-src 'self' data: https://*.tile.openstreetmap.org; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"  # X-Frame-Options 대체 (최신 브라우저)
    ))
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # DENY: CSP frame-ancestors 'none'과 일치. SAMEORIGIN과 모순을 방지.
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # 이 앱은 카메라/마이크/위치정보를 사용하지 않으므로 모두 차단
    response.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), camera=(), microphone=(), payment=()"
    )
    return response


# ── 인증 데코레이터 ─────────────────────────────────────────────────────────────

def _wants_json():
    """클라이언트가 JSON 응답을 기대하는 요청인지 판별."""
    return (
        request.is_json
        or "application/json" in request.headers.get("Accept", "")
        or request.path.startswith("/admin/api")
    )


def login_required(f):
    """
    로그인 확인 데코레이터.
    세션에 user_id가 없으면:
      - 일반 페이지 요청: /login으로 리다이렉트
      - JSON/API 요청: 401 반환
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            if _wants_json():
                return jsonify({"error": "로그인이 필요합니다."}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """
    관리자 권한 확인 데코레이터.
    로그인은 됐지만 is_admin이 False면 403 반환.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            if _wants_json():
                return jsonify({"error": "로그인이 필요합니다."}), 401
            return redirect("/login")
        if not session.get("is_admin"):
            return jsonify({"error": "관리자 권한이 필요합니다."}), 403
        return f(*args, **kwargs)
    return decorated


def current_user_id() -> int:
    """
    현재 로그인한 사용자의 DB id를 반환한다.

    반드시 @login_required 또는 @admin_required 데코레이터 이후에만 호출해야 한다.
    세션에 user_id가 없으면 KeyError가 발생한다.
    """
    return session["user_id"]


# ── 인증 라우트 ─────────────────────────────────────────────────────────────────

@app.route("/login")
def login_page():
    """로그인 페이지 반환. 이미 로그인된 경우 메인 페이지(/)로 리다이렉트."""
    if session.get("user_id"):
        return redirect("/")
    return render_template("login.html")


@app.route("/login", methods=["POST"])
@limiter.limit("10 per minute; 30 per hour")
def login():
    """
    아이디/비밀번호 로그인 처리.

    Request JSON : {"username": str, "password": str}
    Response 200 : {"ok": true, "is_admin": bool}
    Response 401 : {"error": str}  — 아이디 또는 비밀번호 불일치
    """
    body = request.get_json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = sync_db.get_user_by_username(username)
    stored_hash = user["password_hash"] if user else _DUMMY_HASH
    if not check_password_hash(stored_hash, password) or not user:
        return jsonify({"error": "아이디 또는 비밀번호가 틀렸습니다."}), 401
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["is_admin"] = user["is_admin"]
    return jsonify({"ok": True, "is_admin": user["is_admin"]})


@app.route("/register", methods=["POST"])
@limiter.limit("5 per hour")
def register():
    """
    신규 계정 생성.
    ALLOW_REGISTRATION=false(.env 기본값)이면 비활성화된다.
    공개 인터넷 배포 시에는 false 권장 — 관리자 패널에서 직접 계정 생성.

    Request JSON : {"username": str, "password": str}
    Response 200 : {"ok": true}
    Response 400 : {"error": str}  — 유효성 검사 실패
    Response 403 : {"error": str}  — 회원가입 비활성화
    """
    if not config.ALLOW_REGISTRATION:
        return jsonify({"error": "현재 회원가입이 비활성화되어 있습니다. 관리자에게 문의하세요."}), 403
    body = request.get_json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if len(username) < 3:
        return jsonify({"error": "아이디는 3자 이상이어야 합니다."}), 400
    if len(password) < 8:
        return jsonify({"error": "비밀번호는 8자 이상이어야 합니다."}), 400
    if sync_db.get_user_by_username(username):
        return jsonify({"error": "이미 사용 중인 아이디입니다."}), 400
    try:
        sync_db.create_user(username, generate_password_hash(password))
        return jsonify({"ok": True})
    except Exception:
        logging.error("회원가입 오류:\n%s", traceback.format_exc())
        return jsonify({"error": "계정 생성 중 오류가 발생했습니다."}), 400


@app.route("/logout", methods=["POST"])
def logout():
    """세션을 완전히 삭제하고 로그아웃."""
    session.clear()
    return jsonify({"ok": True})


@app.route("/me")
@login_required
def me():
    """
    현재 로그인한 사용자 정보와 연결 상태를 반환한다.

    Response: {
        "username": str,
        "is_admin": bool,
        "garmin_connected": bool,
        "strava_connected": bool,
        "auto_sync_enabled": bool
    }
    """
    auto_sync_setting = sync_db.get_auto_sync_setting(current_user_id())
    return jsonify({
        "username":          session["username"],
        "is_admin":          session.get("is_admin", False),
        "garmin_connected":  garmin_client.is_logged_in(current_user_id()),
        "strava_connected":  strava_client.is_connected(current_user_id()),
        "auto_sync_enabled": auto_sync_setting["auto_sync_enabled"],
    })


@app.route("/auto-sync", methods=["POST"])
@login_required
def toggle_auto_sync():
    """
    자동 동기화 활성화/비활성화.
    활성화 시 스케줄러가 2분마다 새 러닝 활동을 자동 업로드한다.

    Request JSON : {"enabled": bool}
    Response 200 : {"ok": true, "auto_sync_enabled": bool}
    """
    body = request.get_json()
    enabled = bool(body.get("enabled")) if body else False
    sync_db.set_auto_sync(current_user_id(), enabled)
    logging.info("Auto-sync %s: user_id=%s", "활성화" if enabled else "비활성화", current_user_id())
    return jsonify({"ok": True, "auto_sync_enabled": enabled})


@app.route("/me/password", methods=["POST"])
@login_required
def change_password():
    """
    비밀번호 변경. 현재 비밀번호가 맞아야 변경 가능.
    변경 성공 시 현재 세션을 무효화해 재로그인을 요구한다.
    (기존 쿠키가 탈취된 경우에도 비밀번호 변경으로 공격자를 차단할 수 있음)

    Request JSON : {"current": str, "new": str}
    Response 200 : {"ok": true}
    Response 400 : {"error": str}
    """
    body = request.get_json()
    current_pw = body.get("current") or ""
    new_pw = body.get("new") or ""
    user = sync_db.get_user_by_id(current_user_id())
    if not check_password_hash(user["password_hash"] if user else "", current_pw):
        return jsonify({"error": "현재 비밀번호가 틀렸습니다."}), 400
    if len(new_pw) < 8:
        return jsonify({"error": "새 비밀번호는 8자 이상이어야 합니다."}), 400
    sync_db.update_user_password(current_user_id(), generate_password_hash(new_pw))
    # 비밀번호 변경 후 세션 초기화 → 재로그인 요구
    # 탈취된 쿠키가 있더라도 비밀번호 변경으로 공격자를 즉시 차단한다.
    session.clear()
    return jsonify({"ok": True})


# ── 페이지 라우트 ──────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    """메인 페이지 (활동 목록 + 동기화 UI)."""
    return render_template("index.html")


@app.route("/admin")
@admin_required
def admin_page():
    """관리자 패널 페이지 (사용자 관리)."""
    return render_template("admin.html")


# ── 상태 확인 ──────────────────────────────────────────────────────────────────

@app.route("/status")
@login_required
def status():
    """Garmin / Strava 연결 상태를 반환한다."""
    return jsonify({
        "garmin_connected": garmin_client.is_logged_in(current_user_id()),
        "strava_connected": strava_client.is_connected(current_user_id()),
    })


# ── Garmin 인증 라우트 ─────────────────────────────────────────────────────────

@app.route("/garmin/login", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def garmin_login():
    """
    Garmin Connect 이메일/비밀번호 로그인.
    MFA가 필요한 경우 mfa_required: true를 반환하며,
    클라이언트는 /garmin/mfa 로 코드를 제출해야 한다.

    Request JSON : {"email": str, "password": str}
    Response 200 : {"ok": true}  또는  {"mfa_required": true}
    Response 400 : {"error": str}
    """
    body = request.get_json()
    if not body or not body.get("email") or not body.get("password"):
        return jsonify({"error": "이메일과 비밀번호를 입력해주세요."}), 400
    try:
        login_success, _ = garmin_client.login_with_credentials(
            current_user_id(), body["email"], body["password"]
        )
        return jsonify({"ok": True} if login_success else {"mfa_required": True})
    except Exception as exc:
        logging.warning("Garmin 로그인 실패 (user_id=%s): %s", current_user_id(), exc)
        return jsonify({"error": "Garmin 로그인에 실패했습니다. 이메일과 비밀번호를 확인해주세요."}), 400


@app.route("/garmin/mfa", methods=["POST"])
@login_required
@limiter.limit("20 per 5 minutes")
def garmin_mfa():
    """
    Garmin MFA(2단계 인증) 코드 제출. /garmin/login 에서 mfa_required: true를 받은 후 호출.

    Request JSON : {"code": str}  — 이메일/앱으로 받은 6~8자리 코드
    Response 200 : {"ok": true}
    Response 400 : {"error": str}
    """
    if not garmin_client.has_pending_mfa(current_user_id()):
        return jsonify({"error": "진행 중인 MFA 세션이 없습니다. 다시 로그인해주세요."}), 400
    body = request.get_json()
    if not body or not body.get("code"):
        return jsonify({"error": "인증 코드를 입력해주세요."}), 400
    try:
        garmin_client.resume_mfa(current_user_id(), body["code"])
        return jsonify({"ok": True})
    except Exception as exc:
        logging.warning("Garmin MFA 실패 (user_id=%s): %s", current_user_id(), exc)
        return jsonify({"error": "MFA 인증에 실패했습니다. 코드를 다시 확인해주세요."}), 400


@app.route("/garmin/logout", methods=["POST"])
@login_required
def garmin_logout():
    """Garmin 세션 토큰 파일을 삭제한다. 재로그인 시까지 Garmin 기능 비활성화."""
    garmin_client.logout(current_user_id())
    return jsonify({"ok": True})


# ── Strava OAuth 라우트 ────────────────────────────────────────────────────────

@app.route("/strava/connect")
@login_required
def strava_connect():
    """
    Strava OAuth 인증 흐름 시작.

    CSRF 방지를 위해 랜덤 state 토큰을 세션에 저장한 후
    Strava 인증 페이지로 리다이렉트한다.
    Strava는 인증 완료 후 state를 콜백 URL에 포함해 돌려보낸다.
    """
    if not config.STRAVA_CLIENT_ID or not config.STRAVA_CLIENT_SECRET:
        return redirect(f"/?strava_error={urllib.parse.quote_plus('.env에 STRAVA 설정이 없습니다.')}")
    # CSRF 방지용 state 토큰: 콜백에서 이 값과 대조해 요청의 진위를 확인
    oauth_state = secrets.token_urlsafe(16)
    session["strava_oauth_state"] = oauth_state
    return redirect(strava_client.get_auth_url(oauth_state))


@app.route("/strava/callback")
@login_required
def strava_callback():
    """
    Strava OAuth 콜백. 인증 완료 후 Strava가 이 URL로 리다이렉트한다.

    처리 순서:
      1. state 검증 (CSRF 방어)
      2. 에러 파라미터 확인 (사용자가 Strava에서 거부한 경우)
      3. code → access_token 교환, DB 저장
    """
    # CSRF 검증: 세션에 저장한 state와 Strava가 돌려보낸 state가 반드시 일치해야 함
    expected_state = session.pop("strava_oauth_state", None)
    received_state = request.args.get("state")
    if not expected_state or expected_state != received_state:
        return redirect(f"/?strava_error={urllib.parse.quote_plus('보안 검증 실패. 다시 시도해주세요.')}")

    error = request.args.get("error")
    if error:
        if error == "access_denied":
            msg = "Strava 연동이 취소되었습니다."
        else:
            logging.warning("Strava OAuth 오류 (user_id=%s): %s", current_user_id(), error)
            msg = "Strava 연동 중 오류가 발생했습니다. 다시 시도해주세요."
        return redirect(f"/?strava_error={urllib.parse.quote_plus(msg)}")

    code = request.args.get("code")
    if not code:
        return redirect(f"/?strava_error={urllib.parse.quote_plus('인증 코드가 없습니다.')}")

    try:
        strava_client.exchange_code(current_user_id(), code)
        return redirect("/?strava_ok=1")
    except Exception as exc:
        logging.error("Strava 코드 교환 실패 (user_id=%s): %s", current_user_id(), exc)
        return redirect(f"/?strava_error={urllib.parse.quote_plus('Strava 연동 중 오류가 발생했습니다. 다시 시도해주세요.')}")


@app.route("/strava/disconnect", methods=["POST"])
@login_required
def strava_disconnect():
    """Strava 연결 해제. DB의 access_token / refresh_token을 삭제한다."""
    sync_db.delete_strava_tokens(current_user_id())
    return jsonify({"ok": True})


# ── 활동 목록 / 상세 라우트 ────────────────────────────────────────────────────

@app.route("/activities")
@login_required
@limiter.limit("60 per minute")
def activities():
    """
    최근 Garmin 러닝 활동 목록을 반환한다.
    동기화 여부(synced)와 연결된 Strava 활동 ID(strava_activity_id)를 포함한다.
    러닝이 아닌 활동(사이클, 수영 등)은 필터링해 제외한다.

    Query params:
        limit  (int, 1-100, 기본 GARMIN_ACTIVITY_LIMIT)
        offset (int, 0 이상, 기본 0)

    Response: {
        "activities": [...],
        "has_more": bool  — 다음 페이지 존재 여부
    }
    """
    try:
        limit  = max(1, min(int(request.args.get("limit",  config.GARMIN_ACTIVITY_LIMIT)), 100))
        offset = max(0,     int(request.args.get("offset", 0)))
    except (ValueError, TypeError):
        limit, offset = config.GARMIN_ACTIVITY_LIMIT, 0

    try:
        # limit+1개 요청 → 반환 수가 limit보다 많으면 다음 페이지 있음
        garmin_activities = garmin_client.get_recent_activities(current_user_id(), limit + 1, offset)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 401

    has_more = len(garmin_activities) > limit
    garmin_activities = garmin_activities[:limit]

    synced_map = sync_db.get_synced_map(current_user_id())
    result = []
    for act in garmin_activities:
        activity_type = act.get("activityType")
        type_key = activity_type.get("typeKey", "") if isinstance(activity_type, dict) else str(activity_type)
        if "running" not in type_key.lower():
            continue  # 러닝 외 종목(사이클, 수영 등) 제외

        garmin_activity_id = str(act["activityId"])
        stride_m    = act.get("averageStrideLength")  # 단위: 미터
        aerobic_te  = act.get("aerobicTrainingEffect")
        moving_s    = act.get("movingDuration")
        cadence_raw = act.get("averageRunCadence")

        # 필드 부재 로깅: API 변경 감지 및 디버깅용
        if cadence_raw is None:
            logging.info("Activity %s (%s): averageRunCadence 없음 (관련 키: %s)",
                         garmin_activity_id, act.get("activityName"),
                         [k for k in act if "cadence" in k.lower()])
        if stride_m is None:
            logging.info("Activity %s (%s): averageStrideLength 없음 (관련 키: %s)",
                         garmin_activity_id, act.get("activityName"),
                         [k for k in act if "stride" in k.lower()])

        result.append({
            "garmin_id":          garmin_activity_id,
            "name":               act.get("activityName", ""),
            "start_time":         act.get("startTimeLocal", ""),
            "location":           act.get("locationName") or "",
            "distance_m":         round(act.get("distance") or 0),
            "duration_s":         round(act.get("duration") or 0),
            "moving_duration_s":  round(moving_s) if moving_s else None,
            "calories":           round(act["calories"])          if act.get("calories")          else None,
            "avg_hr":             round(act["averageHR"])         if act.get("averageHR")         else None,
            "max_hr":             round(act["maxHR"])             if act.get("maxHR")             else None,
            "elevation_gain":     round(act["elevationGain"])     if act.get("elevationGain") is not None else None,
            "elevation_loss":     round(act["elevationLoss"])     if act.get("elevationLoss") is not None else None,
            "avg_cadence":        round(act["averageRunCadence"]) if act.get("averageRunCadence") else None,
            "steps":              act.get("steps"),
            "stride_length_cm":   round(stride_m * 100, 1)       if stride_m                    else None,
            "aerobic_te":         round(aerobic_te, 1)            if aerobic_te is not None      else None,
            "synced":             garmin_activity_id in synced_map,
            "strava_activity_id": synced_map.get(garmin_activity_id),
        })
    return jsonify({"activities": result, "has_more": has_more})


@app.route("/activity/<garmin_id>/details")
@login_required
@limiter.limit("30 per minute")
def activity_details_route(garmin_id):
    """
    특정 Garmin 활동의 GPS 경로 좌표를 반환한다.
    지도 표시에 사용된다. 실내 러닝은 coords가 빈 배열.

    Path params:
        garmin_id: Garmin 활동 ID (숫자)

    Response: {"coords": [[lat, lon], ...]}
    """
    if not garmin_id.isdigit():
        return jsonify({"error": "잘못된 활동 ID입니다."}), 400
    try:
        data = garmin_client.fetch_activity_details(current_user_id(), garmin_id)
        return jsonify(data)
    except Exception:
        logging.error("activity_details 오류 (user_id=%s, garmin_id=%s):\n%s",
                      current_user_id(), garmin_id, traceback.format_exc())
        return jsonify({"coords": [], "error": "활동 상세 정보를 불러오는 중 오류가 발생했습니다."}), 400


@app.route("/activity/<garmin_id>/splits")
@login_required
@limiter.limit("30 per minute")
def activity_splits(garmin_id):
    """
    특정 Garmin 활동의 구간(랩) 기록을 반환한다.

    Path params:
        garmin_id: Garmin 활동 ID (숫자)

    Response: {"splits": [...lapDTOs...]}
    """
    if not garmin_id.isdigit():
        return jsonify({"error": "잘못된 활동 ID입니다."}), 400
    try:
        splits = garmin_client.fetch_splits(current_user_id(), garmin_id)
        return jsonify({"splits": splits})
    except Exception:
        logging.error("activity_splits 오류 (user_id=%s, garmin_id=%s):\n%s",
                      current_user_id(), garmin_id, traceback.format_exc())
        return jsonify({"error": "구간 기록을 불러오는 중 오류가 발생했습니다."}), 400


# ── 동기화 라우트 ──────────────────────────────────────────────────────────────

@app.route("/sync", methods=["POST"])
@login_required
@limiter.limit("60 per minute")
def sync():
    """
    Garmin FIT 파일을 Strava에 업로드한다. 여러 활동을 배열로 한 번에 전달 가능.

    파일명을 "activity_{garmin_id}.fit" 형태로 지정하는 것이 핵심:
    이 패턴이 Strava external_id가 되고 MyNB가 이를 인식해 포인트를 부여한다.
    (Garmin→Strava 직접 연동 시 external_id가 "garmin_ping_xxx" 형태가 되어 MyNB 인식 불가)

    Request JSON : {"activity_ids": [garmin_id_str, ...]}
    Response 200 : {
        "results": [
            {"garmin_id": str, "ok": true,  "strava_activity_id": int, "already_synced": bool, "duplicate": bool},
            {"garmin_id": str, "ok": false, "error": str},
            ...
        ]
    }
    """
    body = request.get_json()
    activity_ids = body.get("activity_ids", []) if body else []
    if not isinstance(activity_ids, list):
        return jsonify({"error": "activity_ids는 배열이어야 합니다."}), 400

    # DoS 방지: 한 번에 동기화할 수 있는 최대 활동 수 제한
    # 초과 시 나머지는 무시하고 앞쪽부터만 처리 (에러 반환 대신 조용히 잘라냄)
    MAX_SYNC_BATCH = 20
    if len(activity_ids) > MAX_SYNC_BATCH:
        return jsonify({"error": f"한 번에 최대 {MAX_SYNC_BATCH}개까지만 동기화할 수 있습니다."}), 400

    # 타입 안전성 + 형식 검증: Garmin 활동 ID는 숫자만 유효
    # - str/int가 아닌 타입(bool, list 등) 제거
    # - 숫자가 아닌 문자열("../../etc/passwd" 등) 제거
    activity_ids = [
        str(i) for i in activity_ids
        if isinstance(i, (str, int)) and str(i).isdigit()
    ]

    results = []
    for garmin_activity_id in activity_ids:
        # 이미 동기화된 활동 처리
        if sync_db.is_synced(current_user_id(), garmin_activity_id):
            strava_id = sync_db.get_synced_strava_id(current_user_id(), garmin_activity_id)
            # Strava에서 활동이 삭제됐는지 확인
            # None(확인 불가) → 보수적으로 "존재한다"고 처리해 DB 기록 보존
            activity_still_exists = (
                strava_client.activity_exists(current_user_id(), strava_id)
                if strava_id else False
            )
            if activity_still_exists is not False:
                results.append({
                    "garmin_id": garmin_activity_id, "ok": True,
                    "already_synced": True, "strava_activity_id": strava_id,
                })
                continue
            # Strava에서 삭제된 것이 확인됨 → DB 기록 제거 후 재업로드
            sync_db.clear_sync_record(current_user_id(), garmin_activity_id)

        try:
            fit_bytes = garmin_client.download_fit_bytes(current_user_id(), garmin_activity_id)
            # "activity_{id}.fit" 패턴 → Strava external_id → MyNB 포인트 인식
            upload_id = strava_client.upload_fit(
                current_user_id(), fit_bytes, f"activity_{garmin_activity_id}.fit"
            )
            sync_db.record_upload_start(current_user_id(), garmin_activity_id, upload_id)
            upload_result = strava_client.poll_upload(current_user_id(), upload_id)
            sync_db.record_upload_complete(
                current_user_id(), garmin_activity_id, upload_result["activity_id"]
            )
            results.append({
                "garmin_id":          garmin_activity_id,
                "ok":                 True,
                "strava_activity_id": upload_result["activity_id"],
                "duplicate":          upload_result.get("status") == "duplicate",
            })
        except Exception as exc:
            logging.error("Sync 실패 (garmin_id=%s):\n%s", garmin_activity_id, traceback.format_exc())
            err_msg = str(exc)
            sync_db.record_upload_error(current_user_id(), garmin_activity_id, err_msg)
            # Strava duplicate 메시지("/activities/\d+" 포함)는 프론트가 링크 파싱에 사용하므로 그대로 전달.
            # 그 외 내부 오류는 일반 메시지로 교체해 구현 정보 노출을 방지한다.
            safe_msg = err_msg if re.search(r"/activities/\d+", err_msg) else "동기화 중 오류가 발생했습니다."
            results.append({"garmin_id": garmin_activity_id, "ok": False, "error": safe_msg})

    return jsonify({"results": results})


# ── 관리자 API ─────────────────────────────────────────────────────────────────

@app.route("/admin/api/users")
@admin_required
def admin_users():
    """
    전체 사용자 목록과 통계(동기화 횟수, 연결 상태)를 반환한다.

    Response: [{
        "id": int, "username": str, "is_admin": bool, "created_at": str,
        "synced_count": int, "strava_athlete_id": int|null,
        "garmin_connected": bool, "strava_connected": bool
    }, ...]
    """
    users = sync_db.list_users()
    for user in users:
        user.update(sync_db.get_user_stats(user["id"]))
        user["garmin_connected"] = garmin_client.is_logged_in(user["id"])
        user["strava_connected"] = strava_client.is_connected(user["id"])
    return jsonify(users)


@app.route("/admin/api/users/<int:target_user_id>", methods=["PUT"])
@admin_required
def admin_update_user(target_user_id):
    """
    사용자 비밀번호 또는 관리자 권한 변경.

    Path params : target_user_id (int)
    Request JSON: {"password": str (선택), "is_admin": bool (선택)}
    Response 200: {"ok": true}
    Response 400: {"error": str}
    Response 404: {"error": str}  — 존재하지 않는 사용자
    """
    if not sync_db.get_user_by_id(target_user_id):
        return jsonify({"error": "존재하지 않는 사용자입니다."}), 404
    body = request.get_json()
    if not body:
        return jsonify({"error": "요청 데이터가 없습니다."}), 400
    if "is_admin" in body:
        if target_user_id == current_user_id():
            return jsonify({"error": "자신의 관리자 권한은 변경할 수 없습니다."}), 400
        sync_db.update_user_admin(target_user_id, bool(body["is_admin"]))
    if "password" in body:
        if len(body["password"]) < 8:
            return jsonify({"error": "비밀번호는 8자 이상이어야 합니다."}), 400
        sync_db.update_user_password(target_user_id, generate_password_hash(body["password"]))
    return jsonify({"ok": True})


@app.route("/admin/api/users/<int:target_user_id>", methods=["DELETE"])
@admin_required
def admin_delete_user(target_user_id):
    """
    사용자 삭제. Garmin 토큰, Strava 토큰, 동기화 기록을 모두 제거한다.
    자기 자신은 삭제 불가.

    Path params : target_user_id (int)
    Response 200: {"ok": true}
    Response 400: {"error": str}
    Response 404: {"error": str}
    """
    if target_user_id == current_user_id():
        return jsonify({"error": "자신의 계정은 삭제할 수 없습니다."}), 400
    if not sync_db.get_user_by_id(target_user_id):
        return jsonify({"error": "존재하지 않는 사용자입니다."}), 404
    garmin_client.logout(target_user_id)
    sync_db.delete_user(target_user_id)
    return jsonify({"ok": True})


@app.route("/admin/api/users/<int:target_user_id>/activities")
@admin_required
def admin_user_activities(target_user_id):
    """
    특정 사용자의 동기화 기록 목록을 반환한다.

    Response: [{"garmin_id": str, "strava_id": int|null, "upload_id": int|null,
                "synced_at": str, "status": str}, ...]
    """
    if not sync_db.get_user_by_id(target_user_id):
        return jsonify({"error": "존재하지 않는 사용자입니다."}), 404
    return jsonify(sync_db.get_user_activities(target_user_id))


@app.route("/admin/api/users/<int:target_user_id>/activities/<garmin_id>", methods=["DELETE"])
@admin_required
def admin_delete_activity(target_user_id, garmin_id):
    """
    특정 사용자의 동기화 기록을 삭제한다.
    삭제 후 해당 활동을 다시 동기화할 수 있게 된다.

    Path params:
        target_user_id: 대상 사용자 ID (int)
        garmin_id:      삭제할 Garmin 활동 ID (숫자 문자열)
    """
    # 다른 활동 관련 라우트와 동일하게 형식 검증 (일관성 + 방어적 코딩)
    if not garmin_id.isdigit():
        return jsonify({"error": "잘못된 활동 ID입니다."}), 400
    sync_db.clear_sync_record(target_user_id, garmin_id)
    return jsonify({"ok": True})


if __name__ == "__main__":
    # 직접 실행 시 (python app.py) — 로컬 개발용
    # 프로덕션: gunicorn --bind 0.0.0.0:5000 --workers 1 app:app
    app.run(debug=False, port=5000)
