# UV Tape Residue EDS v13

## 핵심 변경
- 새로고침/재접속 후 마지막 Project를 Supabase에서 자동 복원.
- localStorage에 project_id를 저장하고, 해당 프로젝트가 없으면 최신 프로젝트를 조회.
- Project/Point/Analysis/Asset 데이터는 DB/R2를 기준으로 다시 로드.
- 업로드 완료 후 바로 Verification 모달을 열고 첫 미검증 Point부터 표시.
- Point P1~P9 기본 선택.
- Treatment 입력/저장 제거.
- Verification에서 SEM, EDS/Element overview, C/N/O/Si map을 한 번에 표시.
- CV 결과와 Human 결과를 분리.
- NON-RESIDUE / RESIDUE 저장 후 다음 미검증 Point로 자동 이동.
- SKIP은 미검증 상태로 남김.
- Human verification timestamp와 review history 저장.
- 즉시 업로드 결과에서 DB UUID를 canonical point id로 사용해 asset URL이 깨지지 않도록 수정.
- Re-analyze에서도 기존 Point UUID를 유지하고 human 결과를 덮어쓰지 않음.
- 이미지 로딩 실패와 프로젝트 로딩 실패를 빈 화면/0 points로 조용히 처리하지 않음.

## DB migration
`backend/SUPABASE_V13_MIGRATION.sql`을 Supabase SQL Editor에서 실행한다.

추가되는 컬럼:
- points.human_verified_at
- points.human_updated_at
- projects.status
- projects.updated_at

추가 테이블:
- point_review_history

## 사용 흐름
New Analysis
→ PDF 업로드
→ 전체 Point 분석
→ Verification 자동 표시
→ 연구자 검증
→ 결과 저장
→ 다음 미검증 Point
→ 완료

F5 또는 재접속
→ 기존 Project 자동 복원
→ 기존 Point/분석/이미지 복원
→ Verification은 미검증 Point부터 계속

## 주의
- R2 원본 PDF는 Re-analyze에서 재사용한다.
- CV 결과는 OpenAI 개별 분류가 아니다.
- Human 결과는 CV 결과를 덮어쓰지 않는다.
- Skip은 Non-residue가 아니다.
