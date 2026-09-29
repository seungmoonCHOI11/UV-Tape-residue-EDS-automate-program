# UV-Tape Residue EDS — v12 SEM-driven ROI / Residue Classification

이번 버전의 핵심은 **PDF CROP과 분석 ROI를 완전히 분리**한 것입니다.

## 1. PDF CROP — 고정 Pixel 좌표

현재 Bruker EDS PDF의 1240 × 1754 page layout을 기준으로 측정한 CROP 좌표를 사용합니다.
페이지가 다른 렌더 크기로 생성되어도 실제 page 크기에 비례해 자동 스케일합니다.

### Page 1
- SEM: x=98, y=207, w=628, h=418
- Full EDS Map: x=101, y=818, w=1102, h=734

### Page 2
- SE: x=101, y=190, w=542, h=362
- C: x=658, y=190, w=542, h=362
- N: x=101, y=566, w=542, h=362
- O: x=658, y=566, w=542, h=362
- Si: x=101, y=943, w=542, h=362

이 좌표는 **PDF에서 이미지 자체를 가져오는 CROP 기준**입니다.
Residue ROI와는 관계가 없습니다.

## 2. ROI — SEM에서 실제 Residue 위치 검출

각 Point마다 SEM CROP을 독립적으로 분석합니다.

1. SEM local contrast / top-hat 분석
2. 작은 noise 제거
3. connected component 후보 검출
4. 가장 강한 residue candidate를 시작점으로 주변의 가까운 residue fragment를 병합
5. candidate를 포함하도록 padding을 적용해 사각형 ROI 생성
6. ROI를 normalized coordinate로 변환

따라서 ROI는 Point마다 달라질 수 있으며 **고정 pixel 좌표를 사용하지 않습니다.**

## 3. C/N/O/Si에 동일 ROI 투영

SEM에서 검출한 ROI의 normalized coordinate를 각 element panel에 투영합니다.

- SE: white ROI + yellow dashed Local Ring
- C: white ROI + yellow dashed Local Ring
- N: white ROI + yellow dashed Local Ring
- O: white ROI + yellow dashed Local Ring
- Si: white ROI + yellow dashed Local Ring
- SEM: red ROI

ROI라는 글자는 이미지에 넣지 않습니다.

## 4. Residue / Non-residue 판정

최종 사용자 분류는 binary입니다.

- `Residue`
- `Non-residue`

별도로 `High / Medium / Low` confidence를 저장합니다.

판정 구조:

`SEM residue candidate` → spatial gate

`C local enrichment + O local enrichment` → tape-residue chemical evidence

즉 C/O가 이미지 전체에서 높다는 이유만으로 Residue가 되지 않고,
**SEM에서 검출된 실제 residue 위치와 동일한 ROI에서 C/O evidence가 나타나는지**를 확인합니다.

현재 기준:
- Strong SEM candidate + Strong C + Strong O → Residue / High
- Strong SEM + Strong C + supporting O → Residue / Medium
- Strong SEM + supporting C + Strong O → Residue / Medium
- 그 외 → Non-residue, confidence는 evidence 강도에 따라 분리

이 threshold는 연구 데이터가 누적되면 human verification 결과를 이용해 calibration할 수 있도록 features를 함께 저장합니다.

## 5. Local Ring

ROI 바깥의 가까운 주변 영역을 Local Ring으로 만들고 background reference로 사용합니다.

저장되는 주요 feature:
- ROI median
- Local Ring median
- contrast %
- robust z-score
- high-signal coverage
- SEM morphology confidence
- candidate count / candidate area ratio

EDS map 밝기는 **직접적인 농도값이 아니라 상대 X-ray signal/count 기반 지표**로 취급합니다.

## 6. OpenAI 역할

OpenAI는 개별 Point의 Residue / Non-residue 판정에 사용하지 않습니다.

개별 Point 판정은 CV/OpenCV + SEM/EDS evidence로 수행하고,
OpenAI는 전체 실험 조건의 경향, 이상점, 연구 해석에 사용합니다.

## 7. Verification 화면

구성:

- SEM / Residue Overlay
- Full EDS Map
- SE
- C
- N
- O
- Si
- Residue Analysis

`Full Element Maps — original panel` 카드는 제거했습니다.

## 8. 검증

Point 1 테스트 PDF로 worker를 실행하면 SEM에서 실제 residue 위치에 red ROI가 생성되고,
C/N/O/Si에는 같은 위치에 white ROI와 yellow Local Ring이 표시됩니다.

테스트 결과 예시:
- SEM morphology confidence ≈ 0.80
- C z-score ≈ 0.96
- O z-score ≈ 0.34
- 결과: Residue / High

이 수치는 테스트 Point 1에 대한 예시이며 전체 실험 결과를 의미하지 않습니다.
