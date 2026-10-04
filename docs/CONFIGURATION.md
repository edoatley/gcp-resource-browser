# Configuration

Configuration is optional. Without a config file the tool uses the built-in type names
(`app/asset_types.json`) and noise rules (`app/noise_rules.json`). A site config **adds to**
those defaults instead of replacing them, so a later upgrade can still add new defaults.

## Where it is read from

The tool checks these locations in order and uses only the first file it finds:

1. `$GCP_EXPLORER_CONFIG`, an explicit path. If the variable is set and the file does not
   exist, the tool fails instead of silently continuing without it.
2. `./gcp-explorer.json` in the working directory
3. `~/.config/gcp-explorer/config.json`

The file is read once at startup, so restart `serve` to pick up changes. In a container, mount
the file and point `GCP_EXPLORER_CONFIG` at it.

## Format

```json
{
  "_comment": "Optional; ignored.",
  "asset_types": {
    "zone": "dns.googleapis.com/ManagedZone"
  },
  "noise_rules": [
    { "asset_type": "acme.example.com/Widget", "reason": "internal churn" },
    { "asset_type": "compute.googleapis.com/Firewall", "name": "^default-allow-", "reason": "default firewall rules" }
  ],
  "unsuppress": ["compute.googleapis.com/Route"],
  "role_risk_rules": [
    { "permission": "compute.instances.setMetadata", "risk": "high", "reason": "Can add SSH keys to VMs" },
    { "permission": "storage.buckets.setIamPolicy", "risk": "high", "reason": "Public buckets are an incident here" }
  ]
}
```

| Key | Effect |
|:---|:---|
| `asset_types` | Friendly name → CAI asset type. Adds to the built-in names; reusing a built-in name replaces it. |
| `noise_rules` | Extra suppression rules. Each needs `asset_type` (exact match) and `reason`, which is shown to users, so keep it short. An optional `name` is a regex matched against the resource's short name, so a rule can hide only some resources of a type. An optional `note` is documentation only. |
| `unsuppress` | CAI asset types whose **built-in** rules are switched off. Rules you add yourself are not affected. |
| `role_risk_rules` | Extra or overriding [role risk](ROLE_RISK.md) rules: `permission`, `risk` (`high` or `medium`), `reason`, and optional `sources`. A site rule for a permission **replaces** the built-in rule, so you can raise or lower a level. |

Unknown top-level keys are rejected, not ignored, so a misspelled key cannot leave you thinking
a setting was applied when it wasn't. Malformed JSON, or a rule without `asset_type` or
`reason`, also stops the tool at startup.

`--show-all` / `?show_all=true` still disables every rule, including site rules, and anything
hidden by a site rule is reported with its `reason` just like a built-in one.
