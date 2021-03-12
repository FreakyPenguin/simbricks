FROM gcr.io/simbricks/simbricks-builder AS builder
COPY --chown=simbricks:simbricks . /simbricks/
WORKDIR /simbricks/
RUN make -j`nproc` all && \
    make -j`nproc` sims/external/qemu/ready && \
    make -j`nproc` sims/external/gem5/ready && \
    make -j`nproc` sims/external/ns-3/ready && \
    /bin/sh docker/cleanup.sh

FROM gcr.io/simbricks/simbricks-builder
COPY --chown=simbricks:simbricks --from=builder /simbricks /simbricks
USER simbricks:simbricks
WORKDIR /simbricks/
