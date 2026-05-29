#!/bin/bash
# Script to fix Prometheus "out of bounds" errors caused by clock drift.
# This script will stop Prometheus, delete its data volume, and restart it.
# WARNING: This will delete all historical metrics.

set -e

# Get the project name from the current directory name if not set
PROJECT_NAME=${COMPOSE_PROJECT_NAME:-$(basename "$(pwd)")}
VOLUME_NAME="${PROJECT_NAME}_prometheus-data"

echo "Detected project: $PROJECT_NAME"
echo "Detected volume: $VOLUME_NAME"

echo "Stopping Prometheus..."
docker compose stop prometheus
docker compose rm -f prometheus

echo "Removing Prometheus data volume..."
if docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
    docker volume rm "$VOLUME_NAME"
    echo "Volume $VOLUME_NAME removed."
else
    echo "Volume $VOLUME_NAME not found, skipping removal."
fi

echo "Starting Prometheus..."
docker compose up -d prometheus

echo "Prometheus has been reset and should now accept new samples."
