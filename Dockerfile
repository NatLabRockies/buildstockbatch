ARG OS_VER
FROM --platform=linux/amd64 nrel/openstudio:$OS_VER as buildstockbatch
ARG CLOUD_PLATFORM=aws
ENV DEBIAN_FRONTEND=noninteractive
COPY . /buildstock-batch/
COPY nrel_root_ca.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:/root/.cargo/bin:$PATH"
RUN uv python install 3.11
RUN uv venv --python 3.11 && uv pip install "/buildstock-batch[${CLOUD_PLATFORM}]"

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
