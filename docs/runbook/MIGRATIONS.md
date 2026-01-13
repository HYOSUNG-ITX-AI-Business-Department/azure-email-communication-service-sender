# DB 마이그레이션 전략 & 운영 런북 (Issue #8)

이 문서는 **프로덕션 환경에서 DB 스키마를 안전하게 변경/배포/롤백**하기 위한 절차를 제공합니다.

> 원칙
> - 프로덕션은 반드시 `DEBUG=false`를 유지합니다.
> - 스키마 변경은 “애플리케이션 자동 생성(autocreate)”이 아닌 **마이그레이션 도구(예: Alembic)** 로만 수행합니다.
> - 모든 마이그레이션은 **롤백(다운그레이드) 가능**하거나, 불가능할 경우 **복구 절차(PITR/백업 복구)** 가 문서화되어야 합니다.

---

## 1) 마이그레이션 도구(Alembic) 기본 워크플로우

### 1.1 초기 셋업(한 번만)

1. Alembic 설치 및 초기화
   - Python 프로젝트 환경에 `alembic` 추가
   - `alembic init alembic`
2. DB URL 설정
   - Alembic 설정에서 `SQLALCHEMY_DATABASE_URL`(또는 앱 설정에서 주입) 사용
3. 모델 메타데이터 연결
   - `env.py`에서 SQLAlchemy `Base.metadata`를 연결해 autogenerate가 동작하도록 구성

> 주의: autogenerate는 “초안 생성” 용도입니다. 생성된 마이그레이션은 항상 사람이 검토해야 합니다.

### 1.2 마이그레이션 생성(Generate)

스키마 변경(모델 변경) 후 마이그레이션 생성:

```bash
alembic revision --autogenerate -m "add processing visibility zset"
```

생성된 revision 파일을 검토합니다.

- 컬럼 타입/nullable/default 의도대로인지
- 인덱스/제약조건(Unique/FK) 포함 여부
- 데이터 마이그레이션이 필요한 경우(Backfill) 별도 단계 고려

### 1.3 마이그레이션 적용(Apply)

스테이징에서 먼저 적용:

```bash
alembic upgrade head
```

프로덕션 적용:

- **점진 배포**(API 먼저, 워커는 이후)와 함께 수행
- 마이그레이션이 long-running이면 lock/timeout/트래픽 영향을 고려

### 1.4 마이그레이션 롤백(Downgrade)

```bash
alembic downgrade -1
```

- 다운그레이드는 **사전 테스트된** 경우에만 수행합니다.
- 다운그레이드가 위험하거나 불가능한 마이그레이션(예: 데이터 삭제/리쉐이핑)은 아래 “복구 절차”를 사용합니다.

---

## 2) 배포 절차(Deployment Steps)

### 2.1 배포 전 체크리스트

- [ ] 프로덕션에서 `DEBUG=false` 보장(환경변수/시크릿/런타임 설정)
- [ ] 마이그레이션 파일이 리뷰/승인되었고, 다운그레이드/복구 절차가 준비됨
- [ ] 백업/PITR(시점 복구) 준비 상태 확인
- [ ] `/health`, `/ready` 엔드포인트가 스테이징에서 정상

### 2.2 권장 배포 순서

1. **DB 마이그레이션 적용** (필요 시)
2. API(Stateless) 배포
3. Worker 배포
4. 모니터링
   - 에러율(5xx), DLQ 증가, 큐 적체, 처리 지연

> 호환성: 스키마 변경은 가능하면 **Expand → Migrate → Contract** 패턴을 사용해 롤링 배포 중 호환성을 유지합니다.

---

## 3) 롤백 전략(Rollback Strategy)

### 3.1 애플리케이션 롤백

- 가장 먼저 API/Worker 이미지를 이전 버전으로 롤백합니다.
- 롤백 후 `/health`, `/ready` 확인

### 3.2 DB 롤백

- 가능한 경우: `alembic downgrade` 수행
- 불가능/위험한 경우: DB 백업 복구 또는 PITR로 복구

---

## 4) 운영 런북(Incidents & Schema Drift)

### 4.1 스키마 드리프트(Drift) 의심 시

증상:

- 신규 컬럼/테이블이 예기치 않게 생성됨
- 마이그레이션 히스토리와 실제 스키마 불일치

대응:

1. 즉시 `DEBUG` 설정 및 배포 파이프라인 확인(프로덕션에서 `DEBUG`가 활성화되지 않았는지)
2. Alembic revision 상태 확인
   - `alembic current`
   - `alembic heads`
3. 드리프트 원인 파악
   - 수동 DDL 수행 여부
   - 잘못된 환경변수/설정
4. 교정 전략
   - 안전한 경우: 새로운 마이그레이션으로 정리
   - 위험한 경우: 백업 기반 복구 후 재배포

### 4.2 장애 대응 체크리스트(P1/P2)

- [ ] 영향 범위(발송 지연/중복 발송/데이터 손상) 파악
- [ ] DLQ/재시도 증가 확인
- [ ] 최근 배포/마이그레이션 여부 확인
- [ ] 롤백 필요성 판단
- [ ] 사후 분석(RCA) 문서화

---

## 5) CI/CD 게이트(권장)

실제 배포 워크플로우가 있는 경우, 배포 직전에 **최종 환경에서 DEBUG 활성화를 금지**하는 게이트를 추가해야 합니다.

- 예: 배포 job 시작 전에 `DEBUG` 값 검증
- 예: Helm/Kustomize/Task definition 등 최종 산출물에서 DEBUG 활성화 탐지 시 실패

이 레포에서는 최소 안전장치로 “Deny DEBUG (true)” 워크플로우를 통해 DEBUG 활성화 설정이 레포에 들어오는 것을 차단합니다. (문서에서는 `DEBUG=<true>` 같은 형태로 표현합니다.)