from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.pipeline import Pipeline, explicit_parse, validate_delta
from core.model import DecisionDelta
from data.cache import write_snapshot
from data.demo import demo_snapshots
from data.money import USD_CENTS, CENTS_ACCOUNT_PREFIX, to_cents


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # Requests using actual HTTP transport are forbidden; TestClient is in-process.
    def forbidden(*args, **kwargs):
        raise AssertionError("Runtime attempted network access")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    cache_dir = tmp_path / "cache"
    for snapshot in demo_snapshots():
        write_snapshot(cache_dir, snapshot)
    return {"cache_dir": cache_dir, "db_path": tmp_path / "runs.sqlite3", "replay_base_url": "https://replay.test"}


def offline_pipeline(**kwargs):
    def missing(*args):
        raise ModuleNotFoundError("Dev C absent")
    return Pipeline(parser=explicit_parse, renderer=kwargs.get("renderer", missing),
                    validator=kwargs.get("validator", missing), **{k: v for k, v in kwargs.items() if k not in ("renderer", "validator")})


def ask(client, cid="demo_steady", text="Can I afford $1400 today?"):
    return client.post("/ask", json={"text": text, "customer_id": cid})


def test_full_pipeline_all_evidence_and_durable_replay(setup):
    with TestClient(create_app(**setup, pipeline=offline_pipeline())) as client:
        assert client.get("/health").json() == {"ok": True}
        assert len(client.get("/customers").json()) == 5
        model = client.get("/model/demo_steady").json()
        for tid in model["evidence_index"]:
            assert client.get(f"/txn/{tid}").json()["_id"] == tid
        response = ask(client)
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"run_id", "prose", "replay_url", "validated"}
        assert payload["validated"] is True
        assert payload["replay_url"] == f"https://replay.test/r/{payload['run_id']}"
        result = client.get(f"/run/{payload['run_id']}").json()
        assert result["n_paths"] == 500 and result["horizon_weeks"] == 104
        assert len(result["paths"]) == 500 and len(result["paths"][0]["balances"]) == 105
        for claim in result["claims"]:
            assert all(client.get(f"/txn/{tid}").status_code == 200 for tid in claim["evidence"])
        inputs = client.get(f"/run/{payload['run_id']}/inputs").json()
        assert inputs["model"] == model
        assert inputs["delta"]["amount"] == 1400
    # Persisted results survive process/app restart and do not depend on cached refits.
    with TestClient(create_app(**setup, pipeline=offline_pipeline())) as client:
        assert client.get(f"/run/{payload['run_id']}").json() == result


def test_evidence_survives_removal_from_current_cache(setup):
    with TestClient(create_app(**setup, pipeline=offline_pipeline())) as client:
        payload = ask(client).json()
        inputs = client.get(f"/run/{payload['run_id']}/inputs").json()
        tid = next(iter(inputs["evidence"]))
        raw = client.get(f"/txn/{tid}").json()
    for path in setup["cache_dir"].glob("*.json"):
        path.unlink()
    with TestClient(create_app(**setup, pipeline=offline_pipeline())) as client:
        assert client.get("/customers").json() == []
        assert client.get(f"/txn/{tid}").json() == raw
        assert client.get(f"/run/{payload['run_id']}/inputs").json()["evidence"][tid] == raw


def test_cents_evidence_and_units_survive_restart_and_cache_removal(setup):
    snapshot = demo_snapshots()[0]
    bundle = snapshot["accounts"][0]
    bundle["monetary_unit"] = USD_CENTS
    bundle["account"].update(nickname=CENTS_ACCOUNT_PREFIX + "test", balance=231040)
    for collection in ("deposits", "purchases"):
        for row in bundle[collection]:
            row["amount"] = to_cents(row["amount"])
    write_snapshot(setup["cache_dir"], snapshot)
    with TestClient(create_app(**setup, pipeline=offline_pipeline())) as client:
        model = client.get("/model/demo_steady").json()
        assert model["opening_balance"] == 2310.4
        answer = ask(client).json()
        inputs = client.get(f"/run/{answer['run_id']}/inputs").json()
        assert inputs["delta"]["amount"] == 1400
        assert inputs["amount_units"]["model"] == "usd"
        assert set(inputs["amount_units"]["evidence"].values()) == {USD_CENTS}
        row = bundle["purchases"][-1]
        tid = row["_id"]
        transaction = client.get(f"/txn/{tid}")
        assert transaction.json() == row
        assert transaction.headers["X-Monetary-Unit"] == USD_CENTS
    for path in setup["cache_dir"].glob("*.json"):
        path.unlink()
    with TestClient(create_app(**setup, pipeline=offline_pipeline())) as client:
        transaction = client.get(f"/txn/{tid}")
        assert transaction.json() == row and transaction.headers["X-Monetary-Unit"] == USD_CENTS
        assert client.get(f"/run/{answer['run_id']}/inputs").json() == inputs


def test_existing_database_without_unit_rows_defaults_to_dollars(setup):
    with TestClient(create_app(**setup, pipeline=offline_pipeline())) as client:
        answer = ask(client).json()
        inputs = client.get(f"/run/{answer['run_id']}/inputs").json()
        tid = next(iter(inputs["evidence"]))
        with client.app.state.store.connect() as connection:
            connection.execute("DELETE FROM run_units")
            connection.execute("DELETE FROM transaction_units")
    for path in setup["cache_dir"].glob("*.json"):
        path.unlink()
    with TestClient(create_app(**setup, pipeline=offline_pipeline())) as client:
        assert client.get(f"/txn/{tid}").headers["X-Monetary-Unit"] == "usd"
        assert client.get(f"/run/{answer['run_id']}/inputs").json()["amount_units"] == inputs["amount_units"]


def test_each_fixture_can_be_asked_without_network(setup):
    with TestClient(create_app(**setup, pipeline=offline_pipeline())) as client:
        for customer in client.get("/customers").json():
            assert ask(client, customer["customer_id"]).status_code == 200


def test_default_auto_parser_when_dev_c_absent(setup, monkeypatch):
    monkeypatch.setenv("COUNTERFACTUAL_PARSER", "auto")
    with TestClient(create_app(**setup)) as client:
        assert ask(client).status_code == 200
        assert ask(client, text="Can I afford a car?").status_code == 422


@pytest.mark.parametrize("text", ["Can I afford a car?", "Can I afford $1400 in December?",
                                   "Can I afford $1400 today if I lose my job?", "Can I afford $0 today?",
                                   "spend $1400 tomorrow", "spend $-5 today"])
def test_ambiguous_or_unsupported_offline_input_rephrases(setup, text):
    with TestClient(create_app(**setup, pipeline=offline_pipeline())) as client:
        assert ask(client, text=text).status_code == 422


@pytest.mark.parametrize("text,amount,day,cadence", [
    ("Can I afford $1,400 today?", 1400, 0, None),
    ("Can I afford $65/month starting today?", 65, 0, 30),
    ("spend $25/week in 30 days", 25, 30, 7)])
def test_explicit_parser(text, amount, day, cadence):
    delta = validate_delta(explicit_parse(text))
    assert (delta.amount, delta.start_day, delta.cadence_days) == (amount, day, cadence)


def test_low_confidence_and_parse_outage_rephrase(setup):
    pipeline = offline_pipeline()
    pipeline.parser = lambda text: DecisionDelta("one_time", 1400, 0, confidence=.2)
    with TestClient(create_app(**setup, pipeline=pipeline)) as client:
        assert ask(client).status_code == 422
        def broken(text):
            raise RuntimeError("parser offline")
        pipeline.parser = broken
        assert ask(client).status_code == 422


def test_render_retry_validation_and_template_on_failure(setup):
    calls = []
    def renderer(claims, culprit):
        calls.append(claims)
        return "There is a fabricated 999% risk."
    pipeline = offline_pipeline(renderer=renderer, validator=lambda prose, claims: {"passed": False})
    with TestClient(create_app(**setup, pipeline=pipeline)) as client:
        payload = ask(client).json()
    assert len(calls) == 2
    assert "999%" not in payload["prose"] and payload["validated"] is True


def test_valid_second_render_used(setup):
    calls = []
    def renderer(claims, culprit):
        calls.append(1)
        return "rejected" if len(calls) == 1 else "approved prose"
    pipeline = offline_pipeline(renderer=renderer, validator=lambda prose, claims: {"passed": prose == "approved prose"})
    with TestClient(create_app(**setup, pipeline=pipeline)) as client:
        assert ask(client).json()["prose"] == "approved prose"
    assert len(calls) == 2


def test_validator_outage_cannot_release_unchecked_prose(setup):
    def broken(*args):
        raise RuntimeError("validator offline")
    pipeline = offline_pipeline(renderer=lambda *args: "unchecked 999%", validator=broken)
    with TestClient(create_app(**setup, pipeline=pipeline)) as client:
        response = ask(client)
        assert response.status_code == 200 and "999%" not in response.json()["prose"]


@pytest.mark.parametrize("value", ["true", 1, {}, {"passed": "false"}])
def test_validator_requires_boolean_true(setup, value):
    pipeline = offline_pipeline(renderer=lambda *args: "unchecked", validator=lambda *args: value)
    with TestClient(create_app(**setup, pipeline=pipeline)) as client:
        assert ask(client).json()["prose"] != "unchecked"


def test_simulation_failure_surfaces_honestly(setup):
    def broken(*args, **kwargs):
        raise RuntimeError("simulation failed")
    with TestClient(create_app(**setup, pipeline=offline_pipeline(simulator=broken))) as client:
        response = ask(client)
        assert response.status_code == 503
        assert "no financial estimate" in response.json()["detail"]


def test_missing_claim_evidence_cannot_release_estimate(setup):
    from core.run import run
    def broken(model, delta, **kwargs):
        result = run(model, delta, **kwargs)
        return replace(result, claims=[replace(c, evidence=["unresolved"]) for c in result.claims])
    with TestClient(create_app(**setup, pipeline=offline_pipeline(simulator=broken))) as client:
        assert ask(client).status_code == 503


def test_storage_failure_does_not_return_broken_replay(setup, monkeypatch):
    with TestClient(create_app(**setup, pipeline=offline_pipeline())) as client:
        def broken(*args):
            raise OSError("storage full")
        monkeypatch.setattr(client.app.state.store, "save", broken)
        response = ask(client)
        assert response.status_code == 503
        assert "saved" in response.json()["detail"]


def test_missing_resources_and_request_validation(setup):
    with TestClient(create_app(**setup, pipeline=offline_pipeline())) as client:
        for path in ("/model/missing", "/txn/missing", "/run/missing", "/run/missing/inputs"):
            assert client.get(path).status_code == 404
        assert ask(client, cid="missing").status_code == 404
        assert ask(client, text=" ").status_code == 422
        assert client.post("/ask", json={"text": "x", "customer_id": "../bad"}).status_code == 422


def test_unsupported_model_is_422(setup):
    with TestClient(create_app(**setup, pipeline=offline_pipeline())) as client:
        client.app.state.cache.snapshots["demo_steady"]["accounts"][0]["deposits"] = []
        assert ask(client).status_code == 422


def test_repeated_questions_get_distinct_immutable_run_ids(setup):
    with TestClient(create_app(**setup, pipeline=offline_pipeline())) as client:
        first, second = ask(client).json(), ask(client).json()
        assert first["run_id"] != second["run_id"]
        a = client.get(f"/run/{first['run_id']}").json()
        b = client.get(f"/run/{second['run_id']}").json()
        assert a["paths"] == b["paths"] and a["claims"] == b["claims"]
