FROM python:3.13-slim

WORKDIR /app

# 비-root 유저 생성 (보안: root로 실행하지 않음)
# root 권한으로 실행 시 컨테이너 탈출 취약점 악용 위험이 높아진다.
RUN groupadd -r syncnb && useradd -r -g syncnb -m -d /home/syncnb syncnb

# 의존성 먼저 복사해 Docker 레이어 캐시 활용
# requirements.txt 가 바뀌지 않으면 pip install 단계를 재실행하지 않는다.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사 (.dockerignore 로 불필요한 파일 제외 가능)
COPY . .

# Garmin 토큰 저장 디렉토리 생성 및 소유권 설정
RUN mkdir -p /home/syncnb/.garminconnect && \
    chown -R syncnb:syncnb /app /home/syncnb

USER syncnb

# Garmin 세션 토큰이 syncnb 유저의 홈(~/.garminconnect/)에 저장되도록 HOME 설정
ENV HOME=/home/syncnb

EXPOSE 5000

# Gunicorn으로 프로덕션 서버 실행
#
# --workers 1:
#   flask-limiter의 memory:// 스토리지는 단일 프로세스 내에서만 카운터를 공유한다.
#   workers > 1 이면 각 워커가 독립 카운터를 가져 rate limit 효과가 희석된다.
#   (예: workers=2 이면 실제로는 2배 요청까지 허용됨)
#   SyncNb는 소수 사용자 대상으로 동시 요청이 많지 않아 1개로 충분하다.
#
# --timeout 120:
#   Garmin FIT 다운로드 + Strava 업로드 + 완료 폴링(최대 30초)이 순차 실행되므로
#   기본값(30초)보다 넉넉하게 설정한다.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
