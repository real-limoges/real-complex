"""DNS — Cloud Run domain mappings for realcomplex.systems.

Domain registered on Cloudflare, DNS hosted on Cloudflare.
Cloud Run domain mappings handle SSL certificate provisioning.
Cloudflare DNS records (A + CNAME) point to Google's IPs.
"""

import pulumi
import pulumi_gcp as gcp

from infra.cloud_run import fugue

gcp_config = pulumi.Config("gcp")
project = gcp_config.require("project")
region = gcp_config.get("region") or "us-central1"

domain = "realcomplex.systems"

# ---------- Domain Mappings ----------

apex_mapping = gcp.cloudrun.DomainMapping(
    "fugue-apex",
    name=domain,
    location=region,
    metadata=gcp.cloudrun.DomainMappingMetadataArgs(
        namespace=project,
    ),
    spec=gcp.cloudrun.DomainMappingSpecArgs(
        route_name=fugue.name,
    ),
)

www_mapping = gcp.cloudrun.DomainMapping(
    "fugue-www",
    name=f"www.{domain}",
    location=region,
    metadata=gcp.cloudrun.DomainMappingMetadataArgs(
        namespace=project,
    ),
    spec=gcp.cloudrun.DomainMappingSpecArgs(
        route_name=fugue.name,
    ),
)

# ---------- Exports ----------

pulumi.export("domain", domain)
