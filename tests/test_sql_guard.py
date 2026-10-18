import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.security.sql_guard import SQLGuard, SQLGuardError
from backend.services.text2sql_service import Text2SQLService


CASES = json.loads((Path(__file__).resolve().parents[1] / "eval/sql_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_existing_sql_cases(case):
    if case["expected_allowed"]:
        result = SQLGuard().validate(case["sql"])
        assert list(result.tables) == case["expected_tables"]
        assert result.limit == case["expected_limit"]
    else:
        with pytest.raises(SQLGuardError) as error:
            SQLGuard().validate(case["sql"])
        assert error.value.code == case["expected_error_code"]


def test_rejected_generated_sql_never_reaches_database_execution():
    model = MagicMock()
    model.generate_structured.return_value = SimpleNamespace(sql="DELETE FROM tickets")
    session = MagicMock()
    with pytest.raises(SQLGuardError):
        Text2SQLService(model).query(session, "query")
    session.execute.assert_not_called()
    assert model.generate_structured.call_count == 2
