#!/bin/bash
# EC2 User Data Script - Auto-installs Docker and Docker Compose
# Use this when launching your EC2 instance

set -e

# Update system
yum update -y

# Install Docker
amazon-linux-extras install docker -y
systemctl start docker
systemctl enable docker

# Add ec2-user to docker group
usermod -aG docker ec2-user

# Install Docker Compose
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Create app directory
mkdir -p /home/ec2-user/slack-langflow-bridge/data
chown -R ec2-user:ec2-user /home/ec2-user/slack-langflow-bridge

echo "Docker and Docker Compose installed successfully!"
