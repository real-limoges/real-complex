"""Artifact Registry — a private Docker image repository.

This is where CI/CD pushes Docker images for all services (fugue, garcon, etc.).
Cloud Run pulls images from here when deploying.

Pulumi concept: each resource constructor takes a logical name (first arg)
and keyword arguments for the resource's properties.  Pulumi tracks resources
by their logical name — renaming it would cause Pulumi to delete + recreate.
"""

import pulumi
import pulumi_gcp as gcp

from infra.project import api_services

config = pulumi.Config()
gcp_config = pulumi.Config("gcp")
region = gcp_config.get("region") or "us-central1"

registry = gcp.artifactregistry.Repository(
    "realcomplex-images",
    repository_id="realcomplex-images",
    location=region,
    format="DOCKER",
    description="Docker images for realcomplex.systems services",
    opts=pulumi.ResourceOptions(
        depends_on=[api_services["artifactregistry.googleapis.com"]],
    ),
)

# Export the full registry path so service repos know where to push images.
# Format: us-central1-docker.pkg.dev/PROJECT/realcomplex-images
registry_url = pulumi.Output.all(region, registry.project, registry.repository_id).apply(
    lambda args: f"{args[0]}-docker.pkg.dev/{args[1]}/{args[2]}"
)

pulumi.export("registry_url", registry_url)
