# forage-searxng — the SearXNG companion image Forage's search stage talks to.
#
# It is upstream's image plus one thing: the baked configuration in config/.
# No bind mount, no entrypoint wrapper, no forked code. What the config buys is
# the engine set Forage actually queries (pinned, so an upstream release adding
# an unvetted engine cannot change what a Forage query fans out to) and the
# JSON output format Forage's client path needs — both of which upstream's
# generated default settings.yml would not give.
#
# HONEST DEFAULTS — read config/settings.yml and config/limiter.toml with this:
#   * no baked secret_key. SEARXNG_SECRET is REQUIRED at runtime; an unset one
#     is a hard start failure, by upstream's own schema. A shipped known secret
#     would be worse than no secret at all.
#   * no IP-trust relaxations: no pass_ip wildcard, no trusted_proxies
#     additions, no forwarded-header settings. A public image must not arrive
#     believing whatever X-Forwarded-For a caller sends it.
#   * limiter: false, stated rather than pretended. A working limiter needs a
#     Valkey backend (SEARXNG_VALKEY_URL) AND refuses this image's only client:
#     measured, Forage's httpx client is 429'd on its first request and even a
#     browser-shaped one gets 4 JSON requests an hour. `limiter: true` here
#     would be an outage switch labelled as protection. docs/searxng.md carries
#     the numbers and the opt-in; the CI smoke exercises all three states.
#
# The base is digest-pinned. `docker pull searxng/searxng:latest` at deploy time
# — Poppy's old habit — means the image a smoke tested and the image a consumer
# runs are different builds, and the version comment below would be a lie.
# Bumping is a deliberate act with its own tag: see docs/searxng.md.
#
# Dockerfile has no inline comments: a `#` after an instruction's arguments is
# just another argument, and FROM rejects it (proven in US-003). The version a
# digest denotes therefore lives on the comment line directly above it.
#
# searxng/searxng 2026.9.7-3e454637f, published 2026-09-07, revision
# 3e454637fb9829756c805dd9c02100f0bc9520fd. Multi-arch index: linux/amd64,
# linux/arm64, linux/arm/v7.
FROM searxng/searxng@sha256:1dab138ea70a8ceb4d1c182f5c8f256088406c1742b99e279976157dbe1e9759

# --chown rather than relying on the entrypoint's chown -R: the entrypoint only
# fixes ownership when it runs as root, and warns (then carries on) otherwise.
# Getting it right at build time means the image works the same under a
# read-only or non-root runtime.
COPY --chown=searxng:searxng config/ /etc/searxng/
