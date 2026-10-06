"""Third-party ingestion (spec 2 US-004): pinned, capped, re-rendered samplers.

One module per source — ``agentdojo``, ``llmail_inject``, ``cyberseceval`` —
over the shared plumbing in ``common`` and the renderers in ``render``. Each
reads a local download made by hand *outside* the working tree, samples it
with a fixed seed under a cap, re-renders every payload into a ``search`` /
``page`` / ``text`` record with reserved-domain URLs and appends the records to
``tests/corpus/attacks/``. They print ids and counts only, never payload text.
"""
