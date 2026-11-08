# Dev container image. Base image and tools are pinned by digest or checksum [BSI DEV.4.4].
FROM mcr.microsoft.com/devcontainers/python:3.14-trixie@sha256:66a39503cd5b53c94a57b9a235c6c9371ccf17bafd9c90369e566f638a05ac89

# uv from the official image of the project (pinned by digest).
COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /uvx /usr/local/bin/

# kubeconform from the GitHub release, verified against the published SHA-256.
ARG KUBECONFORM_VERSION=v0.7.0
ARG KUBECONFORM_SHA256=c31518ddd122663b3f3aa874cfe8178cb0988de944f29c74a0b9260920d115d3
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN curl -fsSLo /tmp/kubeconform.tar.gz \
      "https://github.com/yannh/kubeconform/releases/download/${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz" \
    && echo "${KUBECONFORM_SHA256}  /tmp/kubeconform.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/kubeconform.tar.gz -C /usr/local/bin kubeconform \
    && rm /tmp/kubeconform.tar.gz
