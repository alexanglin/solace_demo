# Agent Mesh 1.28.7 from its official image, plus the two pinned Event Mesh plugins the
# image omits (docs/adr/0044). The first stage installs the wheels as root into a staging
# directory and ends as an unprivileged numeric user; the final stage copies them into the
# runtime's virtual environment and inherits the image's own non-root user, so no USER
# instruction names a non-numeric account. Developer mode is off here and in the compose
# file because the image defaults it on.
FROM solace/solace-agent-mesh:1.28.7@sha256:25dc09b55e8a718e5a690e4abba039cbd032872cd6d4c402b7c69d1dead70255 AS plugins
USER 0
COPY plugin-requirements.txt /tmp/plugin-requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir --no-deps --require-hashes --target /opt/plugins --requirement /tmp/plugin-requirements.txt
USER 65534

FROM solace/solace-agent-mesh:1.28.7@sha256:25dc09b55e8a718e5a690e4abba039cbd032872cd6d4c402b7c69d1dead70255
COPY --from=plugins /opt/plugins/ /opt/venv/lib/python3.13/site-packages/
ENV SOLACE_DEV_MODE=false
CMD ["run", "--system-env", "configs"]
