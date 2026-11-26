# Provider Configuration Management

## Provider management

Open Provider Manager from the chat provider dropdown.

- My providers: manage existing connections, credentials,
  models, availability, and configuration files.
- Discover providers: rank and compare providers, then add
  supported connections.

Both tabs share the same provider configuration backend.

Supported settings:
- Automatic: use normal provider availability behavior.
- Enabled: include the provider in the picker.
- Disabled: prevent model discovery and new provider execution.
- Removed: hide and disable the provider until restored.
- Display name: optional local label.

Removing a provider does not delete its stored credentials.

## Import and export

Provider configurations can be exported and imported as JSON.

Only supported provider identifiers, availability modes,
and display labels are accepted.

API endpoints and credentials cannot be imported through
the configuration schema. Stored API keys are not exported.
Do not enter sensitive information in display labels.

Imports replace the existing configuration after validation.
Invalid imports preserve existing settings.

The configuration file is saved atomically with owner-only
permissions and is excluded from Git.

## Persistent credentials

On macOS, users may explicitly remember an API key in Keychain.

Credential priority:
1. Temporary session credential.
2. Saved macOS Keychain credential.
3. Existing environment credential.

Session credentials remain the default unless persistence
is explicitly selected.

Removing the active credential exposes the next available
credential source. Environment credentials remain unchanged.

## Security and compatibility

- Configuration mutations require local, same-origin access.
- Disabled providers are blocked at the backend client layer.
- Credential storage is separate from provider configuration.
- Native Keychain storage has been verified across processes.
- No existing API credentials were modified during testing.
- Arbitrary provider endpoints are not accepted.
- Keychain persistence is currently macOS-only.

## Recovery

If provider configuration becomes unreadable, stop the server,
back up provider_configuration.json, remove it from the runtime
data directory, and restart the server to restore default settings.

This does not remove saved credentials.
