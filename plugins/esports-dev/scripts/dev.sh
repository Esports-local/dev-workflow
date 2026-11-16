#!/usr/bin/env bash
# esports-ui / esports-api / esports-analyzer 를 한 번에 띄우고 끄는 스크립트
# (실제 구성은 esports-api/docker-compose.yml: ui :8000, api :8002, analyzer :8001, MinIO :9000/:9001)
#
#   dev.sh up        빌드 후 백그라운드로 전부 띄움 (기본값)
#   dev.sh down      전부 끔 (데이터 볼륨은 유지)
#   dev.sh restart   다시 띄움
#   dev.sh logs [서비스]   로그 따라보기 (예: dev.sh logs analyzer)
#   dev.sh ps        상태 확인
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
  down)    compose down ;;
  restart) compose down && up ;;
  logs)    compose logs -f --tail=100 "$@" ;;
  ps)      compose ps ;;
  *)       echo "사용법: dev.sh [up|down|restart|logs [서비스]|ps]"; exit 1 ;;
esac
