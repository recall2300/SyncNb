# SyncNb

**Garmin → Strava 재업로드로 MyNB 포인트를 받자**

Garmin Connect에서 Strava로 직접 연동하면 `external_id`가 `garmin_ping_xxx` 형태가 됩니다.  
한국 앱 **MyNB**는 이 패턴을 인식하지 못해 포인트 전환이 되지 않습니다.

SyncNb는 Garmin의 FIT 파일을 직접 다운로드 → Strava Upload API로 재업로드해서  
`external_id`가 `activity_{id}.fit` 형태가 되게 만듭니다. 이 방식이면 MyNB가 정상 인식합니다.

> **전제**: Garmin → Strava 직접 연동은 반드시 꺼야 합니다 (중복 활동 방지).

---

## 목차

- [기능](#기능)
- [빠른 시작 (Windows 로컬)](#빠른-시작-windows-로컬)
- [Docker로 실행 (NAS / 서버)](#docker로-실행-nas--서버)
- [환경변수 설정](#환경변수-설정)
- [Strava API 앱 등록](#strava-api-앱-등록)
- [보안 설정 가이드](#보안-설정-가이드)
- [파일 구조](#파일-구조)
- [API 엔드포인트](#api-엔드포인트)
- [자주 묻는 질문](#자주-묻는-질문)

---

## 기능

- 🏃 최근 Garmin 러닝 활동 목록 표시 (동기화 여부 포함)
- 📤 FIT 파일 다운로드 → Strava 업로드 (단건/다건 선택)
- 🔄 자동 동기화 (2분 간격, 새 러닝 활동 자동 업로드)
- 🗺️ 활동 상세 (GPS 지도, 구간 기록)
- 👥 멀티유저 (회원가입/로그인)
- 🛡️ 관리자 패널 (사용자 관리, 동기화 기록 조회/삭제)

---

## 빠른 시작 (Windows 로컬)

### 방법 A: 스크립트로 한 번에 실행

1. 이 저장소를 클론하거나 ZIP으로 다운로드합니다.
2. `.env.example` 을 복사해 `.env`를 만들고 Strava 설정을 입력합니다:
   ```
   copy .env.example .env
   notepad .env
   ```
3. 아래 중 하나를 더블클릭하거나 실행합니다:
   - `start.bat` — 명령 프롬프트 환경
   - `start.ps1` — PowerShell 환경 (우클릭 → "PowerShell로 실행")

스크립트가 자동으로 가상환경 생성, 패키지 설치, 서버 시작을 처리합니다.  
브라우저에서 **http://localhost:5000** 을 열면 됩니다.

> **PowerShell 실행 정책 오류 시:**
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

### 방법 B: 수동 실행

```powershell
# 1. 저장소 클론
git clone <repo_url>
cd SyncNb

# 2. 가상환경 생성 및 활성화
python -m venv .venv
.venv\Scripts\activate

# 3. 패키지 설치
pip install -r requirements.txt

# 4. .env 설정
copy .env.example .env
# .env 파일을 편집기로 열어 Strava 설정 입력

# 5. 서버 실행
python app.py
# → http://localhost:5000
```

### Docker Desktop으로 로컬 테스트

Docker Desktop이 설치되어 있다면 환경 설정 없이 바로 실행할 수 있습니다:

```powershell
docker compose -f docker-compose.dev.yml up --build
# → http://localhost:5000
```

> 개발용 설정이므로 기본 관리자 계정은 `admin` / `admin123`입니다.

---

## Docker로 실행 (NAS / 서버)

### 사전 준비

1. **Strava API 앱 등록** → [Strava API 앱 등록](#strava-api-앱-등록) 참조
2. **도메인/DDNS** 준비 (HTTPS를 위해 권장)
3. Docker, Docker Compose 설치 확인

### 1단계: 환경변수 파일 설정

```bash
cp .env.example .env
nano .env   # 또는 vi .env
```

`.env` 파일을 아래와 같이 채웁니다:

```env
# Strava API
STRAVA_CLIENT_ID=12345
STRAVA_CLIENT_SECRET=abcdef...

# 외부 도메인 (HTTPS 설정 후)
STRAVA_REDIRECT_URI=https://yourdomain.com/strava/callback

# Flask 세션 키 (반드시 랜덤값으로 변경!)
# 생성: python -c "import secrets; print(secrets.token_hex(32))"
FLASK_SECRET_KEY=여기에_랜덤_문자열

# 관리자 계정
ADMIN_USERNAME=admin
ADMIN_PASSWORD=강한비밀번호!

# HTTPS 활성화 (Nginx/NAS Reverse Proxy 설정 후 true로 변경)
HTTPS_ENABLED=true

# 혼자 쓰거나 소수만 사용 시 false 권장
ALLOW_REGISTRATION=false
```

### 2단계: 컨테이너 실행

```bash
# NAS 내장 Reverse Proxy 사용 시 (권장)
docker compose up -d

# Nginx까지 같이 띄울 때 (nginx/certs/ 에 인증서 먼저 배치)
docker compose --profile nginx up -d
```

### 3단계: HTTPS 설정

#### 시놀로지 NAS
1. **DSM → 제어판 → 로그인 포털 → 고급 → 역방향 프록시**
2. 규칙 추가:
   - 소스: `https://yourdomain.com:443`
   - 대상: `http://localhost:5000`
3. **DSM → 제어판 → 보안 → 인증서**에서 Let's Encrypt 인증서 발급

#### 일반 Linux 서버 (Nginx 포함)
```bash
# nginx/nginx.conf에서 yourdomain.com 을 실제 도메인으로 수정 후
docker compose --profile nginx up -d
```

---

## 환경변수 설정

| 변수 | 필수 | 기본값 | 설명 |
|------|------|--------|------|
| `STRAVA_CLIENT_ID` | ✅ | — | Strava API 앱 클라이언트 ID |
| `STRAVA_CLIENT_SECRET` | ✅ | — | Strava API 앱 클라이언트 시크릿 |
| `FLASK_SECRET_KEY` | ✅ | `syncnb-dev-key-change-me` | 세션 서명 키. 반드시 랜덤값으로 변경 |
| `STRAVA_REDIRECT_URI` | — | `http://localhost:5000/strava/callback` | Strava OAuth 콜백 URL. 외부 배포 시 도메인으로 변경 |
| `ADMIN_USERNAME` | — | `admin` | 첫 실행 시 생성될 관리자 아이디 |
| `ADMIN_PASSWORD` | — | `syncnb-admin` | 첫 실행 시 생성될 관리자 비밀번호 |
| `HTTPS_ENABLED` | — | `false` | `true` 설정 시 세션 쿠키에 Secure 플래그 추가 |
| `ALLOW_REGISTRATION` | — | `true` | `false` 설정 시 /register 엔드포인트 비활성화 |

> ⚠️ **`FLASK_SECRET_KEY` 생성 방법**:  
> ```python
> python -c "import secrets; print(secrets.token_hex(32))"
> ```

---

## Strava API 앱 등록

1. https://www.strava.com/settings/api 접속
2. **Create & Manage Your App** 클릭
3. 정보 입력:
   - **Application Name**: SyncNb (아무 이름)
   - **Category**: 아무거나 선택
   - **Club**: 비워두기
   - **Website**: `http://localhost` (로컬 테스트) 또는 실제 도메인
   - **Authorization Callback Domain**: `localhost` (로컬) 또는 실제 도메인
4. 생성 후 **Client ID**와 **Client Secret**을 `.env`에 입력

> **외부 배포 시**: Authorization Callback Domain을 실제 도메인(예: `yourdomain.com`)으로 변경하고  
> `STRAVA_REDIRECT_URI`도 `https://yourdomain.com/strava/callback`으로 수정해야 합니다.

---

## 보안 설정 가이드

### 로컬 개발 (Windows)

별도 보안 설정 불필요. `.env`에 Strava 자격증명만 설정하면 됩니다.

### NAS/서버 배포

반드시 아래 항목을 설정하세요:

| 항목 | 설정 방법 |
|------|-----------|
| **강한 비밀번호** | `.env`의 `ADMIN_PASSWORD`, `FLASK_SECRET_KEY` 변경 |
| **HTTPS** | NAS Reverse Proxy 또는 Nginx + Let's Encrypt |
| **회원가입 제한** | `ALLOW_REGISTRATION=false` (혼자 쓰는 경우) |
| **쿠키 보안** | HTTPS 설정 후 `HTTPS_ENABLED=true` |
| **방화벽** | 공유기에서 5000 포트 직접 노출 금지, 443만 포워딩 |

### 보안 기능 (구현 완료)

- ✅ Strava OAuth CSRF 방어 (state 토큰)
- ✅ 로그인/회원가입/Garmin 로그인 Rate Limiting
- ✅ 세션 쿠키 HttpOnly + SameSite=Lax
- ✅ 보안 응답 헤더 (CSP, X-Frame-Options 등)
- ✅ Garmin MFA 세션 5분 만료
- ✅ URL 파라미터 숫자 검증
- ✅ SQL Injection 방어 (파라미터 바인딩)
- ✅ 비밀번호 bcrypt 해싱
- ✅ SQLite WAL 모드 (동시성)

---

## 파일 구조

```
SyncNb/
├── app.py              # Flask 라우트 전체 + 인증 데코레이터
├── config.py           # 환경변수 로드 + 전역 설정
├── garmin_client.py    # Garmin SSO 로그인, 활동 목록, FIT 다운로드
├── strava_client.py    # Strava OAuth, 토큰 갱신, FIT 업로드, 폴링
├── sync_db.py          # SQLite 스키마 + 모든 DB 쿼리
├── scheduler.py        # APScheduler 자동 동기화 백그라운드 작업
│
├── templates/
│   ├── login.html      # 로그인/회원가입 페이지
│   ├── index.html      # 메인 페이지 (활동 목록, 동기화)
│   └── admin.html      # 관리자 패널
│
├── static/
│   ├── css/            # 공통, 로그인, 앱, 관리자 스타일
│   └── js/             # 공통, 로그인, 메인, 관리자 JavaScript
│
├── Dockerfile          # 프로덕션 Docker 이미지 빌드
├── docker-compose.yml          # 프로덕션 Docker Compose (+ Nginx 프로필)
├── docker-compose.dev.yml      # 개발/테스트용 Docker Compose
├── nginx/
│   └── nginx.conf      # Nginx HTTPS Reverse Proxy 설정
│
├── start.bat           # Windows 로컬 실행 스크립트 (명령 프롬프트)
├── start.ps1           # Windows 로컬 실행 스크립트 (PowerShell)
├── requirements.txt    # Python 의존성
├── .env.example        # 환경변수 템플릿 (실제 값은 .env에 저장)
└── .gitignore          # .env, syncnb.db 등 민감 파일 제외
```

### 데이터 저장 위치

| 데이터 | 저장 위치 | 비고 |
|--------|-----------|------|
| 사용자/토큰/동기화 기록 | `syncnb.db` (SQLite) | Docker 볼륨으로 마운트 |
| Garmin 세션 토큰 | `~/.garminconnect/{user_id}/` | Docker named volume |
| Strava 토큰 | `syncnb.db`의 `strava_tokens` 테이블 | |

---

## API 엔드포인트

### 인증

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/login` | 로그인 페이지 |
| `POST` | `/login` | 로그인 처리 |
| `POST` | `/register` | 회원가입 (`ALLOW_REGISTRATION=true`일 때만) |
| `POST` | `/logout` | 로그아웃 |
| `GET` | `/me` | 현재 사용자 정보 + 연결 상태 |
| `POST` | `/me/password` | 비밀번호 변경 |

### Garmin 연동

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/garmin/login` | Garmin 이메일/비밀번호 로그인 |
| `POST` | `/garmin/mfa` | MFA 코드 제출 |
| `POST` | `/garmin/logout` | Garmin 연결 해제 |

### Strava 연동

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/strava/connect` | Strava OAuth 시작 |
| `GET` | `/strava/callback` | OAuth 콜백 |
| `POST` | `/strava/disconnect` | Strava 연결 해제 |

### 활동 / 동기화

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/activities` | 최근 러닝 목록 (`?limit=30&offset=0`) |
| `GET` | `/activity/<id>/details` | GPS 경로 좌표 |
| `GET` | `/activity/<id>/splits` | 구간(랩) 기록 |
| `POST` | `/sync` | FIT → Strava 업로드 |
| `POST` | `/auto-sync` | 자동 동기화 토글 |

### 관리자 (`/admin/api/*`, 관리자 권한 필요)

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/admin/api/users` | 전체 사용자 목록 |
| `PUT` | `/admin/api/users/<id>` | 비밀번호/권한 변경 |
| `DELETE` | `/admin/api/users/<id>` | 사용자 삭제 |
| `GET` | `/admin/api/users/<id>/activities` | 동기화 기록 |
| `DELETE` | `/admin/api/users/<id>/activities/<garmin_id>` | 동기화 기록 삭제 |

---

## 자주 묻는 질문

**Q. Garmin → Strava 직접 연동을 꼭 꺼야 하나요?**  
A. 예. 직접 연동을 켜두면 같은 활동이 Strava에 두 번 올라갑니다. Garmin Connect 앱에서 Strava 연동을 비활성화해주세요.

**Q. 이미 Strava에 올라간 활동이 중복으로 다시 올라가지 않나요?**  
A. Strava가 중복 활동을 감지하면 오류 메시지로 기존 활동 ID를 알려줍니다. SyncNb는 이를 파싱해 기존 활동을 성공으로 처리합니다.

**Q. Garmin 비밀번호는 어디에 저장되나요?**  
A. 저장되지 않습니다. 로그인 시 Garmin SSO를 통해 세션 토큰을 받고, 이후에는 토큰만 사용합니다.

**Q. 자동 동기화는 어떻게 동작하나요?**  
A. 프로필 → 자동 동기화 토글을 켜면 서버가 2분마다 Garmin의 최근 5개 활동을 확인합니다. 아직 Strava에 없는 러닝 활동을 자동으로 업로드합니다. 활성화 이전 활동은 올라가지 않습니다.

**Q. Docker 컨테이너를 재시작하면 Garmin 로그인이 풀리나요?**  
A. `docker-compose.yml`에서 Garmin 토큰을 named volume(`garmin_tokens`)으로 마운트하므로 재시작 후에도 로그인이 유지됩니다.

**Q. 비밀번호를 잊어버렸어요.**  
A. 관리자 계정이면 `.env`의 `ADMIN_PASSWORD`를 변경 후 `syncnb.db`를 삭제하고 재시작하세요 (모든 데이터 초기화). 일반 계정이면 관리자 패널에서 비밀번호를 재설정할 수 있습니다.

---

## 개발 환경 요구사항

- Python 3.11 이상
- 의존성: `requirements.txt` 참조
  - Flask, garminconnect, requests, python-dotenv, werkzeug, APScheduler, flask-limiter, gunicorn
