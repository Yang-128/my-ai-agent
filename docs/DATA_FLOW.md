# Data Flow

## Chat request

1. `static/app.js` collects the message, provider, model, selected tools and optional image attachments.
2. The frontend validates image/model compatibility and converts images to data URLs.
3. It may call `POST /api/ai-decide-extensions` for tool recommendations. The user can confirm recommendations or choose tools manually.
4. The frontend sends `POST /api/chat` with the conversation ID, message, provider, model, tools and images.
5. `chat.py` validates the request, conversation, images and vision capability.
6. Conversation history is loaded from `storage.py` and converted into provider messages.
7. Requested tools are filtered against enabled tools and available definitions.
8. Provider output is streamed and tool calls are processed when returned.
9. Tool execution passes through `tools.py`; MCP calls go through `mcp_client.py` to `mcp_server.py`.
10. The backend streams text to the frontend, records usage and persists the assistant response.

## Tool execution

    Frontend selection
          |
          v
    POST /api/chat
          |
          v
    chat.py orchestration
          |
          v
       tools.py
          |
          +--- Built-in tools
          |
          +--- mcp_client.py
                    |
                    | stdio
                    v
                mcp_server.py
                    +--- Google APIs
                    +--- Filesystem/system
                    +--- Conversation storage
                    +--- Web search/fetch

Tool definitions and execution are separate responsibilities. `tools.py` combines definitions and dispatches calls; the MCP server implements MCP-side capabilities.

## Calendar approval

1. A workflow proposes a Calendar action.
2. `calendar_approval.py` stores the pending action.
3. The frontend presents the proposal.
4. The user decision is submitted to `POST /api/google/calendar/approve`.
5. A declined action is discarded.
6. An approved action is applied through `google_integrations.py`.
7. For all-day/timed event type conversion, the implementation creates a replacement before deleting the original. If deletion fails, it attempts to remove the replacement.

## Gmail approval

1. A workflow creates a pending message through `gmail_approval.py`.
2. The frontend displays the proposal and available attachment information.
3. The user decision is submitted to `POST /api/google/gmail/approve`.
4. A declined message is discarded.
5. An approved message is consumed, validated and sent through `google_integrations.py`.
6. Edited recipients, body and attachments are validated before sending.

## Google OAuth

The frontend initiates connection through the Google connect endpoint. `google_integrations.py` creates the OAuth flow, handles the callback, obtains credentials and provides service clients.

Configured scopes include Calendar events, read-only Tasks and Gmail send. Availability depends on valid OAuth configuration and credentials.

## Persistence

- Conversations, messages and usage: `storage.py`.
- Pending Calendar actions: `calendar_approval.py`.
- Pending Gmail messages: `gmail_approval.py`.
- Runtime paths and environment file location: `runtime.py`.

Never inspect or publish local database, OAuth token, environment file or pending-action contents for documentation purposes.
