# Agent Mesh 1.28.7 from its official image, plus the pinned Event Mesh plugins and SDK
# leaf override the image omits (ADR-0044, ADR-0177). The first stage installs only hashed
# wheels into a staging directory and ends as an unprivileged numeric user. The final
# image uses a PYTHONPATH leaf overlay so the base image remains immutable and auditable,
# then invokes the owned nonzero-on-failure lifecycle instead of the vendor zero-exit main.
FROM solace/solace-agent-mesh:1.28.8@sha256:a5ac3b2b971c68da706287536e295002c0db0725dea81bbe2de8fd3d38f9cd65 AS plugins
USER 0
COPY deploy/agent-mesh/plugin-requirements.txt /tmp/plugin-requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir --no-deps --require-hashes --target /opt/plugins --requirement /tmp/plugin-requirements.txt
USER 65534

FROM solace/solace-agent-mesh:1.28.8@sha256:a5ac3b2b971c68da706287536e295002c0db0725dea81bbe2de8fd3d38f9cd65
COPY --from=plugins /opt/plugins/ /opt/aerial-rescue-runtime/
COPY agent-mesh/aerial_rescue_event_mesh_gateway/ /opt/aerial-rescue-runtime/aerial_rescue_event_mesh_gateway/
COPY agent-mesh/aerial_rescue_runtime_compat/ /opt/aerial-rescue-runtime/aerial_rescue_runtime_compat/
ENV PYTHONPATH=/opt/aerial-rescue-runtime
ENV SOLACE_DEV_MODE=false
ENTRYPOINT ["/opt/venv/bin/python", "-m", "aerial_rescue_runtime_compat"]
CMD ["configs"]
