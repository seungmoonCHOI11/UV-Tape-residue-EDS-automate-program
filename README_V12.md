# UV Tape Residue EDS v12

## 주요 변경
- Residue / Non-residue 개별 판정은 OpenAI가 아니라 CV + Human Review가 담당.
- Page 2의 SE map을 기준 좌표로 사용해 residue 후보 ROI를 검출하고 C/N/O/Si map에 동일 위치를 적용.
- 전체 이미지 평균 대신 residue 주변 local-ring background와 robust z-score를 사용.
- C/O spatial overlap과 SEM morphology를 함께 사용해 binary Residue / Non-residue를 산출하고 confidence를 별도로 표시.
- 원본 SEM/EDS/element map은 그대로 보존하고, 사람이 보기 위한 enhanced/overlay 이미지를 별도 생성.
- SEM 및 Element Map UI는 `object-fit: contain`으로 전체 이미지를 표시하며 crop하지 않음.
- Image Gallery는 실제 저장 이미지를 사용하고 카드 클릭 시 AI Review로 이동하지 않고 image lightbox를 표시.
- OpenAI는 AI Analysis에서 전체 실험 결과의 조건별 경향/이상점/연구 해석에만 사용.
- R2에 저장된 원본 PDF를 다시 다운로드해 현재 알고리즘으로 재분석하는 `Re-analyze` 기능 추가.
- SHA-256이 동일한 원본 PDF는 동일 조건의 반복 업로드에서 R2 source 객체를 재사용하여 중복 저장을 줄임.

## 주의
- EDS map의 밝기는 wt%/at% 농도로 직접 해석하지 않음.
- enhanced image는 시각화용이며 숫자 계산에는 raw map을 사용.
- v12 CV 분류값은 연구용 초기 알고리즘이며 human review로 검증/보정해야 함.
