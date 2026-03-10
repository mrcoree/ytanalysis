# Viral Radar - YouTube 떡상 영상 발견 엔진

VPH(시간당 조회수) 기반으로 급성장 중인 YouTube 영상을 자동 탐지하는 시스템입니다.

## 주요 기능

- **영상 발견**: 키워드 기반 YouTube 영상 검색
- **조회수 추적**: 30분마다 자동 통계 수집
- **VPH 분석**: 시간당 조회수 증가량 계산
- **바이럴 스코어**: 종합 떡상 점수 산출
- **자막 가져오기**: 온디맨드 자막 조회 (DB 저장 안 함)

## 실행 방법

### 1. API 키 설정

```bash
cp .env.example .env
# .env 파일을 열어 YouTube API 키 입력
```

YouTube API 키는 [Google Cloud Console](https://console.cloud.google.com/)에서 발급받으세요.

### 2. 실행

```bash
docker compose up
```

### 3. API 접속

- Swagger UI: http://localhost:8000/docs
- API 기본 주소: http://localhost:8000

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/videos?keyword=재벌` | 키워드로 영상 검색 |
| GET | `/video/{video_id}` | 영상 상세 정보 |
| GET | `/video/{video_id}/stats` | 조회수 이력 |
| GET | `/video/{video_id}/transcript` | 자막 가져오기 |

## 크롤러 동작

Celery Worker가 자동으로 실행됩니다:

- **10분마다**: 트렌딩 키워드로 새 영상 발견
- **30분마다**: 추적 중인 모든 영상의 통계 수집 + VPH 계산

## 기술 스택

Python 3.11 / FastAPI / PostgreSQL / Redis / Celery / SQLAlchemy / Docker
