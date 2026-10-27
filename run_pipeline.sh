#!/bin/zsh
# 자막 수집(유튜브 IP 차단 시 20분 대기 후 재시도) → Claude 추출 을 순서대로 실행.
# 두 스크립트가 videos.csv 를 동시에 쓰면 서로 덮어쓰므로 반드시 순차 실행.
# 사용: ./run_pipeline.sh [자막 목표 개수=1300] [추출 최대 개수=2000] [요청 간격 초=8]
set -u
cd "$(dirname "$0")"
TARGET=${1:-1300}
EXTRACT_LIMIT=${2:-2000}
SLEEP=${3:-8}
LOG=pipeline.log
echo "=== pipeline start $(date '+%F %T') target=$TARGET ===" | tee -a $LOG

have() { ls transcripts 2>/dev/null | wc -l | tr -d ' '; }
tries=0
while [ "$(have)" -lt "$TARGET" ] && [ $tries -lt 24 ]; do   # 최대 24회 × 30분 = 12시간 대기
  need=$(( TARGET - $(have) ))
  echo "--- fetch: 보유 $(have), 추가 필요 $need ($(date '+%T'))" | tee -a $LOG
  # -u: 출력 버퍼링 끔 (stderr 의 차단 메시지와 stdout 순서가 섞여 tail 검사가 빗나가던 문제)
  python3 -u fetch_transcripts.py --limit $need --shuffle --sleep $SLEEP >> $LOG 2>&1
  rc=$?
  if [ $rc -eq 2 ]; then   # fetch_transcripts.py 가 IP 차단으로 중단
    tries=$((tries+1)); echo "--- IP 차단. 30분 대기 ($tries/24)" | tee -a $LOG; sleep 1800
  elif tail -5 $LOG | grep -q "자막을 받을 pending 영상이 없습니다"; then
    break
  fi
done
echo "--- fetch 종료: 자막 $(have)개 ($(date '+%T'))" | tee -a $LOG

echo "--- extract 시작 (모델: $(grep '^CLAUDE_MODEL' .env | cut -d= -f2)) $(date '+%T')" | tee -a $LOG
python3 -u extract_experience.py --limit $EXTRACT_LIMIT --shuffle >> $LOG 2>&1
echo "=== pipeline end $(date '+%F %T') ===" | tee -a $LOG

# 이후 지도 단계(수동 실행):
#   python3 tag_cards.py                      # 카드별 태그·요약 → map/tags_raw.json (처리한 카드는 건너뜀)
#   python3 build_map.py                      # 임베딩 → t-SNE → 섬 8~12개 → 이름 → map/{map,regions}.json, index.html
#   python3 place_card.py sample_cards.csv --render   # 새 카드(팀 목 데이터)를 밑지도에 배치 → map/index_users.html
