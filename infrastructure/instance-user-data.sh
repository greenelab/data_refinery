#!/bin/bash
# set -e

echo 'DOCKER_STORAGE_OPTIONS="--storage-driver overlay2"' > /etc/sysconfig/docker-storage
/etc/init.d/docker restart

# This is the public key from above - one-time step.
curl https://keybase.io/hashicorp/pgp_keys.asc | gpg --import

# Download the binary and signature files.
curl -0s https://releases.hashicorp.com/nomad/0.7.1/nomad_0.7.1_linux_amd64.zip > nomad_0.7.1_linux_amd64.zip
curl -0s https://releases.hashicorp.com/nomad/0.7.1/nomad_0.7.1_SHA256SUMS > nomad_0.7.1_SHA256SUMS
curl -0s https://releases.hashicorp.com/nomad/0.7.1/nomad_0.7.1_SHA256SUMS.sig > nomad_0.7.1_SHA256SUMS.sig

# Verify the signature file is untampered.
gpg_ok=$(gpg --verify nomad_0.7.1_SHA256SUMS.sig nomad_0.7.1_SHA256SUMS |& grep Good)
if [[ "$gpg_ok" = "" ]]; then
    echo "Could not verify the signature from HashiCorp Security <security@hashicorp.com>."
    exit 1
fi

# shasum is not on the ec2 instance. Start by getting it on there.

# Verify the SHASUM matches the binary.
shasum_ok=$(shasum -a 256 -c nomad_0.7.1_SHA256SUMS |& grep OK)
if [[ "$shasum_ok" = "" ]]; then
    echo "Could not verify the Nomad checksum provided by Hashicorp."
    exit 1
fi

unzip -d /usr/bin nomad_0.7.1_linux_amd64.zip
chmod a+rx /usr/bin/nomad
