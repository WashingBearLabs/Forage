#!/usr/bin/env bash
# docker-entrypoint.sh — minimal, vault-free process launcher for Forage.
#
# Forage is 12-factor: every runtime setting arrives as an environment
# variable at container start (see docs/configuration.md). In particular
# VALKEY_URL arrives ready-made — credentials and all — from the operator's
# env file or secret store. Forage never fetches secrets at boot, so this
# script has exactly one job: exec the command it was handed.
#
# It deliberately prints nothing. VALKEY_URL may carry a password, and the
# service keeps a closed log vocabulary that never echoes it (cache.py's
# `_closed_vocabulary_reason`); a chatty entrypoint would be the one place
# that leaked it into the container log.

set -euo pipefail

exec "$@"
