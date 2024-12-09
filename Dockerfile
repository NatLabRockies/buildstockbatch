FROM nrel/openstudio:3.8.0

# Set environment variables
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
ARG DEBIAN_FRONTEND=noninteractive

# Install Python 3.11 using deadsnakes PPA and necessary tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common wget build-essential libssl-dev libffi-dev zlib1g-dev \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3.11 python3.11-venv python3.11-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Set up virtual environment
ENV VENV_DIR=/opt/venv
RUN python3.11 -m venv $VENV_DIR
ENV PATH=$VENV_DIR/bin:$PATH

# Install Python dependencies
COPY . /buildstock-batch/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir "dask[distributed]" "bokeh" /buildstock-batch

# Clean unnecessary files
RUN rm -rf /var/log/* /buildstock-batch/__pycache__

# Default command
CMD ["/bin/bash"]
