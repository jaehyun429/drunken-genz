# llm_test — 회의 전 로컬 LLM 비교

서버: B200 x1 (VRAM 180GB), RAM 150GB, Ubuntu 24.04 NGC PyTorch 이미지. 27~33B는 BF16 그대로, 100B급 MoE도 올라감.

## 1. 서버 준비 (컨테이너 안, 처음 한 번)
```bash
export HF_HOME=/NHNHOME/WORKSPACE/26moe002_H/jhjeong/hf      # ~/.bashrc에도 추가. 모델 캐시가 워크스페이스에 남게
pip install -U uv && uv venv ~/vllm-env && source ~/vllm-env/bin/activate
uv pip install -U vllm --torch-backend=auto openai    # Blackwell(B200)은 CUDA 12.8+ 휠이 자동 선택됨
df -h /NHNHOME/WORKSPACE/26moe002_H/jhjeong                   # 모델당 55~70GB. 여유 확인
```

## 2. 모델 띄우기
```bash
bash serve.sh qwen27          # 1순위. 다 뜨면 "Application startup complete" 뜸 (첫 다운로드 55GB)
curl localhost:8000/v1/models
```
후보 목록과 순서는 `serve.sh` 상단 주석. 한 번에 하나만. 끄려면 Ctrl+C 후 다음 모델.

## 3. 테스트 20개 돌리기
컨테이너 포트가 8000은 안 열려 있음 (열린 건 Jupyter 8889, VSCode 8081, 접속용 30001). 두 가지 방법:

**A. 컨테이너 안에서 바로 돌리기** (제일 간단. VSCode 터미널이나 Jupyter 터미널에서)
```bash
cd llm_test && python run_eval.py --base http://localhost:8000/v1 --tag qwen27
```
결과 `results/qwen27.md`를 VSCode에서 열어 보거나 30001 포트로 내려받기.

**B. 노트북에서 돌리기** (30001이 SSH라면)
```bash
ssh -p 30001 -L 8000:localhost:8000 <계정>@<플랫폼 접속 주소>   # 터널 열어두고
python run_eval.py --base http://localhost:8000/v1 --tag qwen27
```

모델 바꿔 띄운 뒤 `--tag`만 바꿔서 반복. `results/` 안 .md들을 나란히 보면 됨.

## 4. 케이스 구성 (cases.jsonl, 20개)
- `q01~q12` 질문 생성: 경험 1건 → 해볼 것 2 + 상상해보기 2, source 필수 (CLAUDE.md 9번)
- `s01~s04` 정리 확인: 글 → 칩 후보 안에서만 고르기 + 후속 질문 2
- `r01~r04` 관련 경험 재정렬: 질문 + 후보 카드 5장 → 관련순 + reason 40자. `expect_top`/`expect_excluded`로 자동 채점

자동 검사는 형식·개수·후보 밖 칩·비현실 가정 키워드까지만. 질문이 "말이 되는지"는 .md 보고 사람이 판단.

## 5. 옵션
- `--only question` 한 과제만
- `--think` reasoning 켜기 (기본 끔. 켜면 느려지고 JSON 안정성 비교용)
- Ollama도 됨: `--base http://localhost:11434/v1 --model qwen3:8b`

## 6. 회의에서 볼 것
1. 통과율 (형식 지킴) 2. 평균 초 (정리 확인은 5초 제한) 3. 질문 톤·비현실 가정 여부 4. related에서 관련 없는 카드를 걸러내는지 5. 라이선스 (Qwen·gpt-oss만 Apache 2.0, 나머진 자체 약관·NC)
