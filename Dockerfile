ARG OS_VER
# BASE_IMAGE may be overridden (e.g. with a locally built ComStock image) to run simulations
# in a project-provided environment. It must contain OpenStudio matching OS_VER, plus curl
# and ca-certificates.
ARG BASE_IMAGE=nrel/openstudio:$OS_VER
FROM --platform=linux/amd64 $BASE_IMAGE as buildstockbatch
ARG CLOUD_PLATFORM=aws
ENV DEBIAN_FRONTEND=noninteractive
COPY . /buildstock-batch/
COPY nrel_root_ca.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:/root/.cargo/bin:$PATH"
# --no-bin: don't put a bare python3.11 shim on PATH, where it would shadow a
# python3.11 provided by the base image (e.g. ComStock's, which has PySAM et al.)
RUN uv python install 3.11 --no-bin
# The venv must live outside /var/simdata/openstudio: the base image declares that
# path as a VOLUME, so anything written there during the build is discarded.
ENV VIRTUAL_ENV=/buildstock-batch/.venv
RUN uv venv --python 3.11 "$VIRTUAL_ENV" && uv pip install "/buildstock-batch[${CLOUD_PLATFORM}]"
# venv goes last on PATH: dask/buildstockbatch executables resolve when the base
# image doesn't provide them, without shadowing base-image interpreters and tools.
ENV PATH="$PATH:$VIRTUAL_ENV/bin"
# dask_cloudprovider starts scheduler/worker containers with the legacy
# "dask-scheduler"/"dask-worker" commands, which modern distributed no longer installs.
RUN printf '#!/bin/sh\nexec /buildstock-batch/.venv/bin/dask scheduler "$@"\n' > /usr/local/bin/dask-scheduler \
    && printf '#!/bin/sh\nexec /buildstock-batch/.venv/bin/dask worker "$@"\n' > /usr/local/bin/dask-worker \
    && chmod +x /usr/local/bin/dask-scheduler /usr/local/bin/dask-worker

# Base plus custom gems
FROM --platform=linux/amd64 buildstockbatch as buildstockbatch-custom-gems
RUN sudo cp /buildstock-batch/Gemfile /var/oscli/
# OpenStudio's docker image sets ENV BUNDLE_WITHOUT=native_ext
# https://github.com/NREL/docker-openstudio/blob/3.2.1/Dockerfile#L12
# which overrides anything set via bundle config commands.
# Unset this so that bundle config commands work properly.
RUN unset BUNDLE_WITHOUT
# Note the addition of 'set' in bundle config commands
RUN bundle config set git.allow_insecure true
RUN bundle config set path /var/oscli/gems/
RUN bundle config set without 'test development native_ext'
RUN bundle install --gemfile /var/oscli/Gemfile
