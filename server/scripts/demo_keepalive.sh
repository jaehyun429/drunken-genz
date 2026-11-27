#!/usr/bin/env bash
# 확인용 페이지를 꺼지지 않게 띄운다 (GPU 서버용). 서버나 터널이 죽으면 5초 뒤 다시 켠다.
# 11/24 회의 중에 서버가 내려가 링크가 안 열려서 만들었다 (이슈 #22).
#
# 쓰는 법 (server 폴더에서):
#   TUNNEL_CMD="ngrok http --url=<고정 주소> 18080" nohup bash scripts/demo_keepalive.sh > keepalive.log 2>&1 &
#   TUNNEL_CMD 를 안 주면 임시 Cloudflare 터널을 연다 (다시 켜질 때마다 주소가 바뀐다. 주소는 tunnel.log 에)
# 끄기:  pkill -f demo_keepalive.sh; pkill -f "uvicorn app.main:app"; pkill -f "$TUNNEL_CMD"
# Qwen(vLLM)은 여기서 띄우지 않는다. 공용 GPU 라 사람이 직접 띄운다. 죽어 있으면 keepalive.log 에 한 줄 남긴다.
set -u
cd "$(dirname "$0")/.."
PORT="${PORT:-18080}"
LLM_PORT="${LLM_PORT:-18000}"
TUNNEL_CMD="${TUNNEL_CMD:-$HOME/cloudflared tunnel --url http://localhost:$PORT}"

keep() {  # keep <이름> <로그 파일> <명령...>
  local name="$1" logf="$2"; shift 2
  while true; do
    echo "[$(date '+%m-%d %H:%M:%S')] $name 시작"
    "$@" >> "$logf" 2>&1
    echo "[$(date '+%m-%d %H:%M:%S')] $name 이(가) 끝남 (종료 코드 $?). 5초 뒤 다시 켠다"
    sleep 5
  done
}

keep "서버" demo.log .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$PORT" &
# shellcheck disable=SC2086
keep "터널" tunnel.log $TUNNEL_CMD &

while true; do   # 10분마다 Qwen 이 살아 있는지만 본다
  sleep 600
  curl -s -m 5 "http://localhost:$LLM_PORT/v1/models" > /dev/null || echo "[$(date '+%m-%d %H:%M:%S')] Qwen 이 응답하지 않음 (포트 $LLM_PORT). 질문·분석이 실패한다"
done
