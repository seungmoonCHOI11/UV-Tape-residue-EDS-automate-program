# UV Tape Residue EDS v16

## v16 핵심 변경

### 1. CROP
`UV-Tape-EDS-v13-point1-test-v3.zip`에서 검증된 고정 CROP 좌표를 그대로 유지합니다.

- Page 1 SEM: `98,207` → `726,625` (628 × 418)
- Page 1 Full EDS Map: `101,818` → `1203,1552` (1102 × 734)
- Page 2 SE: `101,190` → `643,552` (542 × 362)
- Page 2 C: `658,190` → `1200,552` (542 × 362)
- Page 2 N: `101,566` → `643,928` (542 × 362)
- Page 2 O: `658,566` → `1200,928` (542 × 362)
- Page 2 Si: `101,943` → `643,1305` (542 × 362)

### 2. ROI
- ROI 위치는 고정 좌표가 아닙니다.
- **Page 1 SEM**에서 Point별 residue 후보를 검출합니다.
- 하단 SEM 정보/scale-bar 영역은 후보 검출에서 제외합니다.
- 단일 밝은 점만 선택하지 않도록 multi-scale top-hat 후보를 만들고, 여러 후보가 있을 경우 SEM morphology + C/O evidence를 함께 사용해 후보를 선택합니다.
- ROI는 residue 후보를 충분히 포함하도록 rectangular contextual ROI로 생성합니다.
- SEM: 빨간 박스
- SE/C/N/O/Si: 흰색 박스
- 노란 선: 시각적 참고 영역만 사용
- ROI 글자는 표시하지 않습니다.

### 3. Residue 판정
기존 Local Ring 비교를 제거했습니다.

**새 기준:**

`ROI` vs `전체 분석 이미지`

여기서 전체 분석 이미지는 하단의 scale bar / SEM 정보 / metadata 영역을 제외합니다.

C/O 각각에 대해:
- ROI median
- 전체 분석 영역 median
- robust global standard deviation
- log2 ROI/global ratio
- robust z-score
- 전체 이미지 상위 신호 대비 ROI coverage

를 계산합니다.

Residue score는:
- SEM morphology
- C global-vs-ROI evidence
- O global-vs-ROI evidence
- C/O high-signal overlap

을 조합합니다.

노란 Local Ring은 **판정 계산에 사용하지 않습니다.**

### 4. Score
모든 Point에 `features.residue_score`를 생성하고 API가 이를 top-level `residue_score`로도 반환하도록 수정했습니다.
따라서 Verification / Dashboard / Gallery의 Score가 `-`로 표시되지 않습니다.

### 5. 기존 기능 유지
v15를 기반으로 다음을 그대로 유지합니다.

- Project 자동 불러오기
- 초기 Loading 화면
- Dashboard
- New Analysis
- Verification
- AI Analysis
- Image Gallery
- Condition Compare
- Reports
- Supabase
- R2 source PDF
- Re-analyze
- Re-analyze progress
- Human Verification
- 기존 Human / AI metadata 보존
- 기존 API / upload 구조

## 테스트
- v3 Point-1 source PDF를 실제 worker에 넣어 분석 실행
- Page 1/2 crop dimensions 확인
- SEM 기반 ROI 생성 확인
- C/O global-vs-ROI feature 생성 확인
- residue score 생성 확인
- Python backend syntax compile 통과

프론트엔드는 현재 실행 환경에서 npm registry 설치가 시간 제한으로 완료되지 않아 `next build`까지는 실행하지 못했습니다. 소스 변경 후 JSX 범위/변수 참조를 정적으로 점검했습니다.
