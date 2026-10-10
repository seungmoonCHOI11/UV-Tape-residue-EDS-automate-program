# v23.7.45 — Point 실패 격리 + PDF Point 진단

기준: v23.7.44. 기존 분석/점수/ROI/Dashboard/Verification/Compare/Reports 기능은 그대로 유지하고, **장시간 PDF 분석이 한 Point 오류 때문에 전부 중단되는 문제**를 수정했습니다.

## 이번 버전 핵심 변경
1. **한 Point가 실패해도 다음 Point를 계속 분석합니다.**
   - worker crash / worker timeout / result file 오류 / Point 저장(R2·DB) 오류를 해당 Point의 실패로 기록합니다.
   - 예: W4-P1 실패 → W4-P2 → ... → W5-P9 계속 진행.
   - Project 삭제는 정상적인 전체 작업 취소이므로 계속 진행하지 않습니다.

2. **분석 시작 전에 PDF Point 구조를 표시합니다.**
   - 상태 창에 `PDF Point 인식 27 / 27` 형태로 표시합니다.
   - PDF의 Point label을 읽을 수 있으면 label 기준으로 매칭합니다.
   - 이미지형 PDF처럼 label text가 없으면 기존과 동일하게 3-page 순서 기준 fallback을 사용합니다.
   - 설정한 Point보다 PDF에서 적게 인식되면 누락 Point ID를 상태 창에 표시합니다.

3. **실패 Point와 원인을 상태 창에 남깁니다.**
   - Point ID (예: `250W_30s_W4_P1`)
   - 실패 단계 (`worker`, `worker_timeout`, `result`, `save`)
   - 실제 예외 메시지
   - 새 분석의 상태는 기존 `projects.description -> upload_job`에 저장되므로 별도 SQL migration은 필요 없습니다.

4. **완료 상태를 더 정확하게 구분합니다.**
   - 27/27 저장 + 실패 0 + PDF 누락 0 → `completed`
   - 일부 저장 + 실패/누락 존재 → `partial`
   - 저장 0 + Point 실패 → `failed`

## 예시
27 Point PDF에서 10번째 Point(W4-P1) worker가 실패한 경우:

- PDF Point 인식: 27 / 27
- 저장 완료: 26 / 27
- 실패 Point: 1
- W4-P1의 오류 원인 표시
- W4-P2부터 W5-P9까지 계속 분석
- 최종 상태: `일부 Point만 저장됨`

반대로 PDF 자체에서 9 Point만 인식된 경우:

- PDF Point 인식: 9 / 27
- 미인식/누락 Point 목록 표시
- 인식 가능한 9 Point는 분석
- 최종 상태: `일부 Point만 저장됨`

따라서 다음부터는 **"PDF가 9개만 잡힌 것인지"와 "10번째 Point에서 worker가 실패한 것인지"를 상태 창에서 바로 구분**할 수 있습니다.

## 배포
ZIP 안 프로젝트 폴더의 **내용을 기존 GitHub 저장소 루트에 덮어쓴 뒤** Vercel + Render를 모두 재배포하세요.

- Vercel Root Directory: `frontend`
- Vercel Build: `npm run build`
- Render Root Directory: `backend`
- Render Build: `pip install -r requirements.txt`
- Render Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`

분석 작업 중에는 Render 재배포를 하지 않는 것이 안전합니다.

## 검증
- Python syntax compile: 통과.
- Point worker 실패 후 다음 Point 계속 처리: 통과.
- Point 저장 실패 후 다음 Point 계속 처리: 통과.
- 백엔드 unit test 11개: 통과 (테스트 환경에서 외부 Supabase/OpenAI import만 stub 처리).
- Next.js production build는 현재 작업 환경에 `node_modules`가 없고 외부 package download가 차단되어 실행하지 못했습니다. JSX 변경은 기존 컴포넌트 구조 안에서만 적용했습니다.

실제 Render/R2/Supabase 및 실제 연구 PDF 연결 테스트는 배포 후 확인이 필요합니다.
