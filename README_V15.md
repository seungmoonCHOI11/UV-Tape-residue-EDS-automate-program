# UV Tape Residue EDS v15 — production patch

## 기준
- v13.6: 기존 production 기능 보존 기준
- v14: 기존 loading / Supabase / R2 / Re-analyze 구조 보존
- `UV-Tape-EDS-v13-point1-test-v3.zip`: **CROP 및 ROI 표시의 시각적 기준**

## 1. PDF CROP — v3 검증 좌표 고정
PyMuPDF 1x render 기준 1240 × 1754 page에서 v3 asset을 원본 PDF와 template-match하여 다음 경계를 확인했다.

### Page 1
- SEM: x=98..726, y=207..625 → 628 × 418
- Full EDS Map: x=101..1203, y=818..1552 → 1102 × 734

### Page 2
- SE: x=101..643, y=190..552 → 542 × 362
- C: x=658..1200, y=190..552 → 542 × 362
- N: x=101..643, y=566..928 → 542 × 362
- O: x=658..1200, y=566..928 → 542 × 362
- Si: x=101..643, y=943..1305 → 542 × 362
- Full Element panel은 원본 보존 asset으로만 저장하고 Verification 화면에는 표시하지 않는다.

비율 기반 crop을 사용하지 않아 Point 1에서 확인된 오른쪽/아래쪽 잘림과 panel 혼입을 방지한다.

## 2. ROI
- ROI는 고정 페이지 좌표가 아니다.
- Page 2 SE에서 각 Point의 실제 밝은 physical candidate를 검출한다.
- metadata/scale-bar 영역은 후보에서 제외한다.
- 이전 v14처럼 여러 큰 component를 하나의 ROI에 합치지 않고 가장 강한 compact physical candidate 1개를 기준으로 rectangular ROI를 생성한다.
- ROI는 candidate 크기에 비례해 padding하고 이미지 경계에 맞게 clip한다.
- SEM: **빨간 박스**
- SE/C/N/O/Si: **흰색 박스**
- Local Ring: **노란 선**
- ROI 글자는 넣지 않는다.
- SEM에서 얻은 ROI normalized coordinate를 각 element panel에 동일 투영한다.

## 3. Residue CV
기존 v14의 문제였던 단일 큰 C enrichment 값에 의한 false positive를 줄이기 위해 percent contrast를 주 판정값으로 사용하지 않는다.

주요 feature:
- SEM morphology score
- C local score
- O local score
- C/O spatial overlap
- C/O log2 local ratio 및 z-score는 진단용으로 함께 저장

결과는 `Residue / Non-residue / Review` 3상태다.
- Residue: SE morphology + C/O local evidence + spatial overlap이 함께 충족
- Non-residue: 후보와 C/O evidence가 모두 약한 경우
- Review: 증거가 서로 충돌하거나 충분히 강하지 않은 경우

`C enrichment 300%` 같은 상대 백분율은 background가 낮을 때 과장될 수 있으므로 diagnostic 값으로 clip하여 저장하며, wt%/at% 농도로 해석하지 않는다.

## 4. Verification
- SEM / Full EDS Map
- SE / C / N / O / Si 5개 개별 map
- 각 map의 동일 ROI 표시
- CV RESULT에 실제 다음 값을 표시:
  - SEM MORPHOLOGY
  - C SCORE
  - O SCORE
  - SPATIAL OVERLAP
  - Score / Confidence

`Full Element Maps — original panel`은 화면에서 제거했다.

## 5. Dashboard
기존 Point 전체 나열 대신 현재 저장된 조건을 요약한다.
- Saved Power / Time conditions
- Point 수
- Residue / Non-residue / Review
- Residue %
- Zone별 결과
- Power / Time별 결과
- 대표 Residue / Non-residue / Review 각 1개

대표 사례는 순위나 우열을 의미하지 않고 각 결과 상태를 보여주기 위한 예시다.

## 6. 기존 기능 보존
다음을 v13.6 기준으로 유지한다.
- 초기 `Project data loading` 화면
- 최신 Project 자동 로딩
- Supabase 데이터
- R2 source PDF
- Dashboard / New Analysis / Verification / AI Analysis / Gallery / Condition Compare / Reports
- Re-analyze
- Re-analyze progress overlay
- Human Verification
- 기존 Human Verification / AI metadata 보존
- 기존 upload / API 구조

Re-analyze는 R2 원본 PDF를 다시 분석하고 기존 Point가 있으면 Human/AI metadata를 유지한 채 analysis/assets만 갱신한다.

## 7. 주의
CV는 rule-based computer vision prototype이며, 자동 결과가 애매하면 Review로 남겨 Human Verification에서 확인할 수 있다.
