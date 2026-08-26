---
name: delegation
description: Coordinate reviewed foreground subagents within strict limits.
version: 1.0.0
---

# Delegation

Delegation is available only when the server exposes an approved subagent definition. Use at most
three foreground subagents, one level deep, for independent bounded questions. Share the parent
budget and reconcile conflicting evidence before answering.

Subagents cannot approve actions, schedule automation, save memory, create background jobs, or
delegate again. If no approved subagent is available, continue in the main agent without claiming
that delegation occurred.
