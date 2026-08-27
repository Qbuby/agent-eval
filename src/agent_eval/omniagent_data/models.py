from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=256)
    source: str | None = Field(default=None, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=10, ge=1, le=20)


class DescribeRequest(StrictModel):
    entities: list[str] = Field(min_length=1, max_length=5)
    include_relationships: bool = True


class FieldSelection(StrictModel):
    field: str = Field(min_length=1, max_length=128)
    alias: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class AggregateSelection(StrictModel):
    aggregate: Literal["count", "count_distinct", "sum", "avg", "min", "max"]
    field: str | None = Field(default=None, max_length=128)
    alias: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

    @model_validator(mode="after")
    def require_field_except_count(self) -> "AggregateSelection":
        if self.aggregate != "count" and not self.field:
            raise ValueError("aggregate field is required")
        return self


class FilterLeaf(StrictModel):
    field: str = Field(min_length=1, max_length=128)
    op: Literal[
        "eq", "ne", "lt", "lte", "gt", "gte", "in", "contains",
        "starts_with", "is_null", "between",
    ]
    value: Any = None


class FilterNode(StrictModel):
    and_: list["FilterExpression"] | None = Field(default=None, alias="and", max_length=20)
    or_: list["FilterExpression"] | None = Field(default=None, alias="or", max_length=20)
    not_: "FilterExpression | None" = Field(default=None, alias="not")

    @model_validator(mode="after")
    def exactly_one_boolean_operator(self) -> "FilterNode":
        values = [self.and_ is not None, self.or_ is not None, self.not_ is not None]
        if sum(values) != 1:
            raise ValueError("boolean filter must contain exactly one of and, or, not")
        if self.and_ is not None and not self.and_:
            raise ValueError("and filter cannot be empty")
        if self.or_ is not None and not self.or_:
            raise ValueError("or filter cannot be empty")
        return self


FilterExpression = FilterLeaf | FilterNode
FilterNode.model_rebuild()


class OrderBy(StrictModel):
    alias: str = Field(min_length=1, max_length=128)
    direction: Literal["asc", "desc"] = "asc"


class QueryRequest(StrictModel):
    from_: str = Field(alias="from", min_length=1, max_length=64)
    select: list[FieldSelection | AggregateSelection] = Field(min_length=1, max_length=20)
    where: FilterExpression | None = None
    relationships: list[str] = Field(default_factory=list, max_length=3)
    group_by: list[str] = Field(default_factory=list, max_length=2)
    order_by: list[OrderBy] = Field(default_factory=list, max_length=5)
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=4096)


class DataCapabilityError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class QueryLimitError(DataCapabilityError):
    def __init__(self, message: str) -> None:
        super().__init__("QUERY_LIMIT", message)


class FieldDeniedError(DataCapabilityError):
    def __init__(self, message: str) -> None:
        super().__init__("FIELD_DENIED", message)
