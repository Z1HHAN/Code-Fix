# syntax=docker/dockerfile:1
# Evidence image only. Pin the Alpine digest in a controlled build pipeline.
FROM alpine:3.20

RUN apk add --no-cache \
      iproute2 \
      procps \
      libcap \
      libcap-utils \
      busybox-extras \
      kmod \
      util-linux \
      ca-certificates \
    && addgroup -S -g 10000 evidence \
    && adduser -S -D -H -u 10000 -G evidence evidence \
    && mkdir -p /work /tmp \
    && chown -R evidence:evidence /work /tmp

USER 10000:10000
WORKDIR /work

# No credentials, private keys, tokens, or internal addresses may be copied here.
ENTRYPOINT ["/bin/sh", "-c"]
CMD ["while :; do sleep 3600; done"]
