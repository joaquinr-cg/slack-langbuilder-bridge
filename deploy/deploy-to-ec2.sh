#!/bin/bash
# Deploy script - Run from local machine to deploy to EC2
# Usage: ./deploy-to-ec2.sh <ec2-host> [key-file]
# Example: ./deploy-to-ec2.sh ec2-user@54.123.45.67 ~/.ssh/my-key.pem

set -e

EC2_HOST="${1:?Usage: ./deploy-to-ec2.sh <ec2-host> [key-file]}"
KEY_FILE="${2:--}"  # Optional, uses default SSH key if not provided

SSH_OPTS=""
if [ "$KEY_FILE" != "-" ]; then
    SSH_OPTS="-i $KEY_FILE"
fi

APP_DIR="/home/ec2-user/slack-langflow-bridge"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Deploying Slack-Langflow Bridge to EC2 ==="
echo "Host: $EC2_HOST"
echo "Local dir: $LOCAL_DIR"
echo ""

# Create remote directory
echo "Creating remote directory..."
ssh $SSH_OPTS "$EC2_HOST" "mkdir -p $APP_DIR/data $APP_DIR/src"

# Copy application files
echo "Copying application files..."
scp $SSH_OPTS "$LOCAL_DIR/docker-compose.yml" "$EC2_HOST:$APP_DIR/"
scp $SSH_OPTS "$LOCAL_DIR/Dockerfile" "$EC2_HOST:$APP_DIR/"
scp $SSH_OPTS "$LOCAL_DIR/requirements.txt" "$EC2_HOST:$APP_DIR/"
scp $SSH_OPTS -r "$LOCAL_DIR/src/"* "$EC2_HOST:$APP_DIR/src/"

# Check if .env exists on remote, if not copy example
echo "Checking .env configuration..."
ssh $SSH_OPTS "$EC2_HOST" "test -f $APP_DIR/.env || echo 'WARNING: .env file not found on server!'"

# Copy database if it exists locally and not on remote
if [ -f "$LOCAL_DIR/data/sessions.db" ]; then
    echo "Checking database..."
    ssh $SSH_OPTS "$EC2_HOST" "test -f $APP_DIR/data/sessions.db" 2>/dev/null || {
        echo "Copying local database to server..."
        scp $SSH_OPTS "$LOCAL_DIR/data/sessions.db" "$EC2_HOST:$APP_DIR/data/"
    }
fi

# Build and restart
echo "Building and starting containers..."
ssh $SSH_OPTS "$EC2_HOST" "cd $APP_DIR && docker-compose down 2>/dev/null || true"
ssh $SSH_OPTS "$EC2_HOST" "cd $APP_DIR && docker-compose build --no-cache"
ssh $SSH_OPTS "$EC2_HOST" "cd $APP_DIR && docker-compose up -d"

# Show status
echo ""
echo "=== Deployment Complete ==="
ssh $SSH_OPTS "$EC2_HOST" "cd $APP_DIR && docker-compose ps"
echo ""
echo "View logs with: ssh $SSH_OPTS $EC2_HOST 'cd $APP_DIR && docker-compose logs -f'"
