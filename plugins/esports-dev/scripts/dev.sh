#!/usr/bin/env bash
# esports-ui / esports-api / esports-analyzer 를 한 번에 띄우고 끄는 스크립트
# (실제 구성은 esports-api/docker-compose.yml: ui :8000, api :8002, analyzer :8001, MinIO :9000/:9001)
#
#   dev.sh up        빌드 후 백그라운드로 전부 띄움 (기본값)
#   dev.sh down      전부 끔 (데이터 볼륨은 유지)
#   dev.sh restart   다시 띄움
#   dev.sh logs [서비스]   로그 따라보기 (예: dev.sh logs analyzer, 평가 실행기는 dev.sh logs worker)
#   dev.sh ps        상태 확인
#
# up 은 맥에서 평가 실행기(dev-workflow/evals/worker.py)도 백그라운드로 켠다. 실행기는 /skills/ 화면에서
# "평가 실행 허용"을 켜야 평가를 가져간다 (claude 구독 한도를 쓰므로 기본은 꺼짐). dev-workflow 가 없으면 건너뛴다
set -euo pipefail

# 세 레포가 나란히 있는 폴더를 찾는다: ESPORTS_ROOT → 지금 폴더 → 지금 폴더의 위 (레포 안에서 부른 경우)
if [ -n "${ESPORTS_ROOT:-}" ]; then
  ROOT="$ESPORTS_ROOT"
elif [ -d "$PWD/esports-api" ]; then
  ROOT="$PWD"
elif [ -d "$PWD/../esports-api" ]; then
  ROOT="$(cd "$PWD/.." && pwd)"
else
  echo "[error] esports-api 폴더를 찾을 수 없습니다. 세 레포가 있는 폴더에서 부르거나 ESPORTS_ROOT 를 지정하세요"; exit 1
fi
API_DIR="$ROOT/esports-api"

for d in esports-api esports-ui esports-analyzer; do
  [ -d "$ROOT/$d" ] || { echo "[error] $ROOT/$d 폴더가 없습니다"; exit 1; }
done

if ! docker info >/dev/null 2>&1; then
  # 이 맥은 OrbStack. 없으면 Docker Desktop
  if command -v orb >/dev/null 2>&1; then
    echo "[info] OrbStack 을 켭니다..."
    orb start >/dev/null 2>&1 || open -a OrbStack
  elif open -Ra OrbStack 2>/dev/null; then
    echo "[info] OrbStack 을 켭니다..."
    open -a OrbStack
  elif open -Ra Docker 2>/dev/null; then
    echo "[info] Docker Desktop 을 켭니다..."
    open -a Docker
  else
    echo "[error] OrbStack 이나 Docker Desktop 을 찾을 수 없습니다"; exit 1
  fi
  for i in $(seq 1 90); do  # 최대 3분 대기
    docker info >/dev/null 2>&1 && break
    [ "$i" -eq 90 ] && { echo "[error] Docker 가 3분 안에 준비되지 않았습니다"; exit 1; }
    sleep 2
  done
  echo "[info] Docker 엔진 준비 완료"
fi

if [ ! -f "$API_DIR/.env" ]; then
  echo "[info] esports-api/.env 가 없어 .env.example 을 복사합니다"
  cp "$API_DIR/.env.example" "$API_DIR/.env"
fi
[ -f "$ROOT/esports-analyzer/.env" ] || echo "[warn] esports-analyzer/.env 없음 → 번역(GEMINI_API_KEY)은 안 됩니다"

compose() { (cd "$API_DIR" && docker compose "$@"); }

WORKER="$ROOT/dev-workflow/evals/worker.py"
WORKER_PID="$ROOT/dev-workflow/evals/.worker.pid"
WORKER_LOG="$ROOT/dev-workflow/evals/worker.log"

worker_running() { [ -f "$WORKER_PID" ] && kill -0 "$(cat "$WORKER_PID")" 2>/dev/null; }

start_worker() {
  [ -f "$WORKER" ] || { echo "  worker    건너뜀 (dev-workflow 폴더 없음)"; return; }
  if worker_running; then echo "  worker    이미 실행 중 (pid $(cat "$WORKER_PID"))"; return; fi
  nohup python3 -u "$WORKER" --api http://localhost:8002 >> "$WORKER_LOG" 2>&1 &
  echo $! > "$WORKER_PID"
  echo "  worker    켜짐 (pid $!) · 평가는 화면에서 '평가 실행 허용'을 켜야 돈다"
}

stop_worker() {
  if worker_running; then
    pkill -TERM -P "$(cat "$WORKER_PID")" 2>/dev/null || true  # 돌던 run.py 도 같이 끈다
    kill "$(cat "$WORKER_PID")" 2>/dev/null || true
    echo "평가 실행기를 껐습니다"
  fi
  rm -f "$WORKER_PID"
}

wait_for() {  # 이름 주소
  local name=$1 url=$2
  printf "  %-9s " "$name"
  for _ in $(seq 1 60); do
    if curl -s -o /dev/null "$url"; then echo "OK  $url"; return; fi
    sleep 2
  done
  echo "응답 없음 ($url) → dev.sh logs $name"
}

up() {
  compose up --build -d
  echo "서버 응답 기다리는 중..."
  wait_for analyzer http://localhost:8001/health
  wait_for api      http://localhost:8002/health
  wait_for ui       http://localhost:8000/
  start_worker
  echo
  echo "화면:     http://localhost:8000"
  echo "api 문서: http://localhost:8002/api/docs/"
  echo "MinIO:    http://localhost:9001"
  echo "로그:     dev.sh logs   /   끄기: dev.sh down"
}

cmd=${1:-up}
shift || true
case "$cmd" in
  up)      up ;;
  down)    stop_worker; compose down ;;
  restart) stop_worker; compose down && up ;;
  logs)    if [ "${1:-}" = worker ]; then tail -n 100 -f "$WORKER_LOG"; else compose logs -f --tail=100 "$@"; fi ;;
  ps)      compose ps ;;
  *)       echo "사용법: dev.sh [up|down|restart|logs [서비스]|ps]"; exit 1 ;;
esac
