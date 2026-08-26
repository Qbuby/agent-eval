---
name: scheduled-automation
description: Prepare reviewed fixed-capability automation schedules.
version: 1.0.0
---

# Scheduled Automation

Schedules contain one fixed, schedulable capability and structured arguments. They never contain a
prompt, URL, webhook, shell command, or arbitrary code. Use `once`, intervals of at least fifteen
minutes, or `daily` with an IANA timezone. Creating, modifying, or resuming a schedule requires a
new governed action and user approval. Pausing may be requested directly by the owner.

Do not schedule irreversible actions. Each trigger is a new durable job with its own result.
