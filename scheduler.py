"""
scheduler.py — 자동 동기화 백그라운드 스케줄러

APScheduler의 BackgroundScheduler를 사용해 2분마다 자동 동기화를 실행한다.
자동 동기화가 활성화된 사용자의 최근 러닝 활동을 확인하고,
아직 Strava에 업로드되지 않은 활동을 자동으로 업로드한다.

자동 동기화 조건:
  - 해당 사용자의 auto_sync_enabled = 1
  - Garmin에 로그인되어 있음
  - Strava가 연결되어 있음
  - 활동 시각이 auto_sync_enabled_at 이후 (처음 활성화 시 과거 활동 대량 업로드 방지)
"""

import logging
import traceback

from apscheduler.schedulers.background import BackgroundScheduler

import garmin_client
import strava_client
import sync_db

# daemon=True: 메인 프로세스 종료 시 스케줄러 스레드도 자동 종료
_scheduler = BackgroundScheduler(daemon=True)


def _run_auto_sync() -> None:
    """
    자동 동기화 활성화된 모든 사용자에 대해 동기화를 실행한다.
    개별 사용자 오류는 로깅 후 계속 진행한다 (한 사용자 오류가 전체를 막지 않음).
    """
    users = sync_db.get_auto_sync_users()
    logging.info("Auto-sync 실행: 대상 사용자 %d명", len(users))
    for user in users:
        try:
            _sync_single_user(user["id"], user["auto_sync_enabled_at"])
        except Exception:
            logging.error(
                "Auto-sync 오류 (user_id=%s):\n%s",
                user["id"], traceback.format_exc(),
            )


def _sync_single_user(user_id: int, auto_sync_enabled_at: str | None) -> None:
    """
    단일 사용자에 대해 미동기화 러닝 활동을 Strava에 업로드한다.

    Args:
        user_id:               SyncNb 사용자 ID.
        auto_sync_enabled_at:  자동 동기화 활성화 시각 (UTC, 'YYYY-MM-DD HH:MM:SS' 형식).
                               이 시각 이전 활동은 건너뛴다.
    """
    # ── 연결 상태 확인 ────────────────────────────────────────────────────────
    if not garmin_client.is_logged_in(user_id):
        logging.info("Auto-sync 스킵 (user_id=%s): Garmin 미로그인", user_id)
        return
    if not strava_client.is_connected(user_id):
        logging.info("Auto-sync 스킵 (user_id=%s): Strava 미연결", user_id)
        return

    # ── 최근 활동 목록 가져오기 ────────────────────────────────────────────────
    try:
        # 최근 10개 확인: 러닝이 아닌 활동(사이클, 수영 등)이 섞인 경우에도
        # 최소 몇 개의 러닝을 찾을 수 있도록 여유 있게 가져온다.
        recent_activities = garmin_client.get_recent_activities(user_id, limit=10, offset=0)
    except RuntimeError as exc:
        logging.info("Auto-sync 스킵 (user_id=%s): Garmin 활동 조회 실패 — %s", user_id, exc)
        return

    synced_map = sync_db.get_synced_map(user_id)
    logging.info(
        "Auto-sync (user_id=%s): 최근 활동 %d개 조회, 기존 동기화 %d개, 기준 시각=%s",
        user_id, len(recent_activities), len(synced_map), auto_sync_enabled_at,
    )

    for act in recent_activities:
        # ── 러닝 활동만 처리 ────────────────────────────────────────────────
        activity_type = act.get("activityType")
        type_key = activity_type.get("typeKey", "") if isinstance(activity_type, dict) else str(activity_type)
        if "running" not in type_key.lower():
            continue

        garmin_activity_id = str(act["activityId"])

        # ── 활성화 이전 활동: 미동기화면 진행, 이미 동기화됐으면 스킵 ──────────
        # auto_sync_enabled_at은 UTC('YYYY-MM-DD HH:MM:SS').
        # startTimeGMT(UTC)와 비교해야 정확하다.
        # startTimeGMT가 없으면 startTimeLocal(로컬)로 폴백하되,
        # 로컬 시간은 UTC와 시간대 차이가 있어 비교가 부정확할 수 있다.
        if auto_sync_enabled_at:
            activity_start_time = act.get("startTimeGMT") or act.get("startTimeLocal") or ""
            # Garmin API가 간혹 ISO 형식("2026-05-27T06:30:00.0")으로 반환할 때
            # 공백 형식("2026-05-27 06:30:00")으로 정규화해 비교한다.
            activity_start_time = activity_start_time.replace("T", " ").split(".")[0]
            if activity_start_time and activity_start_time < auto_sync_enabled_at:
                if garmin_activity_id in synced_map:
                    # 활성화 이전 + 이미 동기화됨 → 스킵
                    logging.info(
                        "Auto-sync 스킵 (user_id=%s, garmin=%s): 활성화 이전 + 기동기화 (%s)",
                        user_id, garmin_activity_id, activity_start_time,
                    )
                    continue
                # 활성화 이전이지만 미동기화 → 계속 진행해서 업로드
                logging.info(
                    "Auto-sync 진행 (user_id=%s, garmin=%s): 활성화 이전이나 미동기화 (%s)",
                    user_id, garmin_activity_id, activity_start_time,
                )

        # ── 이미 동기화된 활동 처리 ──────────────────────────────────────────
        if garmin_activity_id in synced_map:
            strava_id = synced_map[garmin_activity_id]
            exists = strava_client.activity_exists(user_id, strava_id)
            if exists is not False:
                # True(존재) 또는 None(확인 불가) → 재업로드 안 함
                logging.info(
                    "Auto-sync 스킵 (user_id=%s, garmin=%s): 이미 동기화됨 (strava=%s, exists=%s)",
                    user_id, garmin_activity_id, strava_id, exists,
                )
                continue
            # Strava에서 삭제된 것이 확인됨 → DB 기록 제거 후 재업로드
            logging.info(
                "Auto-sync 재업로드 (user_id=%s, garmin=%s): Strava 활동 %s 삭제됨",
                user_id, garmin_activity_id, strava_id,
            )
            sync_db.clear_sync_record(user_id, garmin_activity_id)

        # ── 업로드 ─────────────────────────────────────────────────────────
        logging.info("Auto-sync 업로드 시작 (user_id=%s, garmin=%s)", user_id, garmin_activity_id)
        try:
            fit_bytes = garmin_client.download_fit_bytes(user_id, garmin_activity_id)
            upload_id = strava_client.upload_fit(
                user_id, fit_bytes, f"activity_{garmin_activity_id}.fit"
            )
            sync_db.record_upload_start(user_id, garmin_activity_id, upload_id)
            upload_result = strava_client.poll_upload(user_id, upload_id)
            sync_db.record_upload_complete(user_id, garmin_activity_id, upload_result["activity_id"])
            logging.info(
                "Auto-sync 완료: user_id=%s, garmin=%s → strava=%s",
                user_id, garmin_activity_id, upload_result["activity_id"],
            )
        except Exception as exc:
            logging.error(
                "Auto-sync 업로드 실패 (user_id=%s, garmin_id=%s):\n%s",
                user_id, garmin_activity_id, traceback.format_exc(),
            )
            sync_db.record_upload_error(user_id, garmin_activity_id, str(exc)[:200])


def start() -> None:
    """
    스케줄러를 시작하고 2분 간격 자동 동기화 작업을 등록한다.
    app.py에서 호출된다. debug 모드에서는 Werkzeug 자식 프로세스에서만 호출해야 한다.
    """
    _scheduler.add_job(
        _run_auto_sync,
        trigger="interval",
        minutes=2,
        id="auto_sync",
        replace_existing=True,
        # 이전 실행이 끝나지 않아도 새 실행을 최대 1개만 허용.
        # 기본값(1)과 동일하지만 명시해 의도를 분명히 한다.
        # Garmin 다운 + Strava 타임아웃(최대 30초)이 겹쳐도 실행이 무한 누적되지 않는다.
        max_instances=1,
    )
    _scheduler.start()
    logging.info("Auto-sync 스케줄러 시작 (2분 간격)")


def stop() -> None:
    """스케줄러를 중지한다. 테스트 환경에서 정리할 때 사용."""
    if _scheduler.running:
        _scheduler.shutdown()
