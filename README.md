# UV Tape Residue EDS · v23.9.14

23.9.12-GITHUB-CLEAN 첨부본을 기준으로 한 표시·보고서 수정본입니다.

## 적용

GitHub의 backend와 frontend를 함께 업데이트하고 Render와 Vercel 배포가 끝난 후 새로고침하세요. 진행 중인 분석이 있다면 완료 후 배포하세요.

**이 변경을 위해 PDF 재분석이나 저장 ROI 전체 재계산을 실행할 필요가 없습니다.**

## C/O 검증 맵

- 원래 계산 함수는 하단 16%와 가장자리 3픽셀을 통계에서 제외합니다. 기존 분석 함수·ROI·비율·점수·판정은 변경하지 않았습니다.
- 화면에서 같은 제외 영역의 색상을 제거하고 흑백으로 표시합니다. 조건 문구와 축척은 읽을 수 있도록 유지합니다.
- 저장된 C/O overlay에 표시 전용 변환을 적용합니다. 분석을 재실행하거나 DB의 수치를 수정하지 않습니다. 원본 R2 이미지는 덮어쓰지 않습니다.
- 첫 조회에서 표시용 이미지를 생성하고 서버에 캐시합니다. 이후 조회는 캐시를 이용합니다. ROI 저장/재분석 시 기존 캐시 무효화 흐름을 따릅니다.
- 계산의 제외 구역은 기존 고정 마스크입니다. 새로운 형식의 PDF에서 문구가 그 구역보다 위에 있다면 이 수정만으로 계산 제외를 보장할 수 없습니다. 그런 형식은 별도 확인이 필요합니다.
- 원본 SEM 갤러리와 보고서의 원본 이미지에는 표시 보정을 추가하지 않습니다.

## 보고서

- Point 상세, 원본 SEM 모음, 조건·위치 요약: 각각 PDF/PPT 출력 시 기본적으로 **문서 1개**를 제공합니다.
- 내부에서는 기존처럼 제한된 페이지 단위로 렌더링합니다. 마지막에 PDF는 페이지 결합, PPTX는 ZIP/XML 패키지 결합으로 통합합니다. PPT를 이미지로 변환하지 않아 텍스트와 도형의 편집 가능성을 유지합니다.
- 통합된 문서의 페이지 번호와 manifest의 Point→파일/페이지 매핑이 연속됩니다.
- `판정별 파일 분리`를 선택하면 선택된 판정 기준(final/auto)에 따라 Residue, Non-residue, Ambiguous별로 문서를 만듭니다. 데이터가 없는 판정의 빈 문서는 만들지 않습니다.
- 기존 판정 필터는 출력할 Point를 고르는 기능이고, 새 체크박스는 선택된 Point를 파일로 나누는 기능입니다.
- 조건·위치 요약은 분모 왜곡을 방지하기 위해 판정별 파일 분리를 적용하지 않습니다. 판정/점수 필터를 제외하는 기존 요약 규칙을 유지합니다.
- 완료 화면에서 PDF/PPT 직접 다운로드 또는 CSV·manifest를 포함한 전체 ZIP 다운로드를 선택할 수 있습니다.
- 내부 분할 파일은 최종 ZIP에 들어가지 않습니다. 완료 파일은 기존과 같이 임시 보관됩니다.

## 검증

- 새 표시/보고서/다운로드 테스트 9개 통과.
- 전체 backend: 84개 통과, 기존 실패 1개. `test_sem_to_eds_translation_registration_tracks_known_shift`가 `dx_px` 키를 기대하는 오류는 수정하지 않은 23.9.12 원본에서도 동일하게 재현했습니다. 이번 표시/출력 수정에서 ROI 정합 알고리즘을 바꾸지 않았습니다.
- frontend 테스트 18개 통과, Next.js production build 성공.
- 162 Point 합성 데이터로 PDF 162페이지, PPTX 162슬라이드 단일 출력 및 모든 Point의 manifest 매핑 검증.
- PPTX를 LibreOffice로 실제 열어 PDF로 변환한 결과도 162페이지이며 분할 경계의 Point 순서를 확인했습니다.
- 판정별 분리, 빈 판정 제외, SEM 갤러리 및 요약의 단일 출력 검증.
- 기존 분석 함수들의 AST가 원본과 동일함을 확인했습니다. 표시용 헬퍼만 추가했습니다.
- 실제 사용자의 162 Point/R2 데이터 또는 운영 Render에서의 최대 메모리는 측정하지 않았습니다. 통합 시 이미지를 다시 디코딩하지 않도록 설계했지만 모든 크기의 보고서에 대해 512MB 이하를 보장하지는 않습니다.

## v23.9.15
- Keeps v23.9.14 PDF/PPT export, merge, split-by-result, and direct document download features.
- Restores Verification display/interaction to v23.9.13 behavior.
- C/O maps in Verification are no longer display-converted to grayscale outside the analytical mask.

