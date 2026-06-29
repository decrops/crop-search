# Relationship Claim Repair Prompt

Repair only schema or routing defects in already extracted relationship claims.
Preserve evidence text, provenance, relationship mode, and source URLs. If the
evidence span actually belongs to the parameter lane, remove it from the
relationship output rather than rewriting it as a relationship claim.

Do not invent missing source facts. Do not raise status from `needs_review` to
`accepted`.
