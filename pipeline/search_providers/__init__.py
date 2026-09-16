"""Provider-agnostic web search seam.

Every search backend Forage can call — SearXNG today, a paid API later —
implements :class:`~pipeline.search_providers.base.SearchProvider`. This
package is the first nested package under ``pipeline/`` (see ``CLAUDE.md``
invariant 3: that invariant is about top-level packages and is unaffected).
"""

from __future__ import annotations
