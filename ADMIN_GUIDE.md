# Admin Guide: Slack-Langflow Bridge Bot

This guide explains how to configure and manage the Slack-Langflow Bridge Bot.

---

## Quick Start

### 1. Add your first flow

In any Slack channel where the bot is present:

```
@bot flows add <name> <langflow_url> <flow_id> <api_key> [description]
```

**Example:**
```
@bot flows add sales-agent https://dev-langbuilder.cloudgeometry.com abc-123-def-456 sk-myapikey123 Handles sales inquiries
```

### 2. Set it as default

```
@bot flows default sales-agent
```

Now the bot will respond to messages using this flow.

---

## Understanding the Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    SLACK WORKSPACE                       │
│                                                          │
│  #sales ────────→ "sales-agent" flow                    │
│  #support ──────→ "support-agent" flow                  │
│  #general ──────→ default flow                          │
│  DMs ───────────→ default flow                          │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

- **Flow**: A Langflow configuration (URL + flow_id + API key)
- **Default flow**: Used by channels without a specific assignment
- **Channel assignment**: Override the default for specific channels

---

## Commands Reference

### Flow Management

| Command | Description |
|---------|-------------|
| `@bot help` | Show all commands |
| `@bot flows` | List all configured flows |
| `@bot flows add <name> <url> <flow_id> <api_key> [description]` | Add a new flow |
| `@bot flows remove <name>` | Delete a flow |
| `@bot flows default <name>` | Set the default flow |
| `@bot flows info <name>` | Show flow details (masked API key) |

### Channel Configuration

| Command | Description |
|---------|-------------|
| `@bot channel info` | Show which flow this channel uses |
| `@bot channel set <flow_name>` | Assign a flow to this channel |
| `@bot channel reset` | Remove assignment (use default) |

---

## Step-by-Step Examples

### Example 1: Single Flow Setup

For a simple setup with one flow for all channels:

```
@bot flows add myflow https://dev-langbuilder.cloudgeometry.com abc-123 sk-xxx
@bot flows default myflow
```

Done! All channels now use `myflow`.

---

### Example 2: Multiple Flows for Different Teams

**Step 1: Add flows**
```
@bot flows add sales-bot https://dev-langbuilder.cloudgeometry.com flow-111 sk-aaa Sales inquiry handler
@bot flows add support-bot https://dev-langbuilder.cloudgeometry.com flow-222 sk-bbb Customer support agent
@bot flows add general-bot https://dev-langbuilder.cloudgeometry.com flow-333 sk-ccc General assistant
```

**Step 2: Set default**
```
@bot flows default general-bot
```

**Step 3: Assign channels** (run these in the respective channels)

In `#sales`:
```
@bot channel set sales-bot
```

In `#support`:
```
@bot channel set support-bot
```

**Result:**
- `#sales` → uses `sales-bot`
- `#support` → uses `support-bot`
- All other channels → use `general-bot` (default)

---

### Example 3: Update a Flow

To change a flow's configuration, remove and re-add it:

```
@bot flows remove old-flow
@bot flows add old-flow https://new-url.com new-flow-id new-api-key
```

Note: Existing conversations will continue working (sessions are preserved).

---

### Example 4: Check Current Configuration

**List all flows:**
```
@bot flows
```

Output:
```
Configured Flows:
- sales-bot (default) - Sales inquiry handler
- support-bot - Customer support agent
```

**Check a specific channel:**
```
@bot channel info
```

Output:
```
This channel is configured to use flow: sales-bot
```
or
```
This channel uses the default flow: general-bot
```

---

## Where to Find Flow Information

### Langflow URL
The base URL of your Langflow server.
- Example: `https://dev-langbuilder.cloudgeometry.com`

### Flow ID
1. Open your flow in Langflow
2. Click the "API" button (or check the URL)
3. Copy the flow ID (UUID format)
- Example: `59e5abe8-254f-4012-9b90-cb2a3430db70`

### API Key
1. In Langflow, go to Settings → API Keys
2. Create or copy an existing key
- Example: `sk-kiSaZHll3siWJhSLWwd68rEO8tqmcWAMZCwidQZTjLk`

---

## Troubleshooting

### "No flow configured for this channel"

Add a flow and set it as default:
```
@bot flows add myflow https://... flow-id api-key
@bot flows default myflow
```

### "There was an error communicating with the agent"

Check the flow configuration:
```
@bot flows info myflow
```

Verify:
- URL is correct (should start with `https://`)
- Flow ID exists in Langflow
- API key is valid

### Bot not responding

1. Ensure bot is invited to the channel: `/invite @BotName`
2. Check if a flow is configured: `@bot channel info`
3. Contact your system administrator to check logs

### Permission denied

If you see "You don't have permission", contact your workspace admin to add your Slack user ID to the `ADMIN_USER_IDS` environment variable.

---

## Tips

1. **Use descriptive names**: `sales-inquiries` is better than `flow1`

2. **Add descriptions**: They appear in the flows list
   ```
   @bot flows add hr-bot https://... id key Handles HR and employee questions
   ```

3. **Test in a private channel first**: Before deploying to public channels

4. **One flow can serve multiple channels**: You don't need separate flows for similar use cases

---

## Session Behavior

- Each Slack thread = one conversation session
- Sessions persist across bot restarts
- If you change a channel's flow, existing threads continue with their original flow
- New threads use the newly assigned flow

---

## Getting Help

In Slack:
```
@bot help
```

For technical issues, contact your system administrator.
