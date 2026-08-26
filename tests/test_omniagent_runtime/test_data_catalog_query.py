from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from agent_eval.omniagent_data.catalog import CATALOG, describe_entities, search_catalog
from agent_eval.omniagent_data.models import (
    DataCapabilityError,
    DescribeRequest,
    QueryRequest,
    SearchRequest,
)
from agent_eval.omniagent_data.query import compile_query


def test_catalog_hides_sensitive_and_physical_fields() -> None:
    prohibited = {
        "agent_config", "agent_config_b", "full_trace", "actual_output",
        "raw_trace", "content", "turns", "config_fingerprint",
    }
    public = {name for entity in CATALOG.values() for name in entity.fields}
    assert prohibited.isdisjoint(public)
    result = search_catalog(SearchRequest(query="failed evaluations tokens"))
    assert {item["name"] for item in result["items"]} >= {
        "evaluation_runs", "evaluation_results"
    }
    described = describe_entities(DescribeRequest(entities=["evaluation_results"]))
    assert "__tablename__" not in str(described)
    assert "full_trace" not in str(described)


def test_query_contract_rejects_raw_sql_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        QueryRequest.model_validate(
            {"from": "datasets", "select": [{"field": "name"}], "sql": "select *"}
        )
    request = QueryRequest.model_validate(
        {"from": "evaluation_results", "select": [{"field": "full_trace"}]}
    )
    with pytest.raises(DataCapabilityError, match="not selectable"):
        compile_query(request, uuid.uuid4())


def test_compiler_injects_tenant_for_root_and_relationship() -> None:
    tenant_id = uuid.uuid4()
    request = QueryRequest.model_validate(
        {
            "from": "evaluation_runs",
            "relationships": ["results"],
            "select": [
                {"field": "status"},
                {"aggregate": "sum", "field": "results.total_tokens", "alias": "tokens"},
            ],
            "group_by": ["status"],
            "order_by": [{"alias": "tokens", "direction": "desc"}],
            "limit": 20,
        }
    )
    compiled = compile_query(request, tenant_id)
    rendered = str(compiled.statement)
    assert rendered.count("tenant_id") >= 2
    assert "test_runs" in rendered and "test_results" in rendered
    assert str(tenant_id) not in rendered


def test_grouping_relationship_and_cursor_fail_closed() -> None:
    tenant_id = uuid.uuid4()
    with pytest.raises(DataCapabilityError, match="appear in group_by"):
        compile_query(
            QueryRequest.model_validate(
                {
                    "from": "evaluation_results",
                    "select": [
                        {"field": "status"},
                        {"aggregate": "count", "alias": "total"},
                    ],
                    "group_by": ["error_type"],
                }
            ),
            tenant_id,
        )
    with pytest.raises(DataCapabilityError, match="relationship is not allowed"):
        compile_query(
            QueryRequest.model_validate(
                {
                    "from": "datasets",
                    "select": [{"field": "name"}],
                    "relationships": ["users"],
                }
            ),
            tenant_id,
        )
    with pytest.raises(DataCapabilityError, match="cursor pagination"):
        compile_query(
            QueryRequest.model_validate(
                {"from": "datasets", "select": [{"field": "name"}], "cursor": "x"}
            ),
            tenant_id,
        )
