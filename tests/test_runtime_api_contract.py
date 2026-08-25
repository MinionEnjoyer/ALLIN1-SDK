from __future__ import annotations

import json
from pathlib import Path

import pytest

from allin1_sdk.runtime_api_contract import RuntimeApiContract


SCHEMA = Path(__file__).resolve().parents[1] / "sdk/runtime-api-contract.schema.json"


def _contract() -> dict:
    return {
        "schema_version": 1,
        "api_version": 1,
        "assembly": "scripts/TestRuntime.dll",
        "public_type": "Test.ProductApi",
        "source": "src/ProductApi.cs",
        "symbols": [
            {
                "name": "ApiVersion", "kind": "constant",
                "return_type": "int", "value": "1",
            },
            {
                "name": "ICommitParticipant",
                "kind": "interface",
                "capability": "story-save.transactions",
            },
            {
                "name": "Register",
                "kind": "method",
                "capability": "story-save.transactions",
                "return_type": "IDisposable",
                "parameters": [{"name": "value", "type": "ICommitParticipant"}],
                "requires": ["ICommitParticipant"],
            },
        ],
    }


def test_runtime_api_contract_is_strict_data_only_and_versioned(tmp_path: Path) -> None:
    path = tmp_path / "runtime-api.json"
    path.write_text(json.dumps(_contract()), encoding="utf-8")

    contract = RuntimeApiContract.load(path)

    assert contract.api_version == 1
    assert contract.assembly.as_posix() == "scripts/TestRuntime.dll"
    assert contract.public_type == "Test.ProductApi"
    assert contract.symbol_map["Register"].requires == ("ICommitParticipant",)
    assert contract.symbol_map["Register"].capability == "story-save.transactions"
    assert contract.symbol_map["Register"].return_type == "IDisposable"
    assert contract.symbol_map["Register"].parameters[0].parameter_type == (
        "ICommitParticipant"
    )
    assert contract.to_dict()["symbols"][0]["value"] == "1"


def test_runtime_api_schema_exposes_the_loader_signature_contract() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version", "api_version", "assembly", "public_type",
        "source", "symbols",
    }
    symbol = schema["$defs"]["symbol"]
    assert symbol["additionalProperties"] is False
    assert set(symbol["properties"]) == {
        "name", "kind", "capability", "requires", "return_type",
        "parameters", "value",
    }
    parameter = schema["$defs"]["parameter"]
    assert parameter["additionalProperties"] is False
    assert set(parameter["required"]) == {"name", "type"}


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda value: value.update(schema_version=2), "schema_version"),
        (lambda value: value.update(assembly="../escape.dll"), "traversal"),
        (
            lambda value: value["symbols"][2].update(requires=["Missing"]),
            "requires unknown symbols",
        ),
        (
            lambda value: value["symbols"][0].update(extra=True),
            "Unsupported symbols",
        ),
    ],
)
def test_runtime_api_contract_rejects_ambiguous_or_unsafe_data(
    tmp_path: Path, mutate, message: str,
) -> None:
    data = _contract()
    mutate(data)
    path = tmp_path / "runtime-api.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        RuntimeApiContract.load(path)
