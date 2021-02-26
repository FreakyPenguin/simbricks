FROM gcr.io/simbricks/simbricks-builder AS builder
COPY --chown=simbricks:simbricks . /simbricks/
WORKDIR /simbricks/
RUN make -j`nproc` all external && \
    /bin/sh docker/cleanup.sh

FROM gcr.io/simbricks/simbricks-builder
COPY --chown=simbricks:simbricks --from=builder /simbricks /simbricks
USER simbricks:simbricks
WORKDIR /simbricks/
