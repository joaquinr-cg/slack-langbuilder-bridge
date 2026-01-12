# Slack-Langflow Bridge Bot

A Slack bot that bridges conversations to Langflow agentic flows. This bot acts as a proxy - all AI processing happens inside Langflow, not in the bot itself.

## Features

- **Multi-flow support**: Configure multiple Langflow flows and route channels to different agents
- **Thread-based sessions**: Maintains conversation context by mapping Slack threads to Langflow session IDs
- **Socket Mode**: No public URL required - works behind firewalls
- **Multiple trigger types**: Responds to @mentions, DMs, and thread replies
- **Runtime configuration**: Add, remove, and configure flows via Slack commands
- **Channel routing**: Each channel can use a different flow
- **Admin controls**: Restrict flow management to specific users
- **Long-running support**: Handles Langflow agents that take minutes to respond
- **Automatic session cleanup**: Removes stale sessions after configurable TTL

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     SINGLE SLACK APP                            │
│                                                                 │
│  Channel #sales ──────→ Flow: "sales-agent"                    │
│  Channel #support ────→ Flow: "support-agent"                  │
│  Channel #general ────→ Flow: "default" (default flow)         │
│  DMs ─────────────────→ Flow: "default" (default flow)         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      SQLite Database                            │
│  - flows: Multiple Langflow configurations                      │
│  - channel_flows: Channel → Flow mappings                       │
│  - sessions: Thread → Session ID mappings                       │
└─────────────────────────────────────────────────────────────────┘
```

## Prerequisites

- Python 3.11+
- A Slack workspace with admin access
- One or more Langflow servers with configured flows

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
# Required: Slack tokens
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token

# Optional: Default flow (can also add via Slack commands)
LANGFLOW_API_URL=https://your-langflow-server.com
LANGFLOW_FLOW_ID=your-flow-id
LANGFLOW_API_KEY=sk-your-api-key
DEFAULT_FLOW_NAME=default

# Optional: Restrict admin commands to specific users
ADMIN_USER_IDS=U1234567890,U0987654321
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SLACK_BOT_TOKEN` | Yes | - | Bot token from OAuth & Permissions |
| `SLACK_APP_TOKEN` | Yes | - | App-level token for Socket Mode |
| `LANGFLOW_API_URL` | No | - | Base URL for default flow |
| `LANGFLOW_FLOW_ID` | No | - | Flow ID for default flow |
| `LANGFLOW_API_KEY` | No | - | API key for default flow |
| `DEFAULT_FLOW_NAME` | No | `default` | Name for the default flow |
| `ADMIN_USER_IDS` | No | - | Comma-separated admin user IDs |
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

## Bot Commands

All commands are triggered by mentioning the bot: `@YourBot <command>`

### Flow Management

| Command | Description |
|---------|-------------|
| `@bot help` | Show all available commands |
| `@bot flows` | List all configured flows |
| `@bot flows add <name> <url> <flow_id> <api_key> [description]` | Add a new flow |
| `@bot flows remove <name>` | Remove a flow |
| `@bot flows default <name>` | Set the default flow |
| `@bot flows info <name>` | Show flow details |

### Channel Configuration

| Command | Description |
|---------|-------------|
| `@bot channel info` | Show this channel's flow configuration |
| `@bot channel set <flow_name>` | Set this channel to use a specific flow |
| `@bot channel reset` | Remove channel-specific flow (use default) |

### Examples

```
# Add a sales agent flow
@bot flows add sales https://langflow.example.com abc-123-def sk-xxx Sales inquiry handler

# Set the sales channel to use the sales flow
@bot channel set sales

# Add a support flow and make it the default
@bot flows add support https://langflow.example.com def-456-ghi sk-yyy Support agent
@bot flows default support

# Check what flow this channel uses
@bot channel info
```

## Usage

### Regular Conversations

Once flows are configured, any message that isn't a command will be sent to the appropriate Langflow agent:

```
@YourBot What's the weather like today?
```

The bot will respond in a thread. Continue the conversation by replying in that thread.

### Direct Messages

Send a DM to the bot - no @ mention needed. DMs use the default flow.

### Thread Continuation

Once the bot responds in a thread, it will automatically respond to follow-up messages in that thread without needing to be mentioned again.

## How It Works

1. **Message received**: Bot receives a Slack event (mention, DM, or thread reply)
2. **Flow lookup**: Bot determines which flow to use based on channel configuration
3. **Session lookup**: Bot checks SQLite for existing session mapping
4. **Session creation**: If new thread, generates UUID for Langflow session
5. **Langflow call**: Sends message to the appropriate Langflow flow with session ID
6. **Response parsing**: Extracts message from Langflow's nested JSON response
7. **Slack reply**: Sends response in the same thread

### Session Flow Persistence

When a conversation starts in a thread, the flow used is stored with the session. This means:
- If channel #sales uses "sales-agent" flow
- A conversation starts in #sales
- Later, #sales is reconfigured to use "support-agent"
- The existing thread continues using "sales-agent" (original flow)
- New threads in #sales will use "support-agent"

This ensures conversation continuity even when channel configurations change.

## Troubleshooting

### Bot not responding

1. Check bot is invited to the channel: `/invite @YourBot`
2. Verify Socket Mode is enabled in Slack app settings
3. Check if a flow is configured: `@bot channel info`
4. Check logs for errors: `docker-compose logs -f`

### "No flow configured for this channel"

Add a flow and set it as default:
```
@bot flows add myflow https://langflow.example.com flow-id api-key
@bot flows default myflow
```

### "The agent is taking longer than expected"

Increase `REQUEST_TIMEOUT` in your `.env` file. Some agents can take several minutes.

### Permission denied for commands

If you configured `ADMIN_USER_IDS`, only those users can manage flows. Get your user ID from Slack (click profile > ... > Copy member ID).

### Session not maintained

Ensure you're replying in the same thread. Each thread maintains a separate session.

## Project Structure

```
slack-langflow-bridge/
├── src/
│   ├── __init__.py           # Package init
│   ├── main.py               # Entry point
│   ├── config.py             # Configuration
│   ├── slack_handler.py      # Slack event handling & commands
│   ├── session_manager.py    # SQLite session storage
│   ├── flow_manager.py       # Multi-flow management
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
