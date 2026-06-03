"""
strava_client.py — Strava API 클라이언트

OAuth2 인증 흐름, 토큰 갱신, FIT 파일 업로드, 업로드 폴링을 담당한다.

주요 흐름:
  1. get_auth_url(state) → 사용자를 Strava 인증 페이지로 안내
  2. exchange_code(user_id, code) → 인증 코드를 access/refresh 토큰으로 교환, DB 저장
  3. upload_fit(user_id, fit_bytes, filename) → FIT 파일 업로드, upload_id 반환
  4. poll_upload(user_id, upload_id) → 업로드 처리 완료까지 폴링, strava_activity_id 반환
"""

import re
import time

import requests

import sync_db
from config import (
    STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET,
    STRAVA_REDIRECT_URI, STRAVA_AUTH_URL, STRAVA_TOKEN_URL,
    STRAVA_API_BASE, STRAVA_UPLOAD_URL,
    STRAVA_POLL_RETRIES, STRAVA_POLL_INTERVAL,
)


def get_auth_url(state: str) -> str:
    """
    Strava OAuth 인증 URL을 생성해 반환한다.

    Args:
        state: CSRF 방지용 랜덤 토큰. 콜백에서 동일한 값인지 검증해야 한다.

    Returns:
        사용자를 리다이렉트할 Strava 인증 URL.
    """
    req = requests.Request("GET", STRAVA_AUTH_URL, params={
        "client_id":       STRAVA_CLIENT_ID,
        "redirect_uri":    STRAVA_REDIRECT_URI,
        "response_type":   "code",
        "approval_prompt": "auto",
        "scope":           "activity:write,activity:read",
        "state":           state,  # 콜백에서 session의 state와 대조해 CSRF 검증
    })
    return req.prepare().url


def exchange_code(user_id: int, code: str) -> dict:
    """
    Strava가 보낸 인증 코드를 access_token / refresh_token으로 교환하고 DB에 저장한다.

    Args:
        user_id: SyncNb 사용자 ID.
        code:    Strava 콜백 URL의 ?code= 파라미터.

    Returns:
        Strava 토큰 응답 딕셔너리 (access_token, refresh_token, expires_at, athlete 등).

    Raises:
        requests.HTTPError: Strava API 오류 시.
    """
    resp = requests.post(STRAVA_TOKEN_URL, data={
        "client_id":     STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code":          code,
        "grant_type":    "authorization_code",
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    sync_db.save_strava_tokens(
        user_id=user_id,
        athlete_id=data["athlete"]["id"],
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=data["expires_at"],
    )
    return data


def get_valid_access_token(user_id: int) -> str:
    """
    유효한 Strava access_token을 반환한다.
    만료 60초 전이면 자동으로 갱신(refresh)하고 DB에 저장한다.

    Args:
        user_id: SyncNb 사용자 ID.

    Returns:
        현재 유효한 Strava access_token 문자열.

    Raises:
        RuntimeError: Strava가 연결되지 않은 경우.
        requests.HTTPError: 토큰 갱신 실패 시.
    """
    tokens = sync_db.load_strava_tokens(user_id)
    if tokens is None:
        raise RuntimeError("Strava가 연결되어 있지 않습니다.")

    # 만료 60초 전부터 미리 갱신 (실제 API 호출 직전에 만료되는 경우 방지)
    if int(time.time()) >= tokens["expires_at"] - 60:
        resp = requests.post(STRAVA_TOKEN_URL, data={
            "client_id":     STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "grant_type":    "refresh_token",
            "refresh_token": tokens["refresh_token"],
        }, timeout=30)
        resp.raise_for_status()
        new_tokens = resp.json()
        sync_db.save_strava_tokens(
            user_id=user_id,
            athlete_id=tokens["athlete_id"],
            access_token=new_tokens["access_token"],
            refresh_token=new_tokens["refresh_token"],
            expires_at=new_tokens["expires_at"],
        )
        return new_tokens["access_token"]

    return tokens["access_token"]


def has_tokens(user_id: int) -> bool:
    """
    DB에 Strava 토큰이 존재하는지 확인한다 (네트워크 호출 없음).
    관리자 패널처럼 다수 사용자를 한 번에 확인할 때 사용한다.
    """
    return sync_db.load_strava_tokens(user_id) is not None


def is_connected(user_id: int) -> bool:
    """
    해당 사용자가 Strava와 연결되어 있는지(유효한 토큰이 있는지) 확인한다.

    Returns:
        True: 연결됨 (토큰 획득 가능).
        False: 연결 안 됨 또는 토큰 오류.
    """
    try:
        get_valid_access_token(user_id)
        return True
    except Exception:
        return False


def activity_exists(user_id: int, strava_activity_id: int) -> bool | None:
    """
    Strava에 특정 활동이 존재하는지 확인한다.

    Returns:
        True:  활동이 존재함 (HTTP 200).
        False: 활동이 삭제됨 (HTTP 404).
        None:  확인 불가 (401/403/네트워크 오류 등) — 이 경우 DB 기록을 지우면 안 됨.
    """
    try:
        token = get_valid_access_token(user_id)
        resp = requests.get(
            f"{STRAVA_API_BASE}/activities/{strava_activity_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        # 401/403/5xx: 인증 문제나 서버 오류 → 불확실하므로 None 반환
        return None
    except Exception:
        return None


def upload_fit(user_id: int, fit_bytes: bytes, filename: str,
               activity_name: str | None = None) -> int:
    """
    FIT 파일을 Strava에 업로드하고 upload_id를 반환한다.

    업로드는 비동기로 처리되므로 완료 여부는 poll_upload()로 확인해야 한다.

    Args:
        user_id:       SyncNb 사용자 ID.
        fit_bytes:     FIT 파일의 바이너리 내용.
        filename:      업로드 파일명. "activity_{garmin_id}.fit" 형태여야 MyNB가 인식한다.
        activity_name: Strava에 표시될 활동 이름 (None이면 Strava 기본값 사용).

    Returns:
        Strava upload_id (int). poll_upload()에 전달해 완료를 기다린다.

    Raises:
        requests.HTTPError: 업로드 요청 실패 시.
    """
    token = get_valid_access_token(user_id)
    form_data: dict = {"data_type": "fit"}
    if activity_name:
        form_data["name"] = activity_name
    resp = requests.post(
        STRAVA_UPLOAD_URL,
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, fit_bytes, "application/octet-stream")},
        data=form_data,
        timeout=60,  # FIT 파일 업로드는 파일 크기에 따라 더 오래 걸릴 수 있음
    )
    resp.raise_for_status()
    return int(resp.json()["id_str"])


def poll_upload(user_id: int, upload_id: int) -> dict:
    """
    Strava 업로드 처리가 완료될 때까지 폴링한다.

    Strava는 업로드 직후 처리 큐에 넣기 때문에 activity_id를 즉시 알 수 없다.
    약 2초 간격으로 최대 15회(30초) 폴링한다.

    중복 활동인 경우 Strava가 오류 메시지로 기존 활동 URL을 알려준다.
    예: "duplicate of activity <a href='/activities/18462741778'>..."
    이 경우 기존 activity_id를 추출해 정상 처리한다.

    Args:
        user_id:   SyncNb 사용자 ID.
        upload_id: upload_fit()이 반환한 Strava upload_id.

    Returns:
        {"activity_id": int, "status": str, ...}
        중복인 경우 status == "duplicate".

    Raises:
        RuntimeError: 업로드 오류 또는 폴링 타임아웃 시.
    """
    token = get_valid_access_token(user_id)
    headers = {"Authorization": f"Bearer {token}"}
    poll_url = f"{STRAVA_UPLOAD_URL}/{upload_id}"

    for _ in range(STRAVA_POLL_RETRIES):
        resp = requests.get(poll_url, headers=headers, timeout=30)
        resp.raise_for_status()
        payload = resp.json()

        if payload.get("error"):
            error_msg = payload["error"]
            # 중복 활동: 오류 메시지 안에 기존 활동 URL이 포함됨
            # 예1: "... <a href='/activities/18462741778'>..."
            # 예2: "duplicate of activity #18462741778"
            duplicate_match = (
                re.search(r"/activities/(\d+)", error_msg) or
                re.search(r"duplicate of activity #?(\d+)", error_msg, re.IGNORECASE)
            )
            if duplicate_match:
                existing_activity_id = int(duplicate_match.group(1))
                return {"activity_id": existing_activity_id, "status": "duplicate", "error": None}
            raise RuntimeError(f"Strava 업로드 오류: {error_msg}")

        if payload.get("activity_id"):
            return payload  # 처리 완료

        time.sleep(STRAVA_POLL_INTERVAL)

    raise RuntimeError(f"업로드 {upload_id}가 타임아웃되었습니다 ({STRAVA_POLL_RETRIES * STRAVA_POLL_INTERVAL:.0f}초).")
