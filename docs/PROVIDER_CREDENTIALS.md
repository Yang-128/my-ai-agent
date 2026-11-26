# Provider Credential Management

## Setup

1. Select a provider.
2. Open its model selector and API-key guide.
3. Obtain an API key from the provider.
4. Enter the key and select Validate and connect.

## Authentication

Groq, Gemini, OpenRouter and Vercel use fixed provider-controlled
validation endpoints without sending inference requests.

Kilo, OpenCode, ZenMux and TokenRouter require explicit consent
before saving an unverified key.

Authentication does not establish free-tier eligibility,
remaining quota or compatibility with a particular model.

## Security

- Credential management is restricted to local, same-origin requests.
- Validation does not follow HTTP redirects.
- Credentials are not included in status responses.
- Failed validation does not replace existing credentials.
- New credentials are stored in server memory.
- Restarting the server clears session credentials.
- Removing a session credential restores any existing .env key.
- Existing environment credentials are not modified.
- Provider-side revocation must be performed with the provider.

## Limitations

Persistent OS-backed credential storage is not implemented.
Live authentication and inference compatibility depend on each
provider and account. Free-model discovery does not guarantee
that inference will be free.

## Validation results — November 26, 2026

- All 67 regression tests passed.
- All 17 credential-security tests passed.
- Groq, Gemini and OpenRouter accepted existing valid keys
  and rejected deliberately invalid keys.
- Vercel live validation was skipped because no key was configured.
- No inference requests were sent.
- Existing credentials remained unchanged.
