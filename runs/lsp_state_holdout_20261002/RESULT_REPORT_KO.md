# 새 PR 확인 결과: 일반 상태 참조 정책 S의 이득을 확인하지 못함

이전에 쓰지 않은 pytest PR4개에서 사례별 함수명을 지정하지 않는 상태 참조 보충 정책 S를 새 B와 비교했다. 양방향 성공은 B2/12, S0/12이고 회귀 검출은 둘 다4/12다. 기본 B를 유지한다. 이전의 사람이 지정한 경로 실험에서 얻은 개선이 이번 일반 정책으로 재현됐다고 볼 수 없다.

| 지표 | B | S |
|---|---:|---:|
| 근거 있는 회귀 검출 | 4/12 | 4/12 |
| 정상 방향 오탐 | 0/12 | 0/12 |
| 양방향 모두 성공한 반복 | 2/12 | 0/12 |
| 근거 있는 정답 방향 판정 | 6/24 | 4/24 |
| 기권 | 18/24 | 18/24 |
| operational valid | 24/24 | 24/24 |

각 사례 셀은 회귀 검출 · 정상 방향 성공 · 양방향 성공, 각각 최대3이다.

| 사례 | B | S |
|---|---|---|
| J01 예외 chain roundtrip, PR5787 | 3 · 2 · 2 | 3 · 0 · 0 |
| J02 unrelated package 수집, PR6197 | 0 · 0 · 0 | 0 · 0 · 0 |
| J03 --runxfail의 skip 위치, PR7432 | 1 · 0 · 0 | 1 · 0 · 0 |
| J04 generated fixture 표시, PR8399 | 0 · 0 · 0 | 0 · 0 · 0 |

같은4PR의3반복이며 독립12PR이 아니다. 모두 pytest이고 이전에 본 파일도 포함한다. PR 수준의 미사용 사례 확인이며 새 저장소/파일에 대한 일반화 시험은 아니다. 정책 구현은 source fetch/witness 전에 algorithm_seal.json으로 고정했지만 selection에서 공개 patch를 확인했으므로 dataset-blind 알고리즘 선택도 아니다. 새 모델 결과에 맞춰 정책을 수정하지 않았다.

## 무엇을 검증했나

B는 이전의 changed/entry 우선 native definition 정책, S는 동일한 B source에서 전역 subscript/get/pop 키와 non-call receiver 필드를 AST로 골라 최대3개의 실제 textDocument/references를 추가한다. 다른 함수의 참조 source를 우선 공급하고 이미 보이는 변경 head unit을 보존한다. 관련성이 낮은 source 및 base source는 예산에서 교체할 수 있다. 특정 PR ID, 함수명 또는 상태키를 코드에 지정하지 않는다. LSP가 찾지 못한 대상을 AST resolution으로 대체하지 않는다. 원시 LSP 반환과 source admission/eviction은 별도로 저장했다.

각 arm source≤1600, observations≤400, B native definition≤12와 S 추가 references≤3. 48 logical reviews /48 새 물리 호출, gpt-5.6-sol low, workers4, tools0, retry0, seed미통제. 각 방향/arm3반복이며 이전 응답을 재사용하지 않는다. 같은 source여도 추가 observation이 달라 동일 prompt 공유는0이었다.

source/input/code를 호출 전에 freeze 및 seal. labels/witness/test code/prior response는 reviewer prompt에서 제외. 모든 리뷰를 작성자가 비블라인드로 검사했고 운용상 실패와 기권은 분모에 유지했다. 호출실패/누락/schema/ledger/인용오류/모델도구호출은0. source/manifest/input/algorithm/preparation seal 모두 검증했다. issue 파일·행 존재 확인은 localization 정확도 측정이 아니다.

## 실패한 연결

J01은 entry.__dict__, value.__dict__, rep.longrepr 등의 native references가 corpus source로 해석되지 않아 양방향 source가 B와 같았다. S는 unresolved observation만 추가했다. 이 사례에서 잃은 양방향2회는 새 source가 B를 훼손해서 생긴 결과가 아니다. 비통제 seed, 적은 반복, 추가 정적 observation이 있는 상태이므로 원인을 특정하거나 일반적으로 S가 더 나쁘다고 단정하지 않는다. 필요했던 연결은 ExceptionChainRepr 생성자 및 실제 chain/section 보존 구현과 report 생성 타입이다.

J02는 self.obj 참조로 여러 collector 함수를 보충했지만 pytest entry에서 실제 collector/_getobj 구현으로 이어지는 dispatch를 확정하지 못했다. S의 정상 방향2응답은 '한 테스트가 통과한다'고 단정했지만 이 연결이 없어 grounded correctness를 주지 않았다. 정답 witness가 실제로 통과했어도 모델 source의 공백을 채우는 근거로 쓰지 않았다.

J03은 상태 키에서 setup producer를 더 공급했다. 한 회귀 응답은 새 elif가 runxfail 뒤에서 skip 위치 교정을 차단함을 설명했다. 그러나 B도1회 검출했고, 정상 방향은 item.reportinfo()의 실제 파일/줄과 initial longrepr→terminal output 연결을 확정하지 못해 양방향 성공0이었다. producer를 넣는 것만으로 최종 출력 경로가 닫히지 않았다.

J04는 self.obj 참조가 collector 쪽 source로 이어졌다. 필요한 최종 소비자는 fixture name의 '_'와 verbose 옵션으로 목록을 필터링하는 코드인데 이 경로는 공급되지 않았다. 생성된 이름이 보이는 것만으로 '--fixtures' 명령의 숨김/표시 결과를 증명하지 못했다.

추가 native query24개 중11개에 corpus 안 source target이 있었다. 8방향 중5방향 source가 바뀌었으며 새 source unit26개를 넣고 기존24개를 교체했다. 이는 참조가 실제로 탐색됐다는 증거이고, 필요한 runtime/output 경로를 찾았다는 증거는 아니다. 공통 B query96개를 새로 수행하고 S에서 재사용해 전체 준비 semantic query는120개다. server error0.

## 판정 범위

J01 issue는 chain 구조/순서/cause relationship의 roundtrip 상실이다. exception 이름 두 개가 출력 문자열에서 완전히 사라졌다는 주장으로 확대하지 않는다. J01 정상 판정2개는 직접 chain tuple 직렬화/복원 계약으로 인정했지만 runner 타입 binding 및 전체 생성자 구현을 독립 인증한 결과가 아니다. J03의 B/S 각1개는 고정 unconditional skip contract에서 필요한 correction이 새 elif에 의해 차단되는 직접 회귀로 인정한다. 전체 report 생성/terminal formatting을 별도로 증명했다는 뜻은 아니다. 구체 판정은 adjudication_notes.json에 기록했다.

J02 S의 no_supported_issue2개는 verdict_supported=False로 표시했다. 이들은 운용상 valid이며 safe 방향에서 issue를 보고하지 않았으므로 오탐으로 세지 않지만, 근거 있는 정상 성공으로도 세지 않는다. 모델의 기권을 정답으로 교정하거나 숨긴 witness로 부족한 dispatch를 보완하지 않았다. B/S 모두 같은 기준으로 채점했다.

## 실행 증거와 다음 설계

실제 공식 base snapshot과 code patch를 사용하고, 양쪽 revision을 독립 프로세스에서2회씩 실행했다.16개 runtime witness 모두 고정 계약의 수정 전/후 차이를 확인했다. J01의 초기 chain2→restored chain없음/2, J02 collection exit2/0, J03 skip파일 skipping.py/test_sample.py, J04 default generated fixture 표시True/False이며 verbose표시는둘다True였다. Python3.11 호환 alias, assertion rewriting 비활성화, 메모리상 generated version metadata만 보완하고 production source를 고치지 않았다. 초기 metadata parent attribute 누락으로 verbose header에서 INTERNALERROR가 발생한 증거는 보존하고 모델 호출 전에 수정/재실행했다. 이는 모델 호출 실패와 다른 준비 단계 오류다.

다음 개발은 상태 변수 참조를 넓히는 것보다 공개 계약의 최종 출력 소비자를 먼저 찾는 방식이 우선이다. 보고 위치는 reportinfo/longrepr/terminal, fixture 표시는 name/verbose filter, chain은 report 생성 타입/constructor/serialization을 각각 이어야 한다. 계약별 의미를 사람이 알고 target을 지정한 이전 실험과 달리 자동 정책이 이 경로를 선택할 수 있는지를 먼저 개발 사례에서 검증해야 한다. 현재 S를 기본으로 채택하거나 새 사례에서의 개선을 주장하지 않는다.

freeze.json/inputs/packets/lsp_traces는 호출 전 입력, raw는 원문, audit_all.json은 운용 감사, reviewed_ids.json은48건 검토, annotations_all.json/results_all.json은 채점, analysis_details.json은 source/navigation 통계, validation_results.json은 최종 검증이다. 이전 실험 디렉터리는 수정하지 않았다.
