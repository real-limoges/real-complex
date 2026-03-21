"""CozoDB — GCE e2-micro VM with persistent data disk and daily GCS backups.

Runs CozoDB on an internal-only VM (no public IP).  Cloud Run services reach
it via Direct VPC Egress on the internal IP (port 9070).

The VM uses debian-12 with CozoDB installed directly.  A separate persistent
disk stores data so it survives VM recreation.

Daily backups copy the database to a GCS bucket with a 30-day lifecycle.

SSH access: gcloud compute ssh cozodb --zone=us-central1-a --tunnel-through-iap
"""

import pulumi
import pulumi_gcp as gcp

from infra.project import api_services
from infra.networking import vpc, subnet

gcp_config = pulumi.Config("gcp")
project = gcp_config.require("project")
region = gcp_config.get("region") or "us-central1"
zone = f"{region}-a"

# ---------- GCS Backup Bucket ----------

backups_bucket = gcp.storage.Bucket(
    "realcomplex-backups",
    name=f"{project}-backups",
    location=region,
    storage_class="STANDARD",
    uniform_bucket_level_access=True,
    lifecycle_rules=[
        gcp.storage.BucketLifecycleRuleArgs(
            action=gcp.storage.BucketLifecycleRuleActionArgs(type="Delete"),
            condition=gcp.storage.BucketLifecycleRuleConditionArgs(age=30),
        ),
    ],
    opts=pulumi.ResourceOptions(
        depends_on=[api_services["compute.googleapis.com"]],
        protect=True,
    ),
)

# ---------- Service Account ----------

cozodb_vm_sa = gcp.serviceaccount.Account(
    "cozodb-vm",
    account_id="cozodb-vm",
    display_name="CozoDB VM SA",
    opts=pulumi.ResourceOptions(
        depends_on=[api_services["iam.googleapis.com"]],
    ),
)

# SA can write to the backups bucket
gcp.storage.BucketIAMMember(
    "cozodb-vm-backup-writer",
    bucket=backups_bucket.name,
    role="roles/storage.objectAdmin",
    member=cozodb_vm_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# ---------- Persistent Data Disk ----------

data_disk = gcp.compute.Disk(
    "cozodb-data",
    name="cozodb-data",
    size=10,
    type="pd-standard",
    zone=zone,
    opts=pulumi.ResourceOptions(
        protect=True,
        depends_on=[api_services["compute.googleapis.com"]],
    ),
)

# ---------- Startup Script ----------

_COZO_VERSION = "0.7.6"

_STARTUP_SCRIPT = r"""#!/bin/bash
set -euo pipefail

LOG="/var/log/cozodb-startup.log"
exec > >(tee -a "$LOG") 2>&1

echo "$(date): CozoDB VM startup beginning..."

DATA_DIR="/mnt/disks/cozodb"
DEVICE="/dev/disk/by-id/google-cozodb-data"
BUCKET="__BUCKET__"
COZO_VERSION="__COZO_VERSION__"

# Format and mount the data disk if not already mounted
if ! mountpoint -q "$DATA_DIR"; then
    mkdir -p "$DATA_DIR"
    if ! blkid "$DEVICE" &>/dev/null; then
        echo "$(date): Formatting data disk..."
        mkfs.ext4 -m 0 -F -E lazy_itable_init=0 "$DEVICE"
    fi
    mount -o discard,defaults "$DEVICE" "$DATA_DIR"
    echo "$DEVICE $DATA_DIR ext4 discard,defaults,nofail 0 2" >> /etc/fstab
fi

# Install CozoDB if not present
if ! command -v cozo &>/dev/null; then
    echo "$(date): Installing CozoDB v${COZO_VERSION}..."
    ARCH=$(dpkg --print-architecture)
    if [ "$ARCH" = "amd64" ]; then
        COZO_ARCH="x86_64"
    else
        COZO_ARCH="aarch64"
    fi
    COZO_URL="https://github.com/cozodb/cozo/releases/download/v${COZO_VERSION}/cozo-bin-${COZO_VERSION}-${COZO_ARCH}-unknown-linux-gnu.tar.gz"
    curl -sSfL "$COZO_URL" | tar xz -C /usr/local/bin
    chmod +x /usr/local/bin/cozo
fi

# Create systemd service
cat > /etc/systemd/system/cozodb.service <<EOF
[Unit]
Description=CozoDB
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/cozo server --path ${DATA_DIR}/data.db --port 9070 --bind 0.0.0.0 --engine rocksdb
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cozodb
systemctl restart cozodb

# Daily backup cron: copy DB to GCS at 3am UTC
cat > /etc/cron.d/cozodb-backup <<EOF
0 3 * * * root gcloud storage cp -r "${DATA_DIR}/data.db" "gs://${BUCKET}/daily/\$(date +\\%Y-\\%m-\\%d)/" 2>/dev/null
EOF

echo "$(date): CozoDB VM startup complete. Listening on :9070"
"""

# ---------- GCE Instance ----------

cozodb_vm = gcp.compute.Instance(
    "cozodb",
    name="cozodb",
    machine_type="e2-micro",
    zone=zone,
    boot_disk=gcp.compute.InstanceBootDiskArgs(
        initialize_params=gcp.compute.InstanceBootDiskInitializeParamsArgs(
            image="debian-cloud/debian-12",
            size=10,
            type="pd-standard",
        ),
    ),
    attached_disks=[
        gcp.compute.InstanceAttachedDiskArgs(
            source=data_disk.self_link,
            device_name="cozodb-data",
        ),
    ],
    network_interfaces=[
        gcp.compute.InstanceNetworkInterfaceArgs(
            network=vpc.id,
            subnetwork=subnet.id,
            # No access_configs — internal only. Uses Cloud NAT for outbound.
        ),
    ],
    tags=["cozodb"],
    service_account=gcp.compute.InstanceServiceAccountArgs(
        email=cozodb_vm_sa.email,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    ),
    metadata={
        "startup-script": backups_bucket.name.apply(
            lambda name: _STARTUP_SCRIPT.replace("__BUCKET__", name).replace(
                "__COZO_VERSION__", _COZO_VERSION
            )
        ),
    },
    opts=pulumi.ResourceOptions(
        depends_on=[api_services["compute.googleapis.com"]],
    ),
)

# ---------- Exports ----------

# Module-level variable for cloud_run.py to import
cozodb_internal_ip = cozodb_vm.network_interfaces[0].network_ip

pulumi.export("cozodb_internal_ip", cozodb_internal_ip)
pulumi.export("cozodb_vm_name", cozodb_vm.name)
pulumi.export("cozodb_backups_bucket", backups_bucket.name)
