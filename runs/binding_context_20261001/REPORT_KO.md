# 객체·callback 연결 검색 결과

이전 근거표 prompt/schema를 고정하고 기존 정의 탐색(D)과 factory·인자 주입·callback 참조를 추가한 검색(B)을 비교했다. gpt-5.6-sol low, source evidence 1,600 reference tokens, BM25 prefix 800, 후보 edge 최대12, 깊이3이다. 각 기능의 독립 효과를 분리한 실험은 아니다.

**callback 소스 확보 기능은 확인했지만, 기존 14쌍 전체의 엄격한 성능 개선은 확인하지 못했다.** 두 검색 모두 쌍 성공10/14, 근거 있는 결함 검출11/14, 안전 오탐0/14다. HTTP 경로를 동일하게 선언한 보조 P08에서만 D의 기권을 B가 근거 있는 결함 검출로 바꿔 쌍 성공0/1→1/1이었다.

원래 F08에서 B는 producer body를 확보한 뒤 경고했으나, 입력에 없는 요청 경로가 IMG_EXTENSIONS에 해당한다고 근거표에 가정했다. producer source coverage는 개선됐지만 실제 분기 조건 전체가 입증되지 않아 조건부 경고로 분류했다. P08에서 이 경로를 명시하면 실제 header 변화까지 확인된다. source를 더 가져왔다고 완전한 배포 조건이 자동으로 채워지지는 않는다.

현재 결정은 callback 인자 참조를 컨텍스트 수집 기능의 후보로 유지하고, 전체 B 정책의 기본 교체는 보류하는 것이다. 실제 receiver 생성·주입 source와 요청 경로 등 공개 배포 입력이 있는 독립 사례에서 재검증해야 한다. factory/주입은 기능 fixture에서 동작했지만 이번 리뷰 결과의 추가 이득은 확인되지 않았다.

## 이미 관측한 14쌍

| 검색 | 근거 있는 결함 검출 | 안전 오탐 | 기권(world) | 모두 맞힌 쌍 | 유효 리뷰 |
|---|---:|---:|---:|---:|---:|
| definition | 11/14 | 0/14 | 5/28 | 10/14 | 28/28 |
| binding | 11/14 | 0/14 | 4/28 | 10/14 | 28/28 |

## 원래 callback 사례 F08

| 검색 | 근거 있는 결함 검출 | 안전 오탐 | 기권(world) | 모두 맞힌 쌍 | 유효 리뷰 |
|---|---:|---:|---:|---:|---:|
| definition | 0/1 | 0/1 | 1/2 | 0/1 | 2/2 |
| binding | 0/1 | 0/1 | 0/2 | 0/1 | 2/2 |

## 고정 .png 경로를 선언한 보조 사례 P08

| 검색 | 근거 있는 결함 검출 | 안전 오탐 | 기권(world) | 모두 맞힌 쌍 | 유효 리뷰 |
|---|---:|---:|---:|---:|---:|
| definition | 0/1 | 0/1 | 1/2 | 0/1 | 2/2 |
| binding | 1/1 | 0/1 | 0/2 | 1/1 | 2/2 |

## 연결 정보와 한계

F08의 deployment.poll_image는 cache_control(request, missing_image)를 직접 호출한다. B는 missing_image가 괄호 없이 callback 인자로 전달되는 참조를 읽고 producer 본문을 추가했다. 이전 D의 결함 world에서 빠진 `web.Response(status=404, headers={})`가 B에 들어갔다. 안전 world의 producer no-cache는 D에도 이미 보였으므로 그 입력은 동일했다.

F04의 apply_update(connection)는 전달받은 connection.update를 호출하며, corpus에 apply_update의 호출 지점이 없다. unused InfinityConnection import만으로 객체 타입을 정하지 않았다. B에서도 실제 binding은 unresolved이고 모델 입력이 D와 동일하다. 이 기권을 해결하려면 실제 receiver 생성·주입 코드 또는 명시적 배포 binding 계약이 필요하다. 검색기를 더 공격적으로 만들어서는 입증할 수 없다.

단순 무조건부 factory, local alias, 보이는 call-site의 parameter 주입, 명시적 function/bound-method callback에 대한 기능 검사를 했다. 조건부 factory, 재할당·branch 쓰기, global 이름을 가리는 parameter, 무타입 외부 인자는 미해결로 두는 검사를 포함했다. 이는 작은 기능 fixture이며 자연 PR 성능 증거가 아니다. 클래스 필드의 주입, global 객체 생성, 상속·동적 override·monkeypatch 전체를 해석하지 않는다. 정적 연결은 runtime 실행 보장이 아니다.

원래 F08의 HTTP 경로는 source에 없다. 이를 원래 데이터에서 임의로 채우지 않았다. 보조 P08에서는 동일 source를 사용하면서 양쪽 검색 조건에 모두 `/generated.png`라는 고정 요청 입력을 scope로 선언했다. 원본 middleware와 producer AST 본문을 실행한 8개 witness 결과는 모두 예상과 일치했다. P08은 독립 holdout이 아니라 정보 완성 진단이며, callback body 검색 효과를 요청 경로 누락과 구분하기 위한 것이다. witness·labels는 모델에 제공하지 않았다.

## 호출과 감사

동일한 패킷의 26개 world는 prompt/schema까지 같으므로 응답 한 개를 두 arm에서 공유했다. 공유 mapping을 호출 전에 freeze했다. 서로 다른 입력에 대한 호출만 추가했고 raw 응답을 실제 호출인 것처럼 복제하지 않았다. 논리적 리뷰는60개이며 물리적 호출 계획은34회다. 동일 prompt의 무작위 출력 차이를 검색 개선 효과로 세지 않았다.

| 물리적 호출 조건 | 호출 | 성공 | 입력 tokens | cached 입력 | 출력 tokens |
|---|---:|---:|---:|---:|---:|
| definition | 30 | 30 | 336,534 | 208,000 | 12,739 |
| binding | 4 | 4 | 45,051 | 33,280 | 1,837 |

binding 행은 공유한 definition 호출을 제외한 추가 물리적 호출만 집계했다. 이 표에서 arm 전체 논리적 비용을 직접 비교하면 안 된다. 각 world는 두 arm 모두 논리적으로 한 번 호출하며, 실제 공급 소스와 출력 길이가 달라 token 사용은 다를 수 있다. provider usage에는 Codex 시스템 입력이 포함되며 cached 입력은 입력의 일부다. 금액으로 환산하지 않았다.

원본 source hash가 이전 corpus와 동일함을 준비 시 검사했다. source/token cap, freeze hash, response schema, 인용 ID, status/list, 근거표 issue_indices·changed 관계와 도구 호출을 감사했다. 전체 근거표와 리뷰는 작성자가 실제 소스와 대조했다. labels·이전 리뷰·실행 결과는 검색 질의나 모델 입력에 사용하지 않았다.

물리적 호출34/34는 정상 완료됐고 관측된 도구 호출은0회다. 공유 응답까지 포함한60개 논리적 리뷰의 schema·인용·status/list·근거표 이슈 관계 검사에 무효가 없었다. source/token cap 및 freeze hash도 유지됐다. 기능 검사10개와 보조 witness8회가 통과했다. 형식 검사를 통과한 원래 F08의 경로 가정은 작성자 판정에서 제외했으므로 자동 검증만으로 근거의 사실성을 보장하지 않는다.

## 해석 범위

F6/H4/N4는 모두 이전에 관측한 사례다. 이미 알려진 실패를 바탕으로 검색기를 개선한 회귀 검사이므로 독립 일반화 증거가 아니다. P08도 F08 source 재사용이다. source에 실제 연결 정보가 존재하는 경우와 없는 경우를 구분하는 engineering 검증이며, 단일 모델·단일 시행·작성자 판정이다. 고정 배포 scope에 대한 정답 기준을 실제 제품 전체 API 리뷰에 그대로 적용하면 안 된다.

## 자료

- 정책, freeze와 공유 mapping, resolver 검사
- 결과, 작성자 판정, 감사, 보조 witness
- [검색기](binding.py), [실행기](experiment.py)
