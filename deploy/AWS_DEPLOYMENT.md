# AWS EC2 Deployment Guide

This guide walks you through deploying the Slack-Langflow Bridge Bot to AWS EC2.

## Prerequisites

- AWS Account with EC2 access
- AWS CLI configured locally (optional, for convenience)
- SSH key pair for EC2 access

## Step 1: Create EC2 Instance

### Via AWS Console:

1. Go to **EC2 Dashboard** → **Launch Instance**

2. **Name**: `slack-langflow-bridge`

3. **AMI**: Amazon Linux 2023 AMI (or Amazon Linux 2)

4. **Instance Type**: `t3.micro` (free tier eligible) or `t3.small` for better performance

5. **Key Pair**: Select or create a key pair (save the .pem file!)

6. **Network Settings**:
   - Allow SSH (port 22) from your IP
   - No other inbound rules needed (bot uses outbound Socket Mode)

7. **Storage**: 8 GB gp3 (default is fine)

8. **Advanced Details** → **User Data**: Paste contents of `ec2-user-data.sh`:
   ```bash
   #!/bin/bash
   yum update -y
   amazon-linux-extras install docker -y
   systemctl start docker
   systemctl enable docker
   usermod -aG docker ec2-user
   curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
   chmod +x /usr/local/bin/docker-compose
   mkdir -p /home/ec2-user/slack-langflow-bridge/data
   chown -R ec2-user:ec2-user /home/ec2-user/slack-langflow-bridge
   ```

9. Click **Launch Instance**

### Via AWS CLI:

```bash
# Create security group
aws ec2 create-security-group \
    --group-name slack-bot-sg \
    --description "Security group for Slack bot"

# Allow SSH
aws ec2 authorize-security-group-ingress \
    --group-name slack-bot-sg \
    --protocol tcp \
    --port 22 \
    --cidr YOUR_IP/32

# Launch instance
aws ec2 run-instances \
    --image-id ami-0c55b159cbfafe1f0 \
    --instance-type t3.micro \
    --key-name your-key-name \
    --security-groups slack-bot-sg \
    --user-data file://deploy/ec2-user-data.sh \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=slack-langflow-bridge}]'
```

## Step 2: Wait for Instance Ready

Wait 2-3 minutes for the instance to start and run the user-data script.

```bash
# Get instance public IP
aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=slack-langflow-bridge" \
    --query "Reservations[].Instances[].PublicIpAddress" \
    --output text
```

## Step 3: Connect and Configure

```bash
# SSH into instance
ssh -i your-key.pem ec2-user@YOUR_INSTANCE_IP

# Verify Docker is running
docker --version
docker-compose --version
```

## Step 4: Create Environment File

On the EC2 instance, create the `.env` file:

```bash
cd /home/ec2-user/slack-langflow-bridge
nano .env
```

Add your configuration:

```bash
# Required: Slack tokens
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token

# Optional: Admin restriction
# ADMIN_USER_IDS=U1234567890,U0987654321

# Application settings
DATABASE_PATH=./data/sessions.db
REQUEST_TIMEOUT=300
SESSION_TTL_HOURS=24
LOG_LEVEL=INFO
```

Save and exit (Ctrl+X, Y, Enter).

## Step 5: Deploy the Application

### Option A: Use the deploy script (from local machine)

```bash
# From your local project directory
chmod +x deploy/deploy-to-ec2.sh
./deploy/deploy-to-ec2.sh ec2-user@YOUR_INSTANCE_IP ~/.ssh/your-key.pem
```

### Option B: Manual deployment

```bash
# From local machine - copy files
scp -i your-key.pem -r src/ docker-compose.yml Dockerfile requirements.txt ec2-user@YOUR_INSTANCE_IP:/home/ec2-user/slack-langflow-bridge/

# Optional: copy existing database
scp -i your-key.pem data/sessions.db ec2-user@YOUR_INSTANCE_IP:/home/ec2-user/slack-langflow-bridge/data/

# SSH into instance
ssh -i your-key.pem ec2-user@YOUR_INSTANCE_IP

# Build and run
cd /home/ec2-user/slack-langflow-bridge
docker-compose build
docker-compose up -d
```

## Step 6: Verify Deployment

```bash
# Check container is running
docker-compose ps

# View logs
docker-compose logs -f

# Should see:
# ⚡️ Bolt app is running!
```

## Step 7: Configure Flows (if starting fresh)

In Slack, mention the bot to add flows:

```
@bot flows add myflow https://langflow-server.com flow-id api-key Description
@bot flows default myflow
```

---

## Maintenance

### View logs
```bash
ssh -i your-key.pem ec2-user@YOUR_IP "cd /home/ec2-user/slack-langflow-bridge && docker-compose logs -f"
```

### Restart bot
```bash
ssh -i your-key.pem ec2-user@YOUR_IP "cd /home/ec2-user/slack-langflow-bridge && docker-compose restart"
```

### Update code
```bash
./deploy/deploy-to-ec2.sh ec2-user@YOUR_IP ~/.ssh/your-key.pem
```

### Backup database
```bash
scp -i your-key.pem ec2-user@YOUR_IP:/home/ec2-user/slack-langflow-bridge/data/sessions.db ./backup-sessions.db
```

---

## Cost Estimate

| Resource | Monthly Cost |
|----------|--------------|
| t3.micro (on-demand) | ~$8.50 |
| t3.micro (1yr reserved) | ~$5.00 |
| EBS 8GB gp3 | ~$0.70 |
| **Total** | **~$6-9/month** |

---

## Troubleshooting

### Docker not found after launch
Wait a few more minutes for user-data script to complete, or run manually:
```bash
sudo yum install docker -y
sudo systemctl start docker
sudo usermod -aG docker ec2-user
# Log out and back in
```

### Permission denied on docker
```bash
sudo usermod -aG docker ec2-user
# Log out and back in
exit
ssh -i your-key.pem ec2-user@YOUR_IP
```

### Bot not connecting
- Check `.env` file has correct tokens
- Verify security group allows outbound HTTPS (443)
- Check logs: `docker-compose logs`

### Database permissions
```bash
sudo chown -R ec2-user:ec2-user /home/ec2-user/slack-langflow-bridge/data
```
