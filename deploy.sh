#!/bin/bash
# 배포 잠금 — 다른 프로젝트와 동시 빌드 방지
LOCKFILE="/tmp/nas-deploy.lock"
TIMEOUT=300
WAITED=0
while [ -f "$LOCKFILE" ] && [ $WAITED -lt $TIMEOUT ]; do
    echo "Another deploy in progress, waiting..."
    sleep 10
    WAITED=$((WAITED + 10))
done
echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

cd /volume1/docker/ytanalysis
git fetch origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" != "$REMOTE" ]; then
    git pull origin main
    # DB/Redis는 건드리지 않고 api+worker만 재빌드
    docker compose up -d --build --no-deps api worker
    # 미사용 이미지 정리
    docker image prune -f --filter "until=1h" || true
    echo "$(date) - Deployed new version" >> /volume1/docker/ytanalysis/deploy.log
fi
