"""Secret Manager — create secret *containers* (not the values themselves).

Pulumi creates the Secret Manager secret resource (the "envelope"), but you
populate the actual secret *value* via gcloud CLI after `pulumi up`.  This
keeps sensitive data out of Pulumi state and code.

Pulumi concept: `pulumi.export()` makes a value visible in `pulumi stack output`.
This is how you surface useful info (URLs, resource names) after deployment.
"""

import pulumi
import pulumi_gcp as gcp

from infra.project import api_services

_SECRETS = [
    "fugue-secret-key-base",      # Phoenix SECRET_KEY_BASE
    "cozodb-auth-token",          # CozoDB x-cozo-auth header token
]

secrets: dict[str, gcp.secretmanager.Secret] = {}

for secret_name in _SECRETS:
    secrets[secret_name] = gcp.secretmanager.Secret(
        secret_name,
        secret_id=secret_name,
        replication=gcp.secretmanager.SecretReplicationArgs(
            auto=gcp.secretmanager.SecretReplicationAutoArgs(),
        ),
        opts=pulumi.ResourceOptions(
            depends_on=[api_services["secretmanager.googleapis.com"]],
        ),
    )

pulumi.export("secret_names", [s for s in _SECRETS])
