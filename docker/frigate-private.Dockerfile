# Private Linux amd64 prototype. Build context is the verified retained artifact directory.
FROM ghcr.io/blakeblackshear/frigate@sha256:fd60f99ffd4eb6c42360ed4e14953e270586f239309ab3983e55b2e97ffac09a

LABEL io.nanexus.private-prototype="true" \
      io.nanexus.pipeline-checkpoint="827171b7c33d0d56e70778a80381635d39e00e0f" \
      io.nanexus.frigate-version="0.18.2-cfa08c5" \
      io.nanexus.redistribution="not-qualified-LICENSE.nanexus-unresolved"

COPY web/ /opt/frigate/web/
COPY api/ /opt/frigate/frigate/api/
COPY nginx/nginx.conf /usr/local/nginx/conf/nginx.conf
COPY ui/ /nanexus-navigation/
COPY src/ /nanexus/src/
COPY pair/ /nanexus/pair/
COPY startup/boot.py /nanexus/boot.py
COPY startup/service/ /etc/s6-overlay/s6-rc.d/nanexus-private/
COPY startup/service-enable /etc/s6-overlay/s6-rc.d/user/contents.d/nanexus-private
COPY licenses/ /nanexus/licenses/

# Paths only; the bearer, configuration, certificates, media and state are external.
ENV NANEXUS_LIVE_UPSTREAM=http://127.0.0.1:18774 \
    NANEXUS_LIVE_TOKEN_FILE=/nanexus/private/live.token
# Native ENTRYPOINT, USER, CMD and HEALTHCHECK are inherited unchanged.
