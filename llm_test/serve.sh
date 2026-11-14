#!/usr/bin/env bash
# GPU 서버(B200 x1, VRAM 180GB)에서 vLLM으로 후보 모델 띄우기. 한 번에 하나만.
#   bash serve.sh qwen27      # Qwen3.8-27B BF16 (Apache 2.0)            ← 1순위
#   bash serve.sh hcx32       # HyperCLOVA X SEED Think 32B (네이버 자체 라이선스)
#   bash serve.sh exaone33    # EXAONE 4.5 33B BF16 (※ 비상업 라이선스, 비교용)
#   bash serve.sh kanana      # Kanana-2 30B-A3B (카카오 자체 라이선스)
#   bash serve.sh qwen30a3    # Qwen3-30B-A3B-Instruct-2507 (속도 비교용)
#   bash serve.sh gptoss      # gpt-oss-120b (Apache 2.0, 큰 모델 비교용)
#
# 처음 한 번:
#   export HF_HOME=/NHNHOME/WORKSPACE/26moe002_H/jhjeong/hf     # 모델 캐시를 워크스페이스에 (컨테이너 복구 시 안 날아가게)
#   pip install -U uv && uv venv ~/vllm-env && source ~/vllm-env/bin/activate
#   uv pip install -U vllm --torch-backend=auto          # NGC 이미지의 torch와 섞이지 않게 venv에
# 띄운 뒤 확인: curl localhost:8000/v1/models
set -euo pipefail

export HF_HOME="${HF_HOME:-/NHNHOME/WORKSPACE/26moe002_H/jhjeong/hf}"
PORT="${PORT:-8000}"
MAXLEN="${MAXLEN:-16384}"
UTIL="${UTIL:-0.90}"
COMMON=(--host 0.0.0.0 --port "$PORT" --max-model-len "$MAXLEN" --gpu-memory-utilization "$UTIL"
        --served-model-name local --enable-prefix-caching)

case "${1:-}" in
  qwen27)   MODEL="Qwen/Qwen3.8-27B" ;                      EXTRA=(--reasoning-parser qwen3) ;;
  qwen30a3) MODEL="Qwen/Qwen3-30B-A3B-Instruct-2507" ;      EXTRA=() ;;
  hcx32)    MODEL="naver-hyperclovax/HyperCLOVAX-SEED-Think-32B" ; EXTRA=(--trust-remote-code) ;;
  exaone33) MODEL="LGAI-EXAONE/EXAONE-4.5-33B" ;            EXTRA=(--trust-remote-code) ;;
  kanana)   MODEL="kakaocorp/kanana-2-30b-a3b-instruct" ;   EXTRA=(--trust-remote-code) ;;
  gptoss)   MODEL="openai/gpt-oss-120b" ;                   EXTRA=() ;;
  *) echo "사용법: bash serve.sh {qwen27|hcx32|exaone33|kanana|qwen30a3|gptoss}"; exit 1 ;;
esac

# reasoning 모델에서 JSON 강제가 먹으려면 아래 플래그 필요 (vLLM 문서)
if [[ " ${EXTRA[*]} " == *"--reasoning-parser"* ]]; then
  EXTRA+=(--structured-outputs-config.enable_in_reasoning=True)
fi

echo ">> vllm serve $MODEL  (HF_HOME=$HF_HOME)"
exec vllm serve "$MODEL" "${COMMON[@]}" "${EXTRA[@]}"
