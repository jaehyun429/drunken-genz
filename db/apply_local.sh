#!/usr/bin/env bash
# 로컬 Postgres(pgvector) 에 Supabase 흉내 → 소윤 실제 테이블 → 소윤 임시 테이블 → 001 → 002 → 003 → 004 순서로 적용. Docker Desktop 이 켜져 있어야 함.
#   bash db/apply_local.sh            # 컨테이너 없으면 만들고, 있으면 그대로 적용
#   DB_RESET=1 bash db/apply_local.sh # 컨테이너 지우고 처음부터
set -euo pipefail
cd "$(dirname "$0")"
NAME=genz-pg; PORT="${PG_PORT:-54329}"
if [[ "${DB_RESET:-}" == "1" ]]; then docker rm -f $NAME >/dev/null 2>&1 || true; fi
if ! docker ps -a --format '{{.Names}}' | grep -qx $NAME; then
  docker run -d --name $NAME -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=genz -p $PORT:5432 pgvector/pgvector:pg16 >/dev/null
  echo "컨테이너 생성. 준비 대기..."; sleep 5
fi
docker start $NAME >/dev/null 2>&1 || true
until docker exec $NAME pg_isready -U postgres -q; do sleep 1; done
for f in 000_local_supabase.sql 000_soyun_record.sql 000_stub_soyun_tables.sql 001_jaehyun_schema.sql 002_jaehyun_rls.sql 003_jaehyun_question_1114.sql 004_jaehyun_analysis_1116.sql; do
  echo ">> $f"; docker exec -i $NAME psql -U postgres -d genz -v ON_ERROR_STOP=1 -q < "$f"
done
echo "DATABASE_URL=postgresql://postgres:postgres@localhost:$PORT/genz"
