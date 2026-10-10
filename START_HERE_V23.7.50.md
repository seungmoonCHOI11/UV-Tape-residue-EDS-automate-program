# START HERE - v23.7.50

이번 버전은 **판정 기준 통일 + Engineering Compare + 조건 비교용 PPT/PDF**가 핵심입니다.

## 1. 판정 기준 단일화

자동 판정은 모든 화면/집계/리포트에서 동일한 현재 기준을 사용합니다.

- Residue: C ROI/Global >= 2.40x AND O ROI/Global >= 3.00x
- Ambiguous: C와 O가 모두 2.00x 이상이지만 Residue 기준 미충족
- Non-residue: C 또는 O 중 하나라도 2.00x 미만
- 사용자가 Verification에서 직접 저장한 Human Result가 있으면 그 값이 최종 Ground Truth로 우선됩니다.

Dashboard, Verification Navigator, Gallery, Condition View, Condition Compare, AI summary input, Point PPT/PDF 모두 같은 판정 규칙을 사용합니다.

## 2. Engineering Compare

평균 Score 비교를 제거하고 실제 공정 평가에 필요한 두 축으로 재구성했습니다.

1. Plasma condition comparison
   - Power / Time 조건별 Residue incidence (Residue count / measured count)
   - Non-residue / Ambiguous 별도 표시
   - Power x Time matrix
   - 27-point coverage 및 Human Verified count

2. Wafer position comparison
   - W1 = Corner
   - W4 = Edge
   - W5 = Middle
   - 조건별 각 위치 Residue incidence
   - Location Spread = max residue rate - min residue rate
   - Worst Location 표시

W1/W4/W5 이외 wafer는 Engineering Compare의 핵심 집계에서는 제외하고, 제외 개수를 Data Completeness에 표시합니다.

## 3. Reports

Reports 메뉴에 누적 workspace 전체 조건을 대상으로 한 출력이 추가되었습니다.

- Engineering Summary PPT
- Engineering Summary PDF

각 리포트는 다음을 포함합니다.

- Condition Summary
- Power x Time Matrix
- W1 Corner / W4 Edge / W5 Middle comparison
- Location Spread / Worst Location
- Coverage / Human Verification 상태
- 현재 판정 기준 및 해석 기준

기존 Point PPT / Point PDF / JSON export는 그대로 유지됩니다.

## 4. 기존 안정화 기능 유지

v23.7.48까지의 다음 기능을 그대로 유지합니다.

- 27 Point 고정 매핑
- 저장 Retry
- 최종 Supabase/R2 누락 검증
- Human ROI 보존
- 누락 Point 자동 복구
