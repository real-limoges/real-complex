"""Enable required GCP APIs for the project.

Pulumi concept: gcp.projects.Service enables a single API on a GCP project.
We enable all the APIs our infrastructure needs so that subsequent resources
(Cloud Run, Artifact Registry, etc.) can be created without manual console clicks.

Important: other modules should depend on these via `depends_on` to avoid
race conditions where a resource is created before its API is enabled.
"""

import pulumi_gcp as gcp

# List every API this project needs.  Add new ones here as the infra grows.
_REQUIRED_APIS = [
    "run.googleapis.com",               # Cloud Run
    "artifactregistry.googleapis.com",   # Artifact Registry (Docker images)
    "compute.googleapis.com",            # GCE (SurrealDB VM) + networking
    "secretmanager.googleapis.com",      # Secret Manager
    "iam.googleapis.com",                # IAM + Workload Identity Federation
    "iamcredentials.googleapis.com",     # SA token creation (WIF needs this)
    "cloudresourcemanager.googleapis.com",  # Project-level IAM bindings
    "cloudbuild.googleapis.com",            # Cloud Build (Docker image builds)
    "dns.googleapis.com",                   # Cloud DNS
]

# Create a Service resource for each API.  We store them in a dict so other
# modules can reference them in depends_on=[api_services["run.googleapis.com"]].
api_services: dict[str, gcp.projects.Service] = {}

for api in _REQUIRED_APIS:
    # The resource name uses the API prefix (e.g. "run" from "run.googleapis.com")
    # to keep Pulumi URNs short and readable.
    short_name = api.split(".")[0]
    api_services[api] = gcp.projects.Service(
        f"api-{short_name}",
        service=api,
        # disable_on_destroy=False means "don't disable the API if we remove
        # this resource" — safer, avoids accidentally breaking things.
        disable_on_destroy=False,
    )
