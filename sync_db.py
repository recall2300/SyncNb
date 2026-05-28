"""
sync_db.py — SQLite 데이터베이스 관리

멀티유저 구조로 users, strava_tokens, synced_activities 세 테이블을 관리한다.
스키마 버전 관리(PRAGMA user_version)를 통해 자동 마이그레이션을 지원한다.

현재 스키마 버전: 4

테이블 구조:
  users (id, username, password_hash, is_admin, created_at,
          auto_sync_enabled, auto_sync_enabled_at)
  strava_tokens (user_id PK, athlete_id, access_token, refresh_token, expires_at)
  synced_activities (id, user_id, garmin_activity_id, strava_activity_id,
                     strava_upload_id, synced_at, status)
    status: 'pending' | 'uploaded' | 'error'
"""

import sqlite3
from datetime import datetime, timezone
from config import DB_PATH

# 이 값을 올리면 _migrate()에서 해당 버전 이상의 마이그레이션이 실행된다
_SCHEMA_VERSION = 4


def _conn() -> sqlite3.Connection:
    """
    SQLite 연결을 생성하고 반환한다.

    WAL(Write-Ahead Logging) 모드:
      기본 모드는 쓰기 시 전체 DB를 잠근다. WAL 모드에서는 읽기와 쓰기가 동시에 가능해
      멀티스레드 환경(Flask + APScheduler)에서 "database is locked" 오류가 줄어든다.

    busy_timeout:
      잠금 경합 발생 시 즉시 오류를 내지 않고 최대 5초간 재시도한다.
    """
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")   # 동시 읽기/쓰기 허용
    conn.execute("PRAGMA busy_timeout=5000")  # 잠금 대기 최대 5초
    return conn


def init_db() -> None:
    """DB 파일이 없으면 생성하고, 스키마가 구버전이면 마이그레이션한다."""
    with _conn() as conn:
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """
    PRAGMA user_version을 확인해 필요한 마이그레이션을 순서대로 실행한다.

    버전 이력:
      v1~v2: 단일 사용자 시절 (레거시, 테이블 DROP으로 정리)
      v3: 멀티유저 지원 — users, strava_tokens, synced_activities 재설계
      v4: users에 auto_sync_enabled, auto_sync_enabled_at 컬럼 추가
    """
    schema_ver = conn.execute("PRAGMA user_version").fetchone()[0]

    if schema_ver < 3:
        # v1/v2 테이블은 구조가 달라 DROP 후 재생성
        conn.executescript("""
            DROP TABLE IF EXISTS strava_tokens;
            DROP TABLE IF EXISTS synced_activities;

            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin      INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL
            );

            -- Strava OAuth 토큰. user_id 1:1 관계 (한 사용자가 하나의 Strava 계정)
            CREATE TABLE IF NOT EXISTS strava_tokens (
                user_id       INTEGER PRIMARY KEY,
                athlete_id    INTEGER NOT NULL,
                access_token  TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                expires_at    INTEGER NOT NULL  -- Unix timestamp
            );

            -- 동기화 이력. (user_id, garmin_activity_id) 조합이 유니크
            CREATE TABLE IF NOT EXISTS synced_activities (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id            INTEGER NOT NULL,
                garmin_activity_id TEXT NOT NULL,
                strava_activity_id INTEGER,
                strava_upload_id   INTEGER,
                synced_at          TEXT NOT NULL,
                status             TEXT NOT NULL DEFAULT 'pending',
                UNIQUE(user_id, garmin_activity_id)
            );

            PRAGMA user_version = 3;
        """)
        schema_ver = 3

    if schema_ver < 4:
        # v3 → v4: 자동 동기화 관련 컬럼 추가
        conn.execute("ALTER TABLE users ADD COLUMN auto_sync_enabled INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE users ADD COLUMN auto_sync_enabled_at TEXT")
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        schema_ver = 4


# ── 사용자 관리 ────────────────────────────────────────────────────────────────

def create_user(username: str, password_hash: str, is_admin: bool = False) -> int:
    """
    새 사용자를 생성하고 DB id를 반환한다.

    Args:
        username:      고유한 사용자 아이디.
        password_hash: werkzeug.security.generate_password_hash() 결과값.
        is_admin:      관리자 권한 여부 (기본 False).

    Returns:
        생성된 사용자의 DB id (int).
    """
    created_at = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?,?,?,?)",
            (username, password_hash, 1 if is_admin else 0, created_at),
        )
        return cursor.lastrowid


def get_user_by_username(username: str) -> dict | None:
    """
    아이디로 사용자를 조회한다. 로그인 처리에 사용.

    Returns:
        {"id", "username", "password_hash", "is_admin"} 또는 None.
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, is_admin FROM users WHERE username=?",
            (username,),
        ).fetchone()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "password_hash": row[2], "is_admin": bool(row[3])}


def get_user_by_id(user_id: int) -> dict | None:
    """
    DB id로 사용자를 조회한다. 비밀번호 변경, 관리자 패널에 사용.

    Returns:
        {"id", "username", "password_hash", "is_admin", "created_at"} 또는 None.
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, is_admin, created_at FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "username": row[1], "password_hash": row[2],
        "is_admin": bool(row[3]), "created_at": row[4],
    }


def list_users() -> list[dict]:
    """
    모든 사용자를 id 오름차순으로 반환한다.

    Returns:
        [{"id", "username", "is_admin", "created_at"}, ...]
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, username, is_admin, created_at FROM users ORDER BY id",
        ).fetchall()
    return [{"id": r[0], "username": r[1], "is_admin": bool(r[2]), "created_at": r[3]} for r in rows]


def user_exists() -> bool:
    """DB에 사용자가 한 명이라도 있는지 확인한다. 첫 실행 시 관리자 생성 여부 판단에 사용."""
    with _conn() as conn:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def update_user_password(user_id: int, password_hash: str) -> None:
    """사용자의 비밀번호 해시를 업데이트한다."""
    with _conn() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))


def update_user_admin(user_id: int, is_admin: bool) -> None:
    """사용자의 관리자 권한을 변경한다."""
    with _conn() as conn:
        conn.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if is_admin else 0, user_id))


def delete_user(user_id: int) -> None:
    """
    사용자와 관련된 모든 데이터를 삭제한다.
    Strava 토큰, 동기화 기록, 사용자 계정 순으로 삭제한다.
    Garmin 토큰 파일 삭제는 garmin_client.logout()을 별도로 호출해야 한다.
    """
    with _conn() as conn:
        conn.execute("DELETE FROM strava_tokens WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM synced_activities WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))


# ── Strava 토큰 관리 ───────────────────────────────────────────────────────────

def save_strava_tokens(user_id: int, athlete_id: int, access_token: str,
                       refresh_token: str, expires_at: int) -> None:
    """
    Strava OAuth 토큰을 저장하거나 갱신한다.
    같은 user_id가 이미 있으면 모든 토큰 값을 덮어씌운다 (UPSERT).

    Args:
        user_id:       SyncNb 사용자 ID.
        athlete_id:    Strava 운동선수 ID.
        access_token:  Strava API 호출용 토큰. 6시간 유효.
        refresh_token: access_token 갱신용 토큰. 장기 유효.
        expires_at:    access_token 만료 시각 (Unix timestamp).
    """
    with _conn() as conn:
        conn.execute("""
            INSERT INTO strava_tokens (user_id, athlete_id, access_token, refresh_token, expires_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                athlete_id    = excluded.athlete_id,
                access_token  = excluded.access_token,
                refresh_token = excluded.refresh_token,
                expires_at    = excluded.expires_at
        """, (user_id, athlete_id, access_token, refresh_token, expires_at))


def load_strava_tokens(user_id: int) -> dict | None:
    """
    사용자의 Strava 토큰을 불러온다.

    Returns:
        {"athlete_id", "access_token", "refresh_token", "expires_at"} 또는 None.
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT athlete_id, access_token, refresh_token, expires_at "
            "FROM strava_tokens WHERE user_id=?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return {"athlete_id": row[0], "access_token": row[1], "refresh_token": row[2], "expires_at": row[3]}


def delete_strava_tokens(user_id: int) -> None:
    """Strava 연결을 해제한다. 토큰 레코드를 완전히 삭제한다."""
    with _conn() as conn:
        conn.execute("DELETE FROM strava_tokens WHERE user_id=?", (user_id,))


# ── 동기화 이력 관리 ───────────────────────────────────────────────────────────

def is_synced(user_id: int, garmin_activity_id: str) -> bool:
    """
    해당 Garmin 활동이 이미 Strava에 업로드 완료된 상태인지 확인한다.
    status='uploaded' 인 레코드가 있을 때만 True.
    """
    with _conn() as conn:
        return conn.execute(
            "SELECT 1 FROM synced_activities "
            "WHERE user_id=? AND garmin_activity_id=? AND status='uploaded'",
            (user_id, garmin_activity_id),
        ).fetchone() is not None


def record_upload_start(user_id: int, garmin_activity_id: str, strava_upload_id: int) -> None:
    """
    업로드 시작을 기록한다 (status='pending').
    이미 레코드가 있으면 upload_id와 시각을 갱신한다 (UPSERT).

    Args:
        strava_upload_id: strava_client.upload_fit()이 반환한 업로드 대기 ID.
    """
    synced_at = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute("""
            INSERT INTO synced_activities
                (user_id, garmin_activity_id, strava_upload_id, synced_at, status)
            VALUES (?,?,?,?,'pending')
            ON CONFLICT(user_id, garmin_activity_id) DO UPDATE SET
                strava_upload_id = excluded.strava_upload_id,
                synced_at        = excluded.synced_at,
                status           = 'pending'
        """, (user_id, garmin_activity_id, strava_upload_id, synced_at))


def record_upload_complete(user_id: int, garmin_activity_id: str,
                           strava_activity_id: int) -> None:
    """
    업로드 완료를 기록한다 (status='uploaded').

    Args:
        strava_activity_id: Strava에서 생성된 활동 ID.
    """
    with _conn() as conn:
        conn.execute(
            "UPDATE synced_activities "
            "SET strava_activity_id=?, status='uploaded' "
            "WHERE user_id=? AND garmin_activity_id=?",
            (strava_activity_id, user_id, garmin_activity_id),
        )


def record_upload_error(user_id: int, garmin_activity_id: str, error_message: str) -> None:
    """
    업로드 실패를 기록한다 (status='error').
    레코드가 없으면 생성, 있으면 상태를 갱신한다.
    """
    synced_at = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute("""
            INSERT INTO synced_activities
                (user_id, garmin_activity_id, synced_at, status)
            VALUES (?,?,?,'error')
            ON CONFLICT(user_id, garmin_activity_id) DO UPDATE SET
                synced_at = excluded.synced_at,
                status    = 'error'
        """, (user_id, garmin_activity_id, synced_at))


def get_synced_map(user_id: int) -> dict[str, int | None]:
    """
    사용자의 동기화 완료 목록을 {garmin_activity_id → strava_activity_id} 딕셔너리로 반환한다.
    status='uploaded' 인 항목만 포함한다.

    Returns:
        예: {"12345678": 18462741778, "12345679": None, ...}
        strava_activity_id가 None인 경우는 upload 완료 후 activity_id 저장 전 예외 발생한 경우.
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT garmin_activity_id, strava_activity_id "
            "FROM synced_activities WHERE user_id=? AND status='uploaded'",
            (user_id,),
        ).fetchall()
    return {row[0]: row[1] for row in rows}


def get_synced_strava_id(user_id: int, garmin_activity_id: str) -> int | None:
    """
    특정 Garmin 활동에 연결된 Strava 활동 ID를 반환한다.

    Returns:
        Strava 활동 ID (int) 또는 None (미동기화 또는 업로드 중).
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT strava_activity_id FROM synced_activities "
            "WHERE user_id=? AND garmin_activity_id=? AND status='uploaded'",
            (user_id, garmin_activity_id),
        ).fetchone()
    return row[0] if row else None


def clear_sync_record(user_id: int, garmin_activity_id: str) -> None:
    """
    동기화 기록을 삭제한다.
    삭제 후 해당 활동을 재동기화할 수 있다.
    Strava에서 활동이 삭제된 것이 확인된 경우 또는 관리자가 수동으로 초기화할 때 사용한다.
    """
    with _conn() as conn:
        conn.execute(
            "DELETE FROM synced_activities WHERE user_id=? AND garmin_activity_id=?",
            (user_id, garmin_activity_id),
        )


# ── 관리자 헬퍼 ────────────────────────────────────────────────────────────────

def get_user_stats(user_id: int) -> dict:
    """
    사용자의 동기화 통계를 반환한다. 관리자 패널 사용자 목록에 사용.

    Returns:
        {"synced_count": int, "strava_athlete_id": int | None}
    """
    with _conn() as conn:
        synced_count = conn.execute(
            "SELECT COUNT(*) FROM synced_activities WHERE user_id=? AND status='uploaded'",
            (user_id,),
        ).fetchone()[0]
        strava_row = conn.execute(
            "SELECT athlete_id FROM strava_tokens WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return {
        "synced_count":      synced_count,
        "strava_athlete_id": strava_row[0] if strava_row else None,
    }


def get_user_activities(user_id: int) -> list[dict]:
    """
    사용자의 동기화 기록 전체를 최신순으로 반환한다. 관리자 패널에 사용.

    Returns:
        [{"garmin_id", "strava_id", "upload_id", "synced_at", "status"}, ...]
    """
    with _conn() as conn:
        rows = conn.execute("""
            SELECT garmin_activity_id, strava_activity_id,
                   strava_upload_id, synced_at, status
            FROM synced_activities
            WHERE user_id=?
            ORDER BY synced_at DESC
        """, (user_id,)).fetchall()
    return [
        {"garmin_id": r[0], "strava_id": r[1], "upload_id": r[2],
         "synced_at": r[3], "status": r[4]}
        for r in rows
    ]


# ── 자동 동기화 설정 ───────────────────────────────────────────────────────────

def get_auto_sync_users() -> list[dict]:
    """
    자동 동기화가 활성화된 모든 사용자를 반환한다.
    APScheduler의 주기적 작업에서 호출된다.

    Returns:
        [{"id": int, "auto_sync_enabled_at": str}, ...]
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, auto_sync_enabled_at FROM users WHERE auto_sync_enabled = 1"
        ).fetchall()
    return [{"id": row[0], "auto_sync_enabled_at": row[1]} for row in rows]


def set_auto_sync(user_id: int, enabled: bool) -> None:
    """
    사용자의 자동 동기화 설정을 변경한다.
    활성화 시 auto_sync_enabled_at에 현재 시각을 기록한다.
    이 시각은 scheduler가 "활성화 이전 활동은 건너뛰기" 판단에 사용한다.
    """
    enabled_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as conn:
        if enabled:
            conn.execute(
                "UPDATE users SET auto_sync_enabled = 1, auto_sync_enabled_at = ? WHERE id = ?",
                (enabled_at, user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET auto_sync_enabled = 0 WHERE id = ?",
                (user_id,),
            )


def get_auto_sync_setting(user_id: int) -> dict:
    """
    사용자의 자동 동기화 설정을 반환한다.

    Returns:
        {"auto_sync_enabled": bool, "auto_sync_enabled_at": str | None}
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT auto_sync_enabled, auto_sync_enabled_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return {"auto_sync_enabled": False, "auto_sync_enabled_at": None}
    return {"auto_sync_enabled": bool(row[0]), "auto_sync_enabled_at": row[1]}
