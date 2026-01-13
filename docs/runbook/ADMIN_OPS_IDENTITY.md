# Admin/Ops Identity Mechanism (Issue #6)

## 현 상태

- Admin/Ops 성격의 엔드포인트는 현재 `QUEUE_STATS_ALLOWED_CALLERS`(caller_id allowlist)로만 제한됩니다.
- 인증(authentication)은 현재 `X-Caller-Id`를 “신뢰 가능한 상위 프록시/게이트웨이가 주입하는 값”으로 간주합니다.

즉, **애플리케이션 자체가 인터넷-facing에서 신뢰할 수 있는 강한 authN/authZ를 제공하지 않습니다.**

---

## 요구사항(Requirements)

### 보안/운영 요구

- **인증된 호출자만** admin/ops 엔드포인트 접근 가능
- **권한 분리**: 일반 caller와 ops caller 구분
- **회수/로테이션**: 키/토큰 회수 및 교체 가능
- **감사(Audit)**: 누가 언제 어떤 ops 데이터를 조회했는지 추적 가능(최소한 접근 로그)
- **테넌시**: caller_id는 tenant boundary로 유지

### 비기능 요구

- 운영 환경에서 **배포/구성 실수에 강함** (예: allowlist 누락/오설정으로 공개되지 않도록)
- 표준 프로토콜/구현 사용(예: JWT/OIDC, mTLS, API Gateway)

---

## 선택안(Options)

### 옵션 A: API Gateway에서 인증/인가 처리 + X-Caller-Id 주입(현 구조 강화)

- API Gateway/WAF/Service Mesh에서 다음을 수행
  - JWT 검증 또는 mTLS
  - claim 기반으로 `caller_id`를 결정
  - ops 전용 claim/role(`role=ops`)을 가진 경우에만 ops endpoint 라우팅/허용
  - 백엔드로 `X-Caller-Id` 및 필요 시 `X-Caller-Roles` 등 주입

**장점**
- 서비스 코드 변경 최소
- 중앙집중형 정책/회수/로테이션

**단점**
- gateway 구성에 강하게 의존

### 옵션 B: 서비스 자체에서 API Key 기반 Admin/Ops 인증

- `X-Ops-Api-Key` 같은 별도 헤더로 ops 전용 키를 검증
- 키는 secret manager로 관리

**장점**
- gateway 없어도 동작

**단점**
- 키 배포/회수/로테이션 책임이 서비스로 들어옴
- 키 유출 위험

### 옵션 C: mTLS + SPIFFE/SPIRE (Service-to-Service)

- 내부망에서 ops 툴/서비스만 mTLS로 접근

---

## 결정(Chosen Approach)

**권장: 옵션 A**

- 이 프로젝트는 이미 `X-Caller-Id`를 “신뢰 가능한 상위 컴포넌트”가 주입한다는 전제를 두고 있으므로,
  authN/authZ도 동일 계층(API Gateway/Service Mesh)에서 수행하는 것이 일관적입니다.

구현 가이드:
- ops 엔드포인트는 네트워크 레벨에서 차단(내부망/VPN) + gateway authN/authZ 필수
- `QUEUE_STATS_ALLOWED_CALLERS`는 **2차 방어선**으로 유지

---

## API 계약 업데이트

### Queue stats (`GET /api/v1/emails/`)

- 목적: 운영/모니터링용 큐 상태 조회
- 요구:
  - `X-Caller-Id`는 신뢰 가능한 상위 컴포넌트가 주입해야 함
  - `QUEUE_STATS_ALLOWED_CALLERS`에 포함된 caller만 접근 가능

---

## 후속 작업(Next Steps)

- 실제 배포 환경에 맞춘 gateway 정책 문서(예: JWT claim → caller_id 매핑) 추가
- Ops 접근 로그(누가/언제/무엇을) 표준화