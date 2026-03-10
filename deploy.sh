#!/bin/bash
cd /volume1/docker/ytanalysis
git fetch origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" != "$REMOTE" ]; then
    git pull origin main
    docker compose up -d --build
    echo "$(date) - Deployed new version" >> /volume1/docker/ytanalysis/deploy.log
fi
