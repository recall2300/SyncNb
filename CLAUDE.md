# SyncNb — 프로젝트 컨텍스트

## 프로젝트 목적

Garmin Connect → Strava 직접 연동으로 올라간 활동은 Strava의 `external_id`가 `garmin_ping_xxx` 형태가 된다.
한국 앱 **MyNB**는 이 패턴을 인식하지 못해 포인트 전환이 되지 않는다.

SyncNb는 Garmin의 FIT 파일을 직접 다운로드 → Strava Upload API로 재업로드해서
`external_id`가 `activity_{id}.fit` 형태가 되게 만든다. 이 방식이면 MyNB가 정상 인식한다.

**전제**: Garmin→Strava 직접 연동은 끈다 (중복 활동 방지).

---

## 실행 방법

```
cd c:\Code\SyncNb
.venv\Scripts\activate
python app.py
# → http://localhost:5000
```

Python 버전: 3.13 (py -3.13 으로 venv 생성됨)

---

## 파일 구조 및 역할

| 파일 | 역할 |
|------|------|
| `app.py` | Flask 라우트 전체. 인증 데코레이터(`login_required`, `admin_required`), 모든 API 엔드포인트 |
| `garmin_client.py` | Garmin SSO 로그인, MFA 처리, 활동 목록/상세/FIT 다운로드. 토큰은 `~/.garminconnect/{user_id}/`에 저장 |
| `strava_client.py` | Strava OAuth, 토큰 갱신, FIT 업로드, 업로드 폴링, 활동 존재 확인 |
| `sync_db.py` | SQLite 스키마(schema v3) + 쿼리. 멀티유저 구조, 모든 테이블에 `user_id` 컬럼 |
| `config.py` | 환경변수 로드. `.env`에서 Strava 자격증명, Flask secret key, 기본 관리자 계정 읽음 |
| `templates/login.html` | 로그인/회원가입 페이지. 오렌지 그라디언트 배경 |
| `templates/index.html` | 메인 페이지. 활동 목록, 동기화, 행 클릭 상세 모달, 프로필 모달, 관리자 링크 |
| `templates/admin.html` | 관리자 패널. 사용자 목록, 활동 기록 조회/삭제, 비밀번호 재설정, 관리자 권한 토글 |
| `syncnb.db` | SQLite DB (런타임 생성) |
| `.env` | Strava 자격증명 (git에 올리지 않음) |

---

## DB 스키마 (schema v3)

```sql
users (id, username, password_hash, is_admin, created_at)
strava_tokens (user_id PK, athlete_id, access_token, refresh_token, expires_at)
synced_activities (id, user_id, garmin_activity_id, strava_activity_id, strava_upload_id, synced_at, status)
-- status: 'pending' | 'uploaded' | 'error'
```

---

## 주요 API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET/POST | `/login` | 로그인 페이지 / 로그인 처리 |
| POST | `/register` | 회원가입 |
| GET | `/me` | 현재 사용자 정보 + 연결 상태 |
| POST | `/me/password` | 비밀번호 변경 |
| GET | `/activities` | 최근 Garmin 러닝 목록 (동기화 여부 포함) |
| GET | `/activity/<id>/details` | Garmin 활동 상세 + 구간 기록 |
| POST | `/sync` | FIT 다운로드 → Strava 업로드 (배열로 여러 개 가능) |
| GET | `/strava/connect` | Strava OAuth 시작 |
| GET | `/strava/callback` | OAuth 콜백 |
| GET | `/admin` | 관리자 패널 페이지 |
| GET | `/admin/api/users` | 전체 사용자 목록 + 통계 |
| PUT | `/admin/api/users/<id>` | 사용자 편집 (비밀번호, 관리자 권한) |
| DELETE | `/admin/api/users/<id>` | 사용자 삭제 |
| GET | `/admin/api/users/<id>/activities` | 특정 사용자 동기화 기록 |
| DELETE | `/admin/api/users/<id>/activities/<garmin_id>` | 동기화 기록 삭제 |

---

## 기본 관리자 계정

앱 첫 실행 시 자동 생성. `.env`로 변경 가능:
- 기본 아이디: `admin`
- 기본 비밀번호: `syncnb-admin`

```
ADMIN_USERNAME=admin
ADMIN_PASSWORD=syncnb-admin
```

---

## 구현 시 해결한 핵심 문제들

1. **Strava duplicate 처리**: 오류 메시지가 HTML(`<a href='/activities/18462741778'>`) 형태로 옴.
   → `re.search(r"/activities/(\d+)", error_msg)` 로 파싱해서 기존 activity ID 추출, 성공으로 처리.

2. **`activity_exists` 반환값**: Strava 401/403 시 `False`를 반환하면 DB 기록이 잘못 삭제됨.
   → 반환 타입을 `bool | None`으로 변경. `404`만 `False`, 나머지 오류는 `None` (불확실).

3. **Strava OAuth scope**: `activity:write`만으로는 `activity_exists` 체크 시 401.
   → `activity:write,activity:read` 로 변경.

4. **Garmin MFA**: `Garmin` 객체는 JSON 직렬화 불가 → Flask session에 저장 불가.
   → 모듈 레벨 `_pending_mfa: dict[int, dict]` (user_id 키)에 인메모리 저장.

5. **멀티유저 DB 마이그레이션**: `strava_tokens`, `synced_activities`에 `user_id` 추가.
   → `PRAGMA user_version = 3` 체크 후 기존 테이블 DROP & 재생성.

6. **실시간 동기화 진행**: SSE/WebSocket 없이 row별 즉시 업데이트.
   → JS에서 `for (const id of checked)` 루프로 순차 fetch, 각 완료 시 바로 `renderTable()`.

---

## requirements.txt

```
flask>=3.0.0
garminconnect>=0.2.22
requests>=2.31.0
python-dotenv>=1.2.2
werkzeug>=3.0.0
```

---

## 현재 구현 완료 상태

- [x] Garmin 로그인 (이메일/비밀번호 + MFA)
- [x] Strava OAuth 연동
- [x] 러닝 활동 목록 (동기화 여부 표시)
- [x] FIT 다운로드 → Strava 업로드
- [x] 중복 활동 처리 (기존 Strava 활동 ID 추출)
- [x] Strava에서 삭제된 활동 감지 (DB 기록 자동 제거)
- [x] 실시간 진행 바 + 행별 상태 업데이트
- [x] 비고 컬럼 (결과 이유, Strava 링크)
- [x] 멀티유저 (회원가입/로그인/세션)
- [x] 활동 상세 모달 (메트릭 그리드 + 구간 기록)
- [x] 프로필 모달 (비밀번호 변경)
- [x] 관리자 패널 (사용자 관리, 동기화 기록 조회/삭제)

## 답변 규칙

- 답변, Thinking과정은 한글출력.