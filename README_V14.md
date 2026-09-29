# UV Tape Residue EDS v14 — GitHub production patch

## v14 핵심 변경

### 1. Full EDS / Full Element Maps 원본 보존
- Full EDS Map은 Page 1 원본 crop을 그대로 사용.
- Full Element Maps는 Page 2의 원본 Element Map panel을 하나의 이미지로 저장.
- C/N/O/Si 개별 crop을 다시 조립해서 Full Element Maps를 만들지 않음.
- Si와 오른쪽 edge가 잘리지 않도록 원본 panel crop을 별도 asset으로 보존.

### 2. 개별 원소 Map + 동일 ROI
- SE / C / N / O / Si를 각각 독립 asset으로 저장.
- Page 2 SE를 residue 후보 검출의 coordinate reference로 사용.
- 동일 ROI 좌표를 C/N/O/Si에 투영.
- enhanced overlay와 raw map을 분리.

### 3. Local Ring
- ROI 주변의 dilation 영역에서 ROI 자체를 뺀 영역을 Local Ring으로 정의.
- 빨간선 = ROI
- 노란선 = Local Ring
- Local Ring은 ROI의 C/O signal을 주변 배경과 비교하기 위한 기준 영역.
- Local Ring 자체를 residue로 분류하지 않음.
- Verification 화면에서 SE/C/N/O/Si에 ROI + Local Ring을 같이 표시.

### 4. 실제 RESIDUE 판단
CV rule:
- SEM morphology
- C local evidence
- O local evidence
- SEM/C/O spatial overlap

현재 score:
`0.45 × SEM morphology + 0.20 × C + 0.20 × O + 0.15 × spatial overlap`

- Score >= 0.50 → Residue
- Score < 0.50 → Non-residue
- Confidence는 score margin / C-O agreement를 별도로 계산.
- N/Si는 보조 신호로 저장하지만 현재 residue score에는 직접 사용하지 않음.
- EDS map intensity는 wt%/at% 농도 자체가 아니라 ROI와 local background의 상대 영상 신호로 사용.

### 5. Dashboard
Dashboard를 연구 데이터용으로 변경:
- 현재 저장된 Power / Time 조건 카드
- 전체 Point / Residue / Non-residue / Ambiguous / Residue율 / 조건 수
- 조건별 판정 결과
- 전체 판정 분포
- Zone별 판정
- Power / Time 변화
- 대표 RESIDUE / 대표 NON-RESIDUE / 대표 REVIEW 사례만 표시

전체 Point를 Dashboard에 전부 나열하지 않고 대표 결과만 보여줌.

## DB migration
별도 DB migration은 필요하지 않음.
기존 v13.6 스키마를 그대로 사용하며 새 Local Ring / Full Element Map asset은 기존 `point_assets`에 저장된다.

## 배포
기존 GitHub repository에 이 ZIP의 내용을 그대로 덮어쓴다.
- frontend/ → Vercel
- backend/ → Render
- 기존 Supabase / R2 환경변수 유지

## 주의
- Dashboard는 현재 Project의 points/analysis_results를 기반으로 동적으로 계산한다.
- 기존 Point에 v14 신규 asset이 없으면 frontend fallback으로 기존 raw/enhanced asset을 사용한다.
- v14의 CV는 학습된 AI 모델이 아니라 rule-based computer vision prototype이다.
- OpenAI는 개별 Point의 Residue/Non-residue 분류기가 아니라 연구 결과 해석에 사용한다.
