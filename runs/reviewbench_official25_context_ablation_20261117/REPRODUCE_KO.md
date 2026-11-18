# ReviewBench 25개 PR Diff → AST → LSP 재실행

동일 25개, 세 조건, 당시의 동결 AST·LSP 근거와 리뷰 입력 구성을 사용한다. [실험 조건](PROTOCOL_KO.md)과 [기존 결과](RESULTS_KO.md)를 참조한다.

- ReviewBench PR 메타데이터·채점기·정답표는 [고정 커밋](https://github.com/review-bench/ReviewBench/tree/03c62fe06ca42b39216402ebbcb9aec765109a59)에서 가져온다.
- PR의 공개 저장소 미러에서 BASE와 HEAD를 받아 전체 diff를 다시 구성한다. 원문 diff, PR 본문과 정답표는 Git에 복사해 넣지 않았다.
- [동결 입력 자료](reproduction/inputs.json)에는 당시 사용한 변경 단위와 AST·LSP 근거, 입력과 계획 요청의 SHA-256을 담았다. 준비 단계에서 PR 묶음 25개와 계획 요청 75개의 해시를 검사한다.
- 채점 설정과 상대경로 동결 목록은 로컬에서 만든다. 입력 준비는 모델 호출 없이 실행한다.

Git, Python 3.11 이상, Node.js 24/npm을 준비하고 아래 명령을 저장소 루트에서 실행한다. 첫 준비에서 공개 GitHub 미러, npm 및 tiktoken에 대한 네트워크 접근이 필요하다.

    cd runs/reviewbench_official25_context_ablation_20261117
    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install -r requirements-reproduction.txt
    python reproduce.py check-recorded-scores
    python reproduce.py prepare
    python freeze_run.py

check-recorded-scores는 보존된 집계 카운트에서 기존 F1과 오탐 수를 검산한다. 새 리뷰를 생성하지 않는다. prepare는 API 키가 필요 없다.

새 리뷰·채점은 원래 실험 모델 gpt-5.6-sol에 접근할 수 있는 OpenAI API 자격증명을 프로세스 환경의 OPENAI_API_KEY로 주입한 뒤 실행한다. 원래 상한은 총 $70이며 코드가 실행 중 비용을 추적한다. 새 모델 응답 및 채점에 비용이 들고, 같은 모델 별칭에서도 F1은 달라질 수 있다.

    python run.py
    python reproduce.py report

실행 중 생성하는 브로커 토큰·작업 복사본·원시 응답·채점 출력은 로컬 파일이다. 집계 파일 reproduced_summary.json은 기존 RESULTS_KO.md를 덮어쓰지 않는다. 별도 실행은 새 체크아웃에서 준비한다.

## 검증한 범위

별도 경로의 새 사본에서 GitHub 미러와 고정 채점기를 받아 입력 25개, 계획 요청 75개와 동결 파일 393개를 검증했다. 로컬에 보관된 당시 계획 응답을 사용해 최종 요청 75개가 원본과 같음을 확인했다. 기존 채점 결과를 다시 집계했을 때 F1, 오탐, 비용과 조건 간 정탐 보존 수치도 일치했다. 이 검증은 API 호출 없이 수행했다. 새 모델 호출과 새 점수는 별도 실행에서 생성한다.
