"""real-complex — Pulumi entrypoint.

Import order matters: each module may depend on resources from earlier ones.
Pulumi executes this file top-to-bottom, discovers all resources, builds a
dependency graph, and creates/updates them in the correct order.
"""

# Phase 1: GCP Foundation
import infra.project     # noqa: F401 — Enable required APIs (must be first)
import infra.registry    # noqa: F401 — Artifact Registry for Docker images
import infra.networking  # noqa: F401 — VPC + subnet + firewall rules
import infra.secrets     # noqa: F401 — Secret Manager secret containers
import infra.iam         # noqa: F401 — Service accounts + Workload Identity Federation

# Phase 2: Data Services
import infra.cozodb      # noqa: F401 — CozoDB VM on GCE (internal VPC)
import infra.dedalus     # noqa: F401 — GCS bucket + CozoDB VM for Wikipedia graph

# Phase 4: Application Services
import infra.cloud_run   # noqa: F401 — Cloud Run services (Ish, Fugue)

# Phase 6: DNS & Domain
import infra.dns         # noqa: F401 — Cloud DNS zone + domain mapping
