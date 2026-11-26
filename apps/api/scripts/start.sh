#!/bin/sh
# Container entrypoint: migrate -> index knowledge base -> reset demo data -> serve, in one Python
# process (see support_agent/cli/serve.py for why). exec so it is PID 1 and receives SIGTERM
# directly: uvicorn's graceful shutdown then drains in-flight turns.
set -eu
exec serve
