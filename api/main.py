"""Dev B's synchronous worker pipeline and frozen HTTP surface."""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from api.pipeline import (Pipeline, RephraseRequired, check_evidence, checked_template,
                          parse, validate_delta, validation_passed)
from api.store import RunStore
from data.cache import CacheRepository
from data.fit import FitError, fit

logger = logging.getLogger(__name__)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=4000)
    customer_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class AskResponse(BaseModel):
    run_id: str
    prose: str
    replay_url: str
    validated: bool


def create_app(*, cache_dir: Path | None = None, db_path: Path | None = None,
               replay_base_url: str | None = None, pipeline: Pipeline | None = None) -> FastAPI:
    load_dotenv()
    cache_dir = cache_dir or Path(os.getenv("COUNTERFACTUAL_CACHE_DIR", "data/cache"))
    db_path = db_path or Path(os.getenv("COUNTERFACTUAL_DB_PATH", "data/runs.sqlite3"))
    replay_base_url = (replay_base_url or os.getenv("REPLAY_BASE_URL", "http://localhost:3000")).rstrip("/")
    if not replay_base_url.startswith(("https://", "http://")):
        raise ValueError("REPLAY_BASE_URL must be an absolute HTTP(S) frontend origin")
    mode = os.getenv("COUNTERFACTUAL_PARSER", "auto")
    if mode not in ("auto", "llm", "explicit"):
        raise ValueError("COUNTERFACTUAL_PARSER must be auto, llm, or explicit")
    pipeline = pipeline or Pipeline(parser=lambda text: parse(text, mode))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.cache = CacheRepository(cache_dir)
        app.state.store = RunStore(db_path)
        app.state.models = {}
        yield

    app = FastAPI(title="Counterfactual API", lifespan=lifespan)
    app.state.pipeline = pipeline

    def model_for(customer_id: str):
        try:
            snapshot = app.state.cache.snapshot(customer_id)
        except KeyError:
            raise HTTPException(404, "Customer is not cached; ingest it before asking a question") from None
        try:
            if customer_id not in app.state.models:
                model = fit(snapshot)
                for tid in model.evidence_index:
                    app.state.cache.transaction(tid)
                app.state.models[customer_id] = model
            return app.state.models[customer_id]
        except (FitError, ValueError, KeyError) as exc:
            logger.warning("model_fit_failed customer_id=%s error_type=%s", customer_id, type(exc).__name__)
            raise HTTPException(422, "The cached history cannot support a cashflow model; check the ingest report") from None
        except Exception as exc:
            logger.error("model_load_failed error_type=%s", type(exc).__name__)
            raise HTTPException(503, "Cached model is unavailable; please try again later") from None

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/customers")
    def customers():
        return app.state.cache.customers()

    @app.get("/model/{customer_id}")
    def model(customer_id: str):
        return model_for(customer_id).to_dict()

    @app.get("/txn/{txn_id}")
    def transaction(txn_id: str, response: Response):
        try:
            response.headers["X-Monetary-Unit"] = app.state.cache.transaction_unit(txn_id)
            return app.state.cache.transaction(txn_id)
        except KeyError:
            try:
                response.headers["X-Monetary-Unit"] = app.state.store.transaction_unit(txn_id)
                return app.state.store.transaction(txn_id)
            except KeyError:
                raise HTTPException(404, "Transaction is not cached") from None
            except Exception as exc:
                logger.error("evidence_load_failed error_type=%s", type(exc).__name__)
                raise HTTPException(503, "Archived evidence is temporarily unavailable") from None

    @app.get("/run/{run_id}")
    def stored_run(run_id: str):
        try:
            return app.state.store.get(run_id)
        except KeyError:
            raise HTTPException(404, "Run was not found") from None
        except Exception as exc:
            logger.error("replay_load_failed error_type=%s", type(exc).__name__)
            raise HTTPException(503, "Stored replay is temporarily unavailable") from None

    @app.get("/run/{run_id}/inputs")
    def run_inputs(run_id: str):
        # Additive route: D can show the actual model used rather than a later refit.
        try:
            return app.state.store.inputs(run_id)
        except KeyError:
            raise HTTPException(404, "Run was not found") from None
        except Exception as exc:
            logger.error("replay_inputs_failed error_type=%s", type(exc).__name__)
            raise HTTPException(503, "Stored replay inputs are temporarily unavailable") from None

    @app.post("/ask", response_model=AskResponse)
    def ask(body: AskRequest):
        started = time.perf_counter()
        # Cheap customer check before any potentially paid parsing call.
        if body.customer_id not in app.state.cache.snapshots:
            raise HTTPException(404, "Customer is not cached")
        try:
            delta = validate_delta(pipeline.parser(body.text))
        except RephraseRequired as exc:
            raise HTTPException(422, str(exc)) from None
        except Exception as exc:
            logger.warning("parse_failed error_type=%s", type(exc).__name__)
            raise HTTPException(422, "Please rephrase with a specific amount and date; parsing was unavailable") from None
        model = model_for(body.customer_id)
        try:
            result = pipeline.simulator(model, delta, run_id=str(uuid.uuid4()))
            check_evidence(result, app.state.cache.transaction)
            fallback = checked_template(result)
        except Exception as exc:
            logger.error("simulation_failed error_type=%s", type(exc).__name__)
            raise HTTPException(503, "The simulation failed; no financial estimate was produced") from None
        prose = fallback
        for attempt in range(2):
            try:
                candidate = pipeline.renderer(result.claims, result.culprit)
                if not isinstance(candidate, str) or not candidate.strip():
                    raise ValueError("Renderer returned empty prose")
                if validation_passed(pipeline.validator(candidate, result.claims)):
                    prose = candidate
                    break
                logger.warning("render_rejected attempt=%s", attempt + 1)
            except ModuleNotFoundError as exc:
                logger.info("render_dependency_unavailable module=%s", exc.name)
                break
            except Exception as exc:
                logger.warning("render_failed attempt=%s error_type=%s", attempt + 1, type(exc).__name__)
        response = {"run_id": result.run_id, "prose": prose,
                    "replay_url": f"{replay_base_url}/r/{result.run_id}", "validated": True}
        try:
            evidence = {tid: app.state.cache.transaction(tid) for tid in model.evidence_index}
            units = {tid: app.state.cache.transaction_unit(tid) for tid in evidence}
            app.state.store.save(result, model, delta, response, evidence, evidence_units=units)
        except Exception as exc:
            logger.error("run_persist_failed error_type=%s", type(exc).__name__)
            raise HTTPException(503, "The replay could not be saved; please retry your question") from None
        elapsed = time.perf_counter() - started
        logger.info("ask_completed run_id=%s elapsed_seconds=%.4f", result.run_id, elapsed)
        return response

    return app


app = create_app()
