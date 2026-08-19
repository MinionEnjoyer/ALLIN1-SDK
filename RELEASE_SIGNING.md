# Release signing

Tagged Windows releases are packaged only after every executable payload has a
valid, timestamped Authenticode signature. The release workflow uses Microsoft
Azure Artifact Signing with GitHub OpenID Connect; it does not store a certificate
or private key in the repository.

## Required Azure setup

1. Create an Azure Artifact Signing account.
2. Complete public-trust identity validation.
3. Create a public-trust certificate profile.
4. Create an Entra application or managed identity with a federated credential for
   the GitHub environment subject
   `repo:MinionEnjoyer/ALLIN1-SDK:environment:release-signing`.
5. Assign that identity the **Artifact Signing Certificate Profile Signer** role on
   the certificate profile.

## Required GitHub configuration

Add these Actions secrets to `MinionEnjoyer/ALLIN1-SDK`:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

Add these Actions variables:

- `AZURE_ARTIFACT_SIGNING_ENDPOINT`
- `AZURE_ARTIFACT_SIGNING_ACCOUNT`
- `AZURE_ARTIFACT_SIGNING_PROFILE`

Store these values in the repository's `release-signing` environment. The CI job has
read-only repository access and no identity-token permission; only a successful tag
build can enter the signing environment and request the short-lived Azure token.

The workflow inventories unsigned `.exe`, `.dll`, and `.pyd` payloads after the
desktop, agent, and archive helper builds. Artifact Signing signs that exact catalog
with SHA-256 and the Microsoft RFC 3161 timestamp service. Packaging fails if any
executable payload remains unsigned or has an invalid signature.

Do not publish a Windows release by bypassing the signing and verification steps.
