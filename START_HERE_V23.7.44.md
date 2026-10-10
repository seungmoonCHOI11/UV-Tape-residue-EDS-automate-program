# v23.7.44 — PDF 하나씩 백그라운드 분석

기준: v23.7.38. New Analysis는 v23.7.25의 업로드/기판/조건 구성을 참고했습니다.
Dashboard, Verification, Gallery, 조건 보기/비교, Reports, Human ROI, C/O 점수 계산 및 Point worker 알고리즘은 v23.7.38을 유지합니다.

## 사용 방법
1. New Analysis에서 PDF **하나**를 선택합니다.
2. 기판과 Power/Time/Wafer/Point를 지정합니다. 한 PDF에 여러 조건이 있으면 **PDF 순서대로** Add condition으로 추가합니다. 서로 다른 PDF를 등록하는 기능은 제거했습니다.
3. Upload + Analyze를 누릅니다. **업로드와 작업 등록이 끝날 때까지 탭을 열어 두세요.**
4. 분석 상태 창에서 '백그라운드로 보내기'를 누르면 다른 메뉴를 사용할 수 있습니다. 상단 분석 상태 버튼으로 다시 엽니다.
5. 업로드 완료 후에는 브라우저를 닫아도 **서버가 실행 중인 동안** 분석합니다. 다시 접속하면 최근 작업 상태를 불러옵니다.
6. 현재 분석 또는 재분석이 끝난 뒤 다음 PDF를 올립니다.

## 저장 및 중단 처리
- Point 하나를 분석한 직후 R2 이미지 업로드 → Supabase 결과/이미지 경로 저장을 수행합니다.
- 새 분석은 R2와 Supabase가 모두 설정되어 있어야 시작합니다.
- '저장 완료' 수는 해당 Point의 이미지와 DB 저장을 모두 마친 뒤 증가합니다.
- 분석 중 실패하면 먼저 저장한 Point는 유지됩니다. 도중 DB 장애가 난 Point에는 일부 행/자산이 남을 수 있으므로 작업 상태의 저장 완료 수를 기준으로 확인합니다.
- 예상 Point 수보다 적으면 '일부 저장'으로 표시하며, 정상 완료로 표시하지 않습니다. 기존 PDF Point 식별 알고리즘은 변경하지 않았습니다.
- 진행 상태를 기존 Supabase projects.description에 저장합니다. 새 테이블/SQL 변경은 필요하지 않습니다.
- 서버 재시작/재배포로 작업이 사라지면 '분석 중단'으로 표시합니다. **자동 재개 기능은 포함하지 않습니다.** 저장된 원본으로 기존 Re-analyze를 사용할 수 있으나, 실행 전에 프로젝트/원본과 저장 상태를 확인하세요.
- 원본 PDF 업로드만 실패한 경우 분석은 진행하지만 상태 창에 원본 저장 경고를 표시합니다. 이 경우 원본을 이용한 재분석이 불가능할 수 있습니다.
- 완료/실패/일부 저장/중단 이후에는 상태 조회를 멈춥니다. 분석 중에도 전체 프로젝트와 이미지를 2.5초마다 갱신하지 않습니다.

## GitHub / Vercel / Render 적용
ZIP 안 프로젝트 폴더의 **내용**을 기존 저장소 루트에 덮어쓰세요. 기존 서버 환경변수/키는 유지합니다.
- Vercel: Root Directory `frontend`, Build `npm run build`.
- Render: Root Directory `backend`, Build `pip install -r requirements.txt`.
- Render Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`
- Docker 사용 시 수정된 Dockerfile을 사용합니다. 존재하지 않는 data 폴더 COPY 오류를 수정했습니다.
- **Vercel과 Render 모두 배포**해야 합니다. 분석 중에는 재배포하지 마세요.
- 이번 구조는 API 한 프로세스의 작업 실행기를 사용하므로 **서비스 인스턴스 1개 / Uvicorn worker 1개**로 사용하세요. 수평 자동 확장/다중 worker는 사용하지 않습니다.

## 변경한 Render Pro 활용
첨부 화면의 Pro는 워크스페이스 플랜이며, 포함 전송량이 5GB에서 25GB로 늘어난 것으로 표시됩니다. 서버의 RAM/CPU 요금은 별도입니다. Pro 전환만으로 512MB RAM 제한이나 무료 서비스의 실행 제약이 해소되었다고 볼 수 없습니다.
- 늘어난 전송량은 같은 워크스페이스 서비스에 적용되므로 코드에 'Pro 활성화' 설정은 필요하지 않습니다.
- 원본 해상도나 C/O 판정 알고리즘을 임의로 낮추지 않았습니다.
- RAM은 Render의 해당 백엔드 서비스 Compute Plan/Instance Type에서 별도로 확인하세요. 이 ZIP은 플랜이나 과금을 변경하지 않습니다.
- 장시간/브라우저 종료 후 분석은 서버가 계속 실행되는 사양이 필요합니다. 현재 코드의 최대 RAM이 512MB 이하라고 보장하지 않습니다.

## 검증
- Next.js production build 성공 (기존 CSS 호환성 경고 존재).
- 백엔드 테스트: Point별 저장 순서, 일부 저장, 중간 worker 실패, 이미지 저장 실패, 재시작 상태 복구, 중복 실행 차단, 다중 PDF 거부, Vercel CORS 확인.
- 실행: backend에서 `python -m unittest discover -s tests -v`.
- 테스트용 6페이지 PDF를 실제 Point worker로 처리하여 2개 Point의 이미지 생성 및 순차 저장 콜백을 확인했습니다. 실제 연구 데이터의 판정 정확도 검증은 아닙니다.
- 브라우저 실행 환경을 준비하지 못해 화면 클릭/시각 검증은 완료하지 못했습니다.
- 실제 계정의 Render/R2/Supabase 연결과 실제 EDS PDF는 여기서 실행하지 않았습니다. 배포 후 PDF 하나로 예상 Point 수, 이미지 표시, 서버 메모리 사용량을 확인하세요.

기존 데이터 및 Human ROI를 삭제하거나 재분류하는 마이그레이션은 없습니다.
