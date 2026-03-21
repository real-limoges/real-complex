"""IAM — Service accounts + Workload Identity Federation for GitHub Actions.

This is the most complex module.  Three things happen here:

1. **Service accounts** — Cloud Run services run as specific SAs with minimal
   permissions (principle of least privilege).

2. **Workload Identity Pool + Provider** — This lets GitHub Actions authenticate
   to GCP *without* a service account key.  GitHub's OIDC token is exchanged for
   a short-lived GCP token.  Much more secure than storing a JSON key as a secret.

3. **IAM bindings** — Wire up "who can do what":
   - fugue-runner SA can read secrets
   - deployer SA can push images + deploy Cloud Run
   - GitHub Actions can impersonate the deployer SA via WIF

Pulumi concept: `pulumi.Output.all(...).apply(fn)` lets you combine multiple
outputs (which are resolved asynchronously) into a computed value.
"""

import pulumi
import pulumi_gcp as gcp

from infra.project import api_services

config = pulumi.Config()
gcp_config = pulumi.Config("gcp")
project = gcp_config.require("project")

# ---------- Service Accounts ----------

# SA that Cloud Run services (fugue) run as.
fugue_runner_sa = gcp.serviceaccount.Account(
    "fugue-runner",
    account_id="fugue-runner",
    display_name="Fugue Cloud Run runtime SA",
    opts=pulumi.ResourceOptions(
        depends_on=[api_services["iam.googleapis.com"]],
    ),
)

# SA that CI/CD uses to push images and deploy Cloud Run services.
deployer_sa = gcp.serviceaccount.Account(
    "deployer",
    account_id="deployer",
    display_name="CI/CD deployer SA",
    opts=pulumi.ResourceOptions(
        depends_on=[api_services["iam.googleapis.com"]],
    ),
)

# ---------- IAM Bindings ----------

# fugue-runner can access secrets
fugue_secret_accessor = gcp.projects.IAMMember(
    "fugue-runner-secret-accessor",
    project=project,
    role="roles/secretmanager.secretAccessor",
    member=fugue_runner_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# deployer can manage Cloud Run
deployer_run_admin = gcp.projects.IAMMember(
    "deployer-run-admin",
    project=project,
    role="roles/run.admin",
    member=deployer_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# deployer can push to Artifact Registry
deployer_registry_writer = gcp.projects.IAMMember(
    "deployer-registry-writer",
    project=project,
    role="roles/artifactregistry.writer",
    member=deployer_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# deployer can act as other service accounts (needed to deploy Cloud Run
# services that run as fugue-runner)
deployer_sa_user = gcp.projects.IAMMember(
    "deployer-sa-user",
    project=project,
    role="roles/iam.serviceAccountUser",
    member=deployer_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# ---------- Workload Identity Federation ----------

# The pool is a container for identity providers (GitHub, GitLab, etc.)
wif_pool = gcp.iam.WorkloadIdentityPool(
    "github-actions-pool",
    workload_identity_pool_id="github-actions-pool",
    display_name="GitHub Actions",
    description="Identity pool for GitHub Actions OIDC",
    opts=pulumi.ResourceOptions(
        depends_on=[api_services["iam.googleapis.com"]],
    ),
)

# The provider maps GitHub's OIDC token claims to GCP attributes.
wif_provider = gcp.iam.WorkloadIdentityPoolProvider(
    "github-actions-provider",
    workload_identity_pool_id=wif_pool.workload_identity_pool_id,
    workload_identity_pool_provider_id="github-actions-provider",
    display_name="GitHub Actions OIDC",
    description="Maps GitHub OIDC tokens to GCP identities",
    # GitHub's OIDC issuer URL
    oidc=gcp.iam.WorkloadIdentityPoolProviderOidcArgs(
        issuer_uri="https://token.actions.githubusercontent.com",
    ),
    # Map GitHub token claims to GCP attributes.
    # These attributes are used in the IAM binding condition below.
    attribute_mapping={
        "google.subject": "assertion.sub",
        "attribute.actor": "assertion.actor",
        "attribute.repository": "assertion.repository",
        "attribute.repository_owner": "assertion.repository_owner",
    },
    # IMPORTANT: restrict to your GitHub org/user only!
    attribute_condition='assertion.repository_owner == "real-limoges"',
)

# Allow GitHub Actions (via WIF) to impersonate the deployer SA.
# This is the final link: GitHub OIDC → WIF Pool → deployer SA → GCP resources.
wif_deployer_binding = gcp.serviceaccount.IAMMember(
    "wif-deployer-binding",
    service_account_id=deployer_sa.name,
    role="roles/iam.workloadIdentityUser",
    member=wif_pool.name.apply(
        lambda pool_name: (
            f"principalSet://iam.googleapis.com/{pool_name}"
            f"/attribute.repository_owner/real-limoges"
        )
    ),
)

# Export values that GitHub Actions workflows need for authentication.
pulumi.export("deployer_sa_email", deployer_sa.email)
pulumi.export("fugue_runner_sa_email", fugue_runner_sa.email)
pulumi.export("wif_provider_name", wif_provider.name)
