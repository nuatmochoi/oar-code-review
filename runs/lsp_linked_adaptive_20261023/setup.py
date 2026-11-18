import hashlib,json,sys
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1]
sys.path.insert(0,str(ROOT))
base=json.loads((ROOT/'runs/lsp_callback_routes_20261002/schema.json').read_text())
base['properties']['checks']['maxItems']=3;base['properties']['review']['properties']['issues']['maxItems']=3
(R/'schema_final.json').write_text(json.dumps(base,indent=2))
base['required'].append('context_requests');base['properties']['context_requests']={'type':'array','maxItems':6,'items':{'type':'object','additionalProperties':False,'required':['operation','file','line','column','reason'],'properties':{'operation':{'type':'string','enum':['definition','references']},'file':{'type':'string'},'line':{'type':'integer','minimum':1},'column':{'type':'integer','minimum':1},'reason':{'type':'string'}}}}
(R/'schema_first.json').write_text(json.dumps(base,indent=2))
(R/'PROTOCOL_KO.md').write_text('''# 연결 정보 × 후보별 추가 조회: Python 12 PR 실험

2026-10-23. 사용자 승인에 따른 새 실험이며 기존 frozen run은 수정하지 않는다.

## 설계

2×2: P0 연결 설명 없이 소스 제공 / P1 동일 소스와 native 조회·인수 연결 설명, Q0 고정 후속 탐색 / Q1 첫 리뷰의 근거 요청을 우선 실행. 4구성 모두 실제 LSP 초기6회+후속6회 이하, 리뷰2회, 같은 모델 gpt-5.6-sol low. 모델 자체 도구 호출은0; 구조화 요청을 실험 runner가 실제 Pyright로 실행한다. Q0도 같은 후보를 유지하며 2차 판단한다. 따라서 추가 모델 호출 횟수 자체를 통제한다.

P는 관계 표현의 요인이다. 선택 정책과 원문은 공통이며 P만으로 검색 우선순위를 변경하지 않는다. Q는 추가 조회의 우선순위를 변경한다. Q1 요청이 없거나 부적합하면 공통 자동 탐색의 남은 순서를 사용한다. 조회 실패·빈 결과·비정의 위치는 모두 기록한다. native definition이 매개변수/할당 위치를 반환하면 그 위치를 감싸는 코드 본문을 공급할 수 있으나 이를 실제 callable target으로 단정하지 않는다.

공통 개선: 삭제 전용 diff를 base/head 동명 함수로 대응시키고, 전체 함수 삭제는 base-only source로 보존한다. 중첩 함수 선언과 소속 헤더를 보존한다. 소스 최대5200 reference tokens, 관측800, LSP최대12, 깊이3. 두 단계 각각 같은 상한을 적용하고 최종 source는 초기 source를 포함한다. 이전2600토큰 실험보다 상한을 늘렸으므로 과거 결과와 직접 개선율을 계산하지 않는다. 실제 tokens·관계 설명량·query수·시간도 별도 보고한다.

## 순서 및 선정

기존 SymPy 삭제/튜플 사례와 작은 실제 Pyright 별칭 fixture에서 모델 없이 개발한다. transport smoke는 이미 본 입력으로만 수행한다. 정책·prompt·schema를 seal한 뒤 프로젝트에서 미사용 PR12개를 공개 benchmark에서 선정한다. 공개 문제/patch를 읽고 재현 가능한 계약을 정하는 목적 표본이며 무작위·블라인드·저장소 holdout이 아니다. 새 사례 선택 후 정책 수정0; 수정 필요 시 평가를 중단하고 기존 시도 보존 및 재분류한다.

모든 PR의 원본/공식 수정본을 각각 실행해 결함·정상 계약을 확인하고 production patch 외 수정0. witness, gold patch labels, 테스트 코드 및 결과를 reviewer에 전달하지 않는다. 공개 계약/고정 입력만 전달한다. 진입점/필수 root 누락은 preparation failure로 분모에 남기고 모델 호출하지 않는다. 미사용12 PR×2방향×4구성×3반복=288 review trajectories, 정상 준비 시576 fresh physical model calls. 중간 실패는 이후 호출을 생략하며 분모 유지, 재시도0. 기존 응답 재사용0. seed통제없음. 네 구성·반복의 실행 순서를 섞는다.

## 판정

최종 grounded detection, safe 오탐, 정상판단, abstention, 양방향 성공을 측정한다. 후보 최초 발견과 보류→지적/반박/유실 전환도 구분한다. 결과를 생성한 모델 자신을 정답 채점자로 사용하지 않는다. 작성자가 public input·실제 제공 근거·failure scenario를 대조하며 비블라인드라는 한계를 보고한다. 반복3개는 PR3개가 아니다. claim의 조건부 코드 인과가 충분하면 framework 전체 실행 인증을 요구하지 않지만 witness로 제공 근거 공백을 메우지 않는다. safe에서 재지적 없는 것만으로 결함 탐지가 개선됐다고 하지 않는다.

Python 전용 AST 실행 구조 설명은 이번 요인이 아니며 후속 독립 ablation이다. RL학습과 다언어 일반화도 이번 범위가 아니다. 현장 Python 피드백은 사례가 확인되지 않은 정성정보이며 성과 수치에 넣지 않는다.
''')
print('schemas/protocol ready')
