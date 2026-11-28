# Extension package manifest v1

Each package has a UTF-8 JSON manifest named `extension.json` at its root. Importers normalize generated, local, GitHub, and published packages to the same layout. A package is either a directory or a ZIP whose root contains the manifest. Paths are relative to that root. Importers reject absolute paths, traversal, symlinks, duplicate entries, and missing entry points before copying any files.

| Field | Meaning |
|---|---|
| `manifest_version` | Integer `1`. Reject unsupported versions. |
| `id` | Stable lowercase reverse-DNS identifier, such as `org.example.units`. |
| `version` | Semantic version of the extension. |
| `name`, `description` | Human-readable catalog metadata. |
| `runtime` | Runtime kind and supported version range. |
| `dependencies` | Runtime-specific dependency manifest and lockfile paths. Empty when there are no dependencies. |
| `tools` | Unique tool names, entry points, descriptions, and JSON Schema input and output contracts. |
| `configuration` | Non-secret configuration schema and names of required secrets. No credential values. |
| `permissions` | Requested filesystem scopes, HTTPS hosts, and secret names. Empty arrays request no access. |

Example for a pure calculator extension:

```json
{
  "manifest_version": 1,
  "id": "org.example.units",
  "version": "1.0.0",
  "name": "Unit converter",
  "description": "Converts kilometres to metres.",
  "runtime": {"kind": "python", "version": ">=3.12,<4"},
  "dependencies": [],
  "tools": [{
    "name": "kilometres_to_metres",
    "entry_point": {"path": "convert.py", "function": "kilometres_to_metres"},
    "description": "Convert kilometres to metres.",
    "input": {"type": "object", "properties": {"km": {"type": "number"}}, "required": ["km"], "additionalProperties": false},
    "output": {"type": "number"}
  }],
  "configuration": {"schema": {"type": "object", "additionalProperties": false}, "secrets": []},
  "permissions": {"filesystem": [], "network": [], "secrets": []}
}
```

The installer records source and integrity separately from the manifest. An update keeps the same ID and goes through review again if it requests new permissions. Requested permissions require explicit user grants. Installing a package does not import or execute its entry point. The runtime validates inputs and outputs and only exposes validated tools after approval. Registry display names can change without breaking stored references, which use extension ID and tool name.
