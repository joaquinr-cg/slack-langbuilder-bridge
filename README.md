# Slack-Langflow Bridge Bot

A Slack bot that bridges conversations to Langflow agentic flows. This bot acts as a proxy - all AI processing happens inside Langflow, not in the bot itself.

## Features

- **Thread-based sessions**: Maintains conversation context by mapping Slack threads to Langflow session IDs
- **Socket Mode**: No public URL required - works behind firewalls
- **Multiple trigger types**: Responds to @mentions, DMs, and thread replies
- **Long-running support**: Handles Langflow agents that take minutes to respond
- **Automatic session cleanup**: Removes stale sessions after configurable TTL

## Architecture

```
Slack Thread → Bot → Session Manager (SQLite) → Langflow API
                              ↓
                        session_id mapping
```

The bot maps each Slack thread to a unique Langflow session ID, enabling multi-turn conversations with context.

## Prerequisites

- Python 3.11+
- A Slack workspace with admin access
- A running Langflow server with a configured flow

## Slack App Setup

### 1. Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App** > **From scratch**
3. Name your app and select your workspace

### 2. Enable Socket Mode

1. Go to **Socket Mode** in the left sidebar
2. Toggle **Enable Socket Mode** to ON
3. Create an app-level token:
   - Click **Generate Token and Scopes**
   - Name it (e.g., "socket-mode")
   - Add the `connections:write` scope
   - Click **Generate**
   - Copy the token (starts with `xapp-`) - this is your `SLACK_APP_TOKEN`

### 3. Configure Bot Token Scopes

Go to **OAuth & Permissions** > **Scopes** > **Bot Token Scopes** and add:

- `app_mentions:read` - Receive mention events
- `chat:write` - Send messages
- `im:history` - Read DM history
- `im:read` - Access DM information
- `im:write` - Send DMs
- `channels:history` - Read channel messages (for threads)
- `groups:history` - Read private channel messages

### 4. Subscribe to Events

Go to **Event Subscriptions**:

1. Toggle **Enable Events** to ON
2. Under **Subscribe to bot events**, add:
   - `app_mention`
   - `message.im`
   - `message.channels`
   - `message.groups`

### 5. Install the App

1. Go to **OAuth & Permissions**
2. Click **Install to Workspace**
3. Authorize the app
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`) - this is your `SLACK_BOT_TOKEN`

### 6. Invite Bot to Channels

In Slack, invite your bot to channels where you want it to respond:
```
/invite @YourBotName
```

## Configuration

Copy the example environment file:

```bash
cp .env.example .env
```

Edit `.env` with your values:

```bash
# Slack Configuration
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token

# Langflow Configuration
LANGFLOW_API_URL=https://your-langflow-server.com
LANGFLOW_FLOW_ID=your-flow-id
LANGFLOW_API_KEY=sk-your-api-key

# Optional Configuration
DATABASE_PATH=./data/sessions.db
REQUEST_TIMEOUT=300
SESSION_TTL_HOURS=24
LOG_LEVEL=INFO
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SLACK_BOT_TOKEN` | Yes | - | Bot token from OAuth & Permissions |
| `SLACK_APP_TOKEN` | Yes | - | App-level token for Socket Mode |
| `LANGFLOW_API_URL` | Yes | - | Base URL of Langflow server |
| `LANGFLOW_FLOW_ID` | Yes | - | ID of the flow to execute |
| `LANGFLOW_API_KEY` | Yes | - | Langflow API key |
| `DATABASE_PATH` | No | `./data/sessions.db` | SQLite database path |
| `REQUEST_TIMEOUT` | No | `300` | Langflow timeout (seconds) |
| `SESSION_TTL_HOURS` | No | `24` | Session cleanup threshold |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity |

## Running Locally

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Run the Bot

```bash
python -m src.main
```

You should see:
```
2024-01-08 10:00:00 | INFO     | src.main | Starting Slack-Langflow Bridge Bot...
2024-01-08 10:00:00 | INFO     | src.session_manager | Session database initialized at ./data/sessions.db
2024-01-08 10:00:00 | INFO     | src.slack_handler | Starting Slack Socket Mode handler...
```

## Running with Docker

### Build and Run

```bash
docker-compose up -d
```

### View Logs

```bash
docker-compose logs -f
```

### Stop

```bash
docker-compose down
```

## Usage

### Mention in a Channel

```
@YourBot What's the weather like today?
```

The bot will respond in a thread. Continue the conversation by replying in that thread.

### Direct Message

Send a DM to the bot - no @ mention needed.

### Thread Continuation

Once the bot responds in a thread, it will automatically respond to follow-up messages in that thread without needing to be mentioned again.

## How It Works

1. **Message received**: Bot receives a Slack event (mention, DM, or thread reply)
2. **Session lookup**: Bot checks SQLite for existing session mapping
3. **Session creation**: If new thread, generates UUID for Langflow session
4. **Langflow call**: Sends message to Langflow with session ID
5. **Response parsing**: Extracts message from Langflow's nested JSON response
6. **Slack reply**: Sends response in the same thread

## Troubleshooting

### Bot not responding

1. Check bot is invited to the channel: `/invite @YourBot`
2. Verify Socket Mode is enabled in Slack app settings
3. Check logs for errors: `docker-compose logs -f`

### "The agent is taking longer than expected"

Increase `REQUEST_TIMEOUT` in your `.env` file. Some agents can take several minutes.

### Session not maintained

Ensure you're replying in the same thread. Each thread maintains a separate session.

### Permission errors

Verify all required OAuth scopes are added and the app is reinstalled after scope changes.

## Project Structure

```
slack-langflow-bridge/
├── src/
│   ├── __init__.py           # Package init
│   ├── main.py               # Entry point
│   ├── config.py             # Configuration
│   ├── slack_handler.py      # Slack event handling
│   ├── session_manager.py    # SQLite session storage
│   ├── langflow_client.py    # Langflow HTTP client
│   └── response_parser.py    # Response extraction
├── data/
│   └── sessions.db           # SQLite database (auto-created)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## License

MIT
