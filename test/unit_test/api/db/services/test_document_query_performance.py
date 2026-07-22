#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.db.services import document_service


class _Expression:
    def __init__(self, operation, field, value):
        self.operation = operation
        self.field = field
        self.value = value

    def __and__(self, other):
        return _CombinedExpression(self, other)


class _CombinedExpression:
    def __init__(self, *expressions):
        self.expressions = expressions


class _Field:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return _Expression("eq", self.name, value)

    def in_(self, values):
        return _Expression("in", self.name, list(values))

    def not_in(self, values):
        return _Expression("not_in", self.name, list(values))

    def asc(self):
        return ("asc", self.name)

    def desc(self):
        return ("desc", self.name)


class _Query:
    def __init__(self, rows, trace):
        self.rows = list(rows)
        self.trace = trace
        self.joins = []
        self.conditions = []
        self.trace["queries"].append(self)

    def join(self, model, *args, **kwargs):
        self.joins.append(model.__name__)
        return self

    def where(self, *conditions):
        for condition in conditions:
            self._apply_condition(condition)
        return self

    def _apply_condition(self, condition):
        if isinstance(condition, _CombinedExpression):
            for expression in condition.expressions:
                self._apply_condition(expression)
            return
        if not isinstance(condition, _Expression):
            return
        self.conditions.append((condition.operation, condition.field, condition.value))
        if condition.operation == "eq":
            self.rows = [row for row in self.rows if row.get(condition.field) == condition.value]
        elif condition.operation == "in":
            allowed = set(condition.value)
            self.rows = [row for row in self.rows if row.get(condition.field) in allowed]
        elif condition.operation == "not_in":
            excluded = set(condition.value)
            self.rows = [row for row in self.rows if row.get(condition.field) not in excluded]

    def order_by(self, *args, **kwargs):
        return self

    def paginate(self, page, page_size):
        self.trace["paginated"].append(self)
        start = (page - 1) * page_size
        self.rows = self.rows[start : start + page_size]
        return self

    def count(self):
        self.trace["counted"].append(self)
        return len(self.rows)

    def scalar(self):
        self.trace["counted"].append(self)
        return len(self.rows)

    def dicts(self):
        self.trace["materialized"].append(self)
        return list(self.rows)

    def scalars(self):
        return self

    def iterator(self):
        return iter(row["id"] for row in self.rows)

    def __iter__(self):
        return iter(SimpleNamespace(**row) for row in self.rows)


@pytest.fixture
def query_trace(monkeypatch):
    rows = [
        {"id": "doc-1", "kb_id": "kb-1"},
        {"id": "doc-2", "kb_id": "kb-1"},
        {"id": "doc-3", "kb_id": "kb-1"},
        {"id": "doc-4", "kb_id": "kb-1"},
        {"id": "foreign", "kb_id": "kb-2"},
    ]
    trace = {"queries": [], "counted": [], "paginated": [], "materialized": []}

    class _Model:
        id = _Field("id")
        kb_id = _Field("kb_id")
        name = _Field("name")
        suffix = _Field("suffix")
        run = _Field("run")
        type = _Field("type")
        created_by = _Field("created_by")
        pipeline_id = _Field("pipeline_id")

        @classmethod
        def select(cls, *args, **kwargs):
            return _Query(rows, trace)

        @classmethod
        def getter_by(cls, field_name):
            return getattr(cls, field_name)

    monkeypatch.setattr(document_service.DB, "connect", lambda *args, **kwargs: None)
    monkeypatch.setattr(document_service.DB, "close", lambda *args, **kwargs: None)
    monkeypatch.setattr(document_service.DocumentService, "model", _Model)
    monkeypatch.setattr(document_service.DocumentService, "get_cls_model_fields", classmethod(lambda cls: []))
    monkeypatch.setattr(
        document_service.DocMetadataService,
        "get_metadata_for_documents",
        classmethod(lambda cls, doc_ids, kb_id: {}),
    )
    return trace


@pytest.mark.p2
def test_requested_document_ids_use_a_dataset_scoped_query(query_trace):
    owned = document_service.DocumentService.get_ids_by_kb_id("kb-1", ["doc-2", "missing"])

    assert owned == ["doc-2"]
    query = query_trace["queries"][-1]
    assert ("eq", "kb_id", "kb-1") in query.conditions
    assert ("in", "id", ["doc-2", "missing"]) in query.conditions
    assert query.joins == []


@pytest.mark.p2
def test_count_and_pagination_run_before_detail_joins(query_trace):
    docs, total = document_service.DocumentService.get_by_kb_id(
        "kb-1", 2, 2, "id", False, "", [], [], []
    )

    assert total == 4
    assert [doc["id"] for doc in docs] == ["doc-3", "doc-4"]
    assert len(query_trace["counted"]) == 1
    assert query_trace["counted"][0].joins == []
    assert len(query_trace["paginated"]) == 1
    assert query_trace["paginated"][0].joins == []
    detail_query = query_trace["materialized"][-1]
    assert detail_query.joins == ["UserCanvas", "User"]
    assert ("in", "id", ["doc-3", "doc-4"]) in detail_query.conditions


@pytest.mark.p2
def test_unpaginated_internal_scan_can_skip_unused_total(query_trace):
    docs, total = document_service.DocumentService.get_by_kb_id(
        "kb-1",
        0,
        0,
        "id",
        False,
        "",
        [],
        [],
        [],
        calculate_total=False,
    )

    assert total == 0
    assert [doc["id"] for doc in docs] == ["doc-1", "doc-2", "doc-3", "doc-4"]
    assert query_trace["counted"] == []
    assert query_trace["paginated"] == []


@pytest.mark.p2
def test_delete_route_delegates_ownership_lookup_to_document_service():
    repository_root = Path(__file__).resolve().parents[5]
    source = (repository_root / "api/apps/restful_apis/document_api.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    delete_function = next(
        node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "delete_documents"
    )
    document_service_calls = [
        node.func.attr
        for node in ast.walk(delete_function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "DocumentService"
    ]

    assert "get_ids_by_kb_id" in document_service_calls
    assert "query" not in document_service_calls
