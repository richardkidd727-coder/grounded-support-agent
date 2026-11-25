# Knowledge base

Sample support policies for **Driftwood**, a fictional team file-sync and workspace SaaS. These
documents are the RAG corpus; `uv run ingest` chunks, embeds and indexes them.

## Authoring format

Each document starts with YAML front matter:

```yaml
---
doc_id: refund-policy          # stable id, used in citations and the eval set
title: Refund Policy
doc_type: policy               # policy | guide | troubleshooting | reference
audience: [all]                # default audience for every section
version: 2026-09
---
```

A section can narrow its audience with a comment directly under its heading. Child sections
inherit it unless they declare their own:

```markdown
## Pro plan
<!-- audience: pro -->
```

Audience values: `all`, `free`, `pro`, `enterprise`. Retrieval only returns chunks whose audience
includes `all` or the verified customer's tier, so a Free customer can never be quoted the
Enterprise refund limits.

The numeric limits here (refund caps, windows, SLAs) are the same values the tools enforce in
code (`support_agent/tools`). The documents explain the policy; the code is the enforcement.
This file itself is skipped by ingestion.
