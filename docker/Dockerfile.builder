FROM debian:10
RUN apt-get update && apt-get install -y \
        build-essential \
        git \
        libglib2.0-dev \
        libgoogle-perftools-dev \
        libpcap-dev \
        libpixman-1-dev \
        libboost-dev \
        libboost-iostreams-dev \
        m4 \
        ninja-build \
        python-dev \
        python-setuptools \
        python-six \
        python3-setuptools \
        python3-six \
        scons \
        unzip \
        verilator \
        doxygen \
        python3-sphinx \
        python3-sphinx-rtd-theme \
        python3-breathe \
        breathe-doc \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd -g 1000 simbricks && \
    useradd -u 1000 -g simbricks -s /bin/bash -d /simbricks simbricks
