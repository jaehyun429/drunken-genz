#!/usr/bin/env bash
# GPU 서버에서 BGE-M3 임베딩 서버 띄우기 (vLLM, OpenAI 호환 /v1/embeddings). LLM(serve.sh, 18000)과 같이 띄운다.
#   PORT=18001 bash serve_embed.sh
# 확인: curl localhost:18001/v1/embeddings -H 'Content-Type: application/json' \
#        -d '{"model":"BAAI/bge-m3","input":["카페 알바 바리스타"]}' | head -c 200
# 서버 .env: EMBED_BACKEND=openai EMBED_BASE_URL=http://localhost:18001/v1 EMBED_MODEL=BAAI/bge-m3
set -euo pipefail
export HF_HOME="${HF_HOME:-/NHNHOME/WORKSPACE/26moe002_H/jhjeong/hf}"
PORT="${PORT:-18001}"
# 568M 모델이라 VRAM 은 조금만. LLM 이 0.90 을 쓰니 여기는 0.05 (약 9GB) 면 넉넉
UTIL="${UTIL:-0.05}"
exec vllm serve BAAI/bge-m3 --task embed --host 0.0.0.0 --port "$PORT" \
  --gpu-memory-utilization "$UTIL" --max-model-len 2048
