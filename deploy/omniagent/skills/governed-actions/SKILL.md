---
name: governed-actions
description: Prepare exact fixed actions for independent user approval.
version: 1.0.0
---

# Governed Actions

For a write or external side effect, prepare one registered capability with complete immutable
arguments. Explain the impact preview and wait for the browser approval result. Never claim that
conversation text is approval, never change arguments after preparation, and never resubmit a
different action under the same idempotency key.

The browser user alone approves or denies. The model has no approval capability. Deletion of
platform data, identity and role management, credentials, provider configuration, arbitrary HTTP,
SQL, shell, and Kubernetes administration remain prohibited.
