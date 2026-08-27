# OmniAgent two-tenant acceptance evidence

This directory contains the redacted, durable evidence from the stage 14
acceptance run completed on 2026-08-27. The disposable fixture file is not
retained because it contains test credentials.

The run used two users in different tenants and two independent browser
contexts. For both tenants it verified:

- the session, activity, approval, artifact, memory, notification, and schedule
  UI surfaces show only the current tenant's canary;
- eight owner-scoped list surfaces contain the current tenant's object and not
  the other tenant's object;
- direct session, job, action, and artifact access for the other tenant returns
  404;
- cross-tenant session/memory deletion, job cancellation, notification read,
  and schedule pause return 404;
- memory search, event cursor, and event session filtering do not disclose the
  other tenant's data; and
- both owners can still access their resources after all rejected mutation
  attempts.

`api-result.json` and `browser-result.json` contain only canary labels and
boolean outcomes. `a-work-panel.png` and `b-work-panel.png` are the final work
panel screenshots for the two isolated browser contexts. File hashes are in
`SHA256SUMS`.

The disposable stack used loopback ports 18082/18083 and the
`oa-two-tenant-e2e-*` Docker resource prefix. It was removed after evidence was
captured. No production resource or pre-existing Kubernetes cluster was used.
