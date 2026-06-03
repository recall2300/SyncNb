"""
garmin_client.py — Garmin Connect API 클라이언트

Garmin SSO 로그인, MFA(2단계 인증) 처리, 활동 목록/상세/FIT 다운로드를 담당한다.

토큰 저장:
  로그인 성공 시 세션 토큰이 ~/.garminconnect/{user_id}/ 에 파일로 저장된다.
  Docker 환경에서는 named volume으로 마운트해 컨테이너 재시작 후에도 유지된다.

MFA 흐름:
  login_with_credentials() → mfa_required: True 반환
  → resume_mfa(code) → 로그인 완료, 토큰 저장
  MFA 세션은 메모리(_pending_mfa)에 최대 MFA_TIMEOUT_SEC(300초)간 유지된다.
"""

import io
import logging
import shutil
import time
import zipfile
from pathlib import Path

from garminconnect import Garmin

from config import GARMIN_ACTIVITY_LIMIT, GARMIN_TOKEN_STORE

# MFA 진행 중인 세션을 임시 보관하는 인메모리 저장소
# { user_id: {"client": Garmin, "mfa_state": ..., "created_at": float} }
# Garmin 객체는 JSON 직렬화 불가 → Flask session이 아닌 모듈 변수에 저장
_pending_mfa: dict[int, dict] = {}

# MFA 세션 유효 시간 (초). 이 시간이 지나면 코드 제출 불가 → 재로그인 필요
MFA_TIMEOUT_SEC = 300  # 5분


def _token_store_path(user_id: int) -> str:
    """
    사용자별 Garmin 세션 토큰 저장 디렉토리 경로를 반환하고 없으면 생성한다.

    기본 경로: ~/.garminconnect/{user_id}/
    환경변수 GARMIN_TOKEN_STORE로 루트 디렉토리를 변경할 수 있다.
    Docker 배포 시 named volume을 이 경로에 마운트한다.
    """
    path = Path(GARMIN_TOKEN_STORE) / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _cleanup_expired_mfa() -> None:
    """만료된 MFA 세션을 메모리에서 제거한다. 매 MFA 관련 호출 시 실행된다."""
    now = time.time()
    expired_user_ids = [
        uid for uid, session in _pending_mfa.items()
        if now - session.get("created_at", 0) > MFA_TIMEOUT_SEC
    ]
    for uid in expired_user_ids:
        _pending_mfa.pop(uid, None)
        logging.info("MFA 세션 만료 제거 (user_id=%s)", uid)


def login_with_credentials(user_id: int, email: str, password: str) -> tuple[bool, None]:
    """
    이메일/비밀번호로 Garmin Connect에 로그인한다.

    MFA가 필요한 경우 MFA 세션을 _pending_mfa에 저장하고 (False, None)을 반환한다.
    이후 resume_mfa()를 호출해 로그인을 완료해야 한다.

    Args:
        user_id:  SyncNb 사용자 ID.
        email:    Garmin Connect 이메일.
        password: Garmin Connect 비밀번호.

    Returns:
        (True, None):  로그인 성공 (MFA 불필요).
        (False, None): MFA 필요. resume_mfa()로 완료해야 함.

    Raises:
        Exception: Garmin 로그인 실패 (잘못된 자격증명 등).
    """
    _cleanup_expired_mfa()

    # 기존 토큰 삭제 후 새 로그인 시도.
    # garminconnect 라이브러리는 tokenstore에 파일이 있으면 email/password를 무시하고
    # 기존 토큰을 재사용한다. 다른 계정으로 전환할 때 이전 계정 토큰이 남아있으면
    # 새 자격증명이 무시되거나 토큰 불일치로 오류가 발생하므로, 항상 깨끗한 상태에서 시작한다.
    token_path = Path(GARMIN_TOKEN_STORE) / str(user_id)
    if token_path.exists():
        shutil.rmtree(token_path)

    client = Garmin(email=email, password=password, return_on_mfa=True)
    mfa_state, _ = client.login(_token_store_path(user_id))
    if mfa_state:
        # MFA 코드 입력이 필요 → 세션을 메모리에 임시 보관
        _pending_mfa[user_id] = {
            "client":    client,
            "mfa_state": mfa_state,
            "created_at": time.time(),  # 만료 계산용 타임스탬프
        }
        return False, None

    # MFA 불필요 → 로그인 즉시 완료
    _pending_mfa.pop(user_id, None)
    return True, None


def resume_mfa(user_id: int, mfa_code: str) -> None:
    """
    MFA 코드를 제출해 보류 중인 Garmin 로그인을 완료한다.
    성공 시 토큰이 파일로 저장된다.

    Args:
        user_id:  SyncNb 사용자 ID.
        mfa_code: Garmin이 발송한 6~8자리 인증 코드.

    Raises:
        RuntimeError: MFA 세션이 없거나 만료된 경우.
        Exception:    인증 코드가 틀린 경우 (Garmin 라이브러리 예외).
    """
    _cleanup_expired_mfa()
    pending = _pending_mfa.get(user_id)
    if pending is None:
        raise RuntimeError(f"MFA 세션이 없거나 만료되었습니다 ({MFA_TIMEOUT_SEC // 60}분 초과). 다시 로그인해주세요.")

    client: Garmin = pending["client"]
    client.resume_login(pending["mfa_state"], mfa_code)

    # 로그인 완료 후 토큰 파일 저장
    try:
        client.client.dump(_token_store_path(user_id))
    except Exception:
        logging.warning("MFA 완료 후 Garmin 토큰 저장 실패 (user_id=%s)", user_id)

    _pending_mfa.pop(user_id, None)


def has_pending_mfa(user_id: int) -> bool:
    """해당 사용자의 MFA 세션이 아직 유효하게 존재하는지 확인한다."""
    _cleanup_expired_mfa()
    return user_id in _pending_mfa


def _load_client(user_id: int) -> Garmin | None:
    """
    저장된 토큰으로 Garmin 클라이언트를 복원한다.

    Returns:
        Garmin: 토큰이 유효하고 로그인에 성공한 클라이언트.
        None:   토큰 없음 또는 만료됨.
    """
    try:
        client = Garmin()
        client.login(_token_store_path(user_id))
        return client
    except Exception as exc:
        logging.debug("Garmin 클라이언트 로드 실패 (user_id=%s): %s", user_id, exc)
        return None


def is_running_activity(act: dict) -> bool:
    """Garmin 활동 딕셔너리가 러닝 종목인지 확인한다."""
    activity_type = act.get("activityType")
    type_key = activity_type.get("typeKey", "") if isinstance(activity_type, dict) else str(activity_type)
    return "running" in type_key.lower()


def has_token_files(user_id: int) -> bool:
    """
    Garmin 토큰 파일이 존재하는지 확인한다 (네트워크 호출 없음).
    관리자 패널 목록처럼 다수 사용자를 한 번에 확인할 때 사용한다.
    """
    token_path = Path(GARMIN_TOKEN_STORE) / str(user_id)
    return token_path.exists() and any(token_path.iterdir())


def is_logged_in(user_id: int) -> bool:
    """저장된 토큰으로 Garmin에 로그인 가능한지 확인한다."""
    return _load_client(user_id) is not None


def logout(user_id: int) -> None:
    """
    Garmin 세션 토큰 파일을 삭제하고 MFA 세션을 제거한다.
    사용자 계정 삭제 또는 명시적 로그아웃 시 호출된다.
    """
    _pending_mfa.pop(user_id, None)
    token_path = Path(GARMIN_TOKEN_STORE) / str(user_id)
    if token_path.exists():
        shutil.rmtree(token_path)


def get_recent_activities(user_id: int, limit: int = GARMIN_ACTIVITY_LIMIT,
                          offset: int = 0) -> list[dict]:
    """
    최근 Garmin 활동 목록을 반환한다 (전체 종목 포함, 러닝 필터링은 호출 측에서).

    Args:
        user_id: SyncNb 사용자 ID.
        limit:   가져올 최대 활동 수.
        offset:  건너뛸 활동 수 (페이지네이션).

    Returns:
        Garmin Connect API 원본 활동 딕셔너리 목록.

    Raises:
        RuntimeError: Garmin 로그인이 안 되어 있는 경우.
    """
    client = _load_client(user_id)
    if client is None:
        raise RuntimeError("Garmin에 로그인되어 있지 않습니다.")
    return client.get_activities(start=offset, limit=limit)


def fetch_activity_details(user_id: int, garmin_activity_id: str) -> dict:
    """
    특정 활동의 GPS 경로 좌표를 반환한다.

    Args:
        user_id:             SyncNb 사용자 ID.
        garmin_activity_id:  Garmin 활동 ID (숫자 문자열).

    Returns:
        {"coords": [[lat, lon], ...]}
        GPS 데이터가 없는 활동(실내 러닝 등)은 coords가 빈 배열.
    """
    client = _load_client(user_id)
    if client is None:
        raise RuntimeError("Garmin에 로그인되어 있지 않습니다.")
    try:
        data = client.get_activity_details(garmin_activity_id)
        if not isinstance(data, dict):
            logging.warning("Activity %s: details 응답 타입=%s", garmin_activity_id, type(data).__name__)
            return {"coords": []}

        logging.info("Activity %s details 최상위 키: %s", garmin_activity_id, list(data.keys()))

        # geoPolylineDTO: GPS 경로 데이터. 실내 러닝이나 GPS 없는 활동은 null 또는 부재
        geo = data.get("geoPolylineDTO") or {}
        if not geo:
            logging.info("Activity %s: geoPolylineDTO 없음 (실내 또는 GPS 미기록)", garmin_activity_id)

        polyline = geo.get("polyline", []) if geo else []
        logging.info("Activity %s: polyline 포인트 수=%d", garmin_activity_id, len(polyline))

        # Garmin polyline은 {lat, lon, ...} 형태의 딕셔너리 목록
        coords = [
            [round(point["lat"], 6), round(point["lon"], 6)]
            for point in polyline
            if "lat" in point and "lon" in point
        ]
        logging.info("Activity %s: 추출된 좌표 수=%d", garmin_activity_id, len(coords))
        return {"coords": coords}

    except Exception as exc:
        logging.error("fetch_activity_details 오류 (%s): %s", garmin_activity_id, exc, exc_info=True)
        return {"coords": [], "error": str(exc)}


def fetch_splits(user_id: int, garmin_activity_id: str) -> list[dict]:
    """
    특정 활동의 구간(랩) 기록을 반환한다.

    Args:
        user_id:             SyncNb 사용자 ID.
        garmin_activity_id:  Garmin 활동 ID.

    Returns:
        lapDTOs 목록. 데이터가 없거나 오류 시 빈 리스트.
    """
    client = _load_client(user_id)
    if client is None:
        raise RuntimeError("Garmin에 로그인되어 있지 않습니다.")
    try:
        data = client.get_activity_splits(garmin_activity_id)
        return data.get("lapDTOs", [])
    except Exception:
        return []


def download_fit_bytes(user_id: int, garmin_activity_id: str) -> bytes:
    """
    활동 데이터를 FIT 파일 바이너리로 다운로드한다.

    Garmin은 FIT 파일을 ZIP으로 감싸서 제공한다.
    ZIP에서 첫 번째 .fit 파일을 추출해 반환한다.

    Args:
        user_id:             SyncNb 사용자 ID.
        garmin_activity_id:  Garmin 활동 ID.

    Returns:
        FIT 파일 바이너리 (bytes). Strava upload_fit()에 직접 전달 가능.

    Raises:
        RuntimeError: Garmin 로그인 안 됨.
        ValueError:   ZIP 안에 .fit 파일이 없는 경우 (트레킹/수영 등 일부 종목).
    """
    client = _load_client(user_id)
    if client is None:
        raise RuntimeError("Garmin에 로그인되어 있지 않습니다.")

    zip_data = client.download_activity(
        garmin_activity_id,
        dl_fmt=client.ActivityDownloadFormat.ORIGINAL,
    )

    # Garmin은 항상 ZIP으로 감싸서 제공. 내부 파일명은 활동마다 다름.
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        fit_filenames = [name for name in zf.namelist() if name.lower().endswith(".fit")]
        if not fit_filenames:
            raise ValueError(
                f"활동 {garmin_activity_id}의 ZIP에 .fit 파일이 없습니다. "
                f"(포함된 파일: {zf.namelist()})"
            )
        return zf.read(fit_filenames[0])
