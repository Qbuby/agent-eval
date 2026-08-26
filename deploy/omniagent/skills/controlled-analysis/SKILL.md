---
name: controlled-analysis
description: Run bounded Python analysis over declared artifacts or snapshots.
version: 1.0.0
---

# Controlled Analysis

Use analysis only when catalog queries cannot express the required computation. Declare every
input artifact, write only to the supplied output directory, and produce compact reproducible
outputs. Do not use shell commands, subprocesses, network access, dynamic package installation,
absolute paths, or undeclared inputs. Report assumptions, input identifiers, and data timestamps.

Analysis execution is not authorization. It cannot approve actions, create schedules, persist
memory, or obtain service credentials.
