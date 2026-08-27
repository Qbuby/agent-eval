"""Governed, tenant-scoped data discovery and query capabilities."""

from agent_eval.omniagent_data.catalog import describe_entities, search_catalog
from agent_eval.omniagent_data.query import execute_query

__all__ = ["describe_entities", "execute_query", "search_catalog"]
