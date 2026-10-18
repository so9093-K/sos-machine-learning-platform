# 0010 — 컨테이너 실행 기반과 플랫폼 계약 분리

상태: **Superseded by [0011](0011-compose-native-validation.md)**

## 배경

SOS는 호스트에서 API·Worker·Vite, Compose에서 PostgreSQL·SeaweedFS를 실행한다. 실행 재현성과 이식성을 높이려면 동일 코드·잠금 의존성에서 컨테이너 이미지를 구축하되 기존 신원·데이터·Worker 복구 계약이 약화되어서는 안 된다.

## 결정

1. **이미지 계약:** API·Worker·초기화는 같은 Python 이미지의 실행 명령만 다르게 사용한다. Web은 독립 개발용 이미지로 제공하며 공개 배포 이미지라고 주장하지 않는다.
2. **실행 경계:** `compose.yaml` 한 파일에서 관리형 PostgreSQL·SeaweedFS 및 `app` 프로필의 API·Worker·Web을 정의한다. 공개 `make up`은 Compose를 사용하고, 프로필 없이 인프라만 실행하는 경로는 기존 CI·격리 테스트를 위해 유지한다. 호스트 실행 도구는 독립 브라우저 검증을 위한 내부 어댑터로만 사용한다.
3. **보안:** Docker 네트워크를 개발 인증의 루프백으로 간주하지 않는다. 컨테이너 경로는 명시적 Token 신원·호스트 루프백 포트만 허용한다.
4. **데이터와 복구:** 기존 PostgreSQL·S3 영속 볼륨과 Job/Attempt/Iceberg 판본 계약을 보존한다. API·Worker 캐시는 별도의 지속 볼륨에 둔다. 초기화는 관리형 DB·스토리지에만 수행한다.
5. **검증:** 고유 Compose 프로젝트에서 인증 거부·권한·Web 프록시·Dataset·Worker·Iceberg·재시작 및 전체 종료·재기동 후 판본 보존을 검사한다. 인프라-only CI는 프로필 없이 기동하며 별도 Host 브라우저 검증은 독립 테스트 경계에 한정한다. CI `python-quality`는 새 컨테이너 검증의 성공을 요구한다.

## 근거

표준 이미지 기반으로 개발·CI·배포 실행을 재현하면서 플랫폼 도메인을 Docker·Make·Kubernetes에 결합하지 않는다. 새 설정 프레임워크나 실행 추상화 대신 기존 Factory·Worker·Settings 계약을 재사용한다.

## 영향과 한계

현재 경로는 로컬 컨테이너 개발 및 검증용이고 공개 서비스 인증·TLS, 외부 관리형 스토리지, GPU·다중 노드 실행, 비신뢰 코드 격리는 범위 밖이다. 일반 개발 환경에서는 `SOS_AUTH_TOKEN_HASHES`를 명시한다. 이미지 성능과 개발 시 편집·재시작 비용은 후속 실측 과제다. cgroup v2 위임·물리 격리는 컨테이너 기동만으로 보장되지 않는다.
