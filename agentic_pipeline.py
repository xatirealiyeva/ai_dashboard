import json
import logging
import os
import time
from functools import lru_cache

import duckdb
from dotenv import load_dotenv
from json_repair import repair_json
from openai import OpenAI

from agent_prompts import (
    INFER_AGENT_PROMPT,
    QUESTION_AGENT_PROMPT,
    RERANKER_AGENT_PROMPT,
    SYNTHESIZER_AGENT_PROMPT,
)
from compiler import (
    ParameterValidationError,
    apply_default_period,
    compile_query,
    load_metrics,
    load_models,
)
import domain_policy
from test_multi_llm import clean_json_text
from unit_retrieval import retrieve_units

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.getenv("DUCKDB_PATH", os.path.join(BASE_DIR, "deposit_real.duckdb"))
LOGGER = logging.getLogger("query_trace")
LOGGER.setLevel(logging.INFO)


def _trace(request_id: str, phase: str, message: str) -> None:
    LOGGER.info("request=%s phase=%s %s", request_id, phase, message)


def _trace_io(request_id: str, agent_name: str, direction: str, content) -> None:
    enabled = os.getenv("AGENT_IO_LOGGING", "true").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return
    serialized = json.dumps(content, ensure_ascii=True, sort_keys=True, default=str)
    LOGGER.info("request=%s phase=agent.%s.io_%s %s", request_id, agent_name, direction, serialized)


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    api_key = os.getenv("QWEN_OPENAI_API_KEY") or os.getenv("QWEN_API_KEY")
    if not api_key:
        raise RuntimeError("QWEN_OPENAI_API_KEY is not configured")
    base_url = (
        os.getenv("QWEN_OPENAI_API_BASE_URL")
        or os.getenv("QWEN_BASE_URL")
        or "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    )
    return OpenAI(api_key=api_key, base_url=base_url)


def _json_object(raw_text: str) -> dict:
    if not raw_text:
        raise ValueError("Qwen returned an empty response")
    cleaned = clean_json_text(raw_text)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        value = repair_json(cleaned, return_objects=True)
    if isinstance(value, list):
        if not value:
            raise ValueError("Qwen returned an empty JSON list")
        value = value[0]
    if not isinstance(value, dict):
        raise ValueError("Qwen response must be a JSON object")
    return value


def _call_agent(
    request_id: str,
    agent_name: str,
    system_prompt: str,
    payload: dict,
    max_tokens: int,
) -> dict:
    phase = f"agent.{agent_name}"
    started = time.perf_counter()
    model = os.getenv("QWEN_OPENAI_MODEL") or os.getenv("QWEN_MODEL", "qwen-plus")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]
    _trace_io(
        request_id,
        agent_name,
        "input",
        {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens},
    )
    _trace(request_id, phase, "started")
    try:
        response = _client().chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=max_tokens,
        )
        raw_output = response.choices[0].message.content
        _trace_io(request_id, agent_name, "output_raw", raw_output)
        result = _json_object(raw_output)
    except Exception as error:
        _trace(request_id, phase, f"failed error={type(error).__name__}: {str(error)[:250]}")
        raise
    _trace_io(request_id, agent_name, "output_parsed", result)
    _trace(request_id, phase, f"complete elapsed_ms={(time.perf_counter() - started) * 1000:.0f}")
    return result


def _unit_card(unit: dict) -> dict:
    based_on = unit.get("based_on") or {}
    return {
        "name": unit["name"],
        "description": unit.get("description_az", ""),
        "intents": unit.get("intents_az", []),
        "examples": unit.get("examples_az", []),
        "operation": unit.get("operation"),
        "model": based_on.get("model"),
        "measure": based_on.get("measure"),
        "outputs": unit.get("outputs", []),
        "parameters": unit.get("parameters", {}),
        "default_period": unit.get("default_period"),
        "temporal_scope": unit.get("temporal_scope"),
        "time_column": unit.get("time_column"),
        "granularity": unit.get("granularity"),
        "ranking": unit.get("ranking"),
        "members": unit.get("members", []),
        "allow_approximate": unit.get("allow_approximate", False),
    }


def _canonical_model_column(name: str, model: dict) -> str:
    lowered = str(name).lower()
    for column in model.get("dimensions", []) + model.get("measures", []):
        if lowered == str(column["column"]).lower() or lowered in {
            str(alias).lower() for alias in column.get("aliases", [])
        }:
            return column["column"]
    return str(name)


def _validate_inferred_query(semantic_query: dict, unit: dict, model: dict = None) -> None:
    parameters = unit.get("parameters") or {}
    allowed_slots = set(parameters) | {"metric"}
    allowed_slots.add("include_current_period")
    if unit.get("operation") == "compare":
        allowed_slots.add("report_period_bounds")
    if unit.get("operation") == "ranked_breakdown":
        allowed_slots.add("rank_direction")
    unexpected = set(semantic_query) - allowed_slots
    if unexpected:
        raise ValueError(f"Infer Agent returned unsupported parameter slots: {sorted(unexpected)}")

    for flag in ("include_current_period", "report_period_bounds"):
        if flag in semantic_query and not isinstance(semantic_query[flag], bool):
            raise ValueError(f"{flag} must be boolean")
    if "rank_direction" in semantic_query and semantic_query["rank_direction"] not in {"asc", "desc"}:
        raise ValueError("rank_direction must be 'asc' or 'desc'")

    group_spec = parameters.get("group_by") or {}
    group_by = semantic_query.get("group_by") or []
    if not isinstance(group_by, list):
        raise ValueError("group_by must be a list")
    normalized_group_by = [
        _canonical_model_column(column, model) if model else str(column)
        for column in group_by
    ]
    allowed_group_by = set(group_spec.get("optional", [])) | set(group_spec.get("required", []))
    if model:
        allowed_group_by = {_canonical_model_column(column, model) for column in allowed_group_by}
    canonical_allowed_group_by = {str(column).lower() for column in allowed_group_by}
    for column in normalized_group_by:
        if str(column).lower() not in canonical_allowed_group_by:
            raise ValueError(f"Infer Agent used column not allowed in group_by: {column}")
    present_group_by = {str(column).lower() for column in normalized_group_by}
    for required in group_spec.get("required", []):
        canonical = _canonical_model_column(required, model) if model else required
        if str(canonical).lower() not in present_group_by:
            normalized_group_by.append(canonical)
            present_group_by.add(str(canonical).lower())
    semantic_query["group_by"] = normalized_group_by

    for slot, slot_value in semantic_query.items():
        if slot in {"metric", "include_current_period", "report_period_bounds", "rank_direction", "group_by"}:
            continue
        if slot_value is None:
            semantic_query[slot] = []
            slot_value = []
        elif slot != "group_by" and isinstance(slot_value, dict):
            if "column" in slot_value:
                normalized_filters = [slot_value]
            else:
                normalized_filters = [
                    {
                        "column": column,
                        "operator": "IN" if isinstance(value, list) else "=",
                        "value": value,
                    }
                    for column, value in slot_value.items()
                ]
            semantic_query[slot] = normalized_filters
            slot_value = semantic_query[slot]
        allowed_columns = set(parameters.get(slot, {}).get("optional", []))
        allowed_columns.update(parameters.get(slot, {}).get("required", []))
        if model:
            allowed_columns = {_canonical_model_column(column, model) for column in allowed_columns}
        canonical_allowed = {str(column).lower() for column in allowed_columns}
        if not isinstance(slot_value, list):
            raise ValueError(f"{slot} must be a list")
        columns = []
        for filter_item in slot_value:
            if not isinstance(filter_item, dict) or not isinstance(filter_item.get("column"), str):
                raise ValueError(f"Each item in {slot} must contain a column")
            if "value" not in filter_item:
                raise ValueError(f"Each filter in {slot} must contain a value")
            column = _canonical_model_column(filter_item["column"], model) if model else filter_item["column"]
            filter_item["column"] = column
            columns.append(column)
        disallowed = sorted({column for column in columns if str(column).lower() not in canonical_allowed})
        if disallowed:
            raise ValueError(f"Infer Agent used columns not allowed in {slot}: {disallowed}")


def _execute_selected_unit(
    question: str,
    question_id: str,
    request_id: str,
    unit: dict,
    candidates: list,
    rerank: dict,
    focus: str = "",
) -> dict:
    metric_name = unit["name"]
    model_name = (unit.get("based_on") or {}).get("model")
    model_schema = load_models().get(model_name)
    if not model_schema:
        raise ValueError(f"Model schema not found for selected unit: {model_name}")

    infer_payload = {
        "question": question,
        "focus": focus,
        "selected_unit": _unit_card(unit),
        "model_schema": model_schema,
    }
    previous_query = None
    inferred = None
    sql = None
    for attempt in range(2):
        if previous_query is not None:
            infer_payload["previous_query"] = previous_query
            infer_payload["validation_error"] = str(previous_query.get("_validation_error", ""))
        inferred = _call_agent(
            request_id,
            "infer" if attempt == 0 else "infer_repair",
            INFER_AGENT_PROMPT,
            infer_payload,
            max_tokens=900,
        )
        if inferred.get("error"):
            return {
                "id": question_id,
                "question": question,
                "type": "error",
                "code": inferred["error"],
                "message": inferred.get("reason", "Could not infer safe query parameters."),
                "retrieval": candidates,
                "rerank": rerank,
            }
        if inferred.get("metric") != metric_name:
            raise ValueError("Infer Agent metric did not match the selected unit")

        compile_started = time.perf_counter()
        _trace(request_id, "sql.compile", f"started question_id={question_id} metric={metric_name} attempt={attempt + 1}")
        try:
            inferred = domain_policy.enforce(inferred, unit, model_schema)
            inferred = apply_default_period(inferred, unit, model_schema)
            _validate_inferred_query(inferred, unit, model_schema)
            _trace_io(request_id, "infer", "output_validated", inferred)
            sql = compile_query(inferred)
        except (ParameterValidationError, ValueError) as error:
            _trace(request_id, "sql.compile", f"validation_failed question_id={question_id} attempt={attempt + 1} error={str(error)[:250]}")
            if attempt == 1:
                raise
            previous_query = dict(inferred)
            previous_query["_validation_error"] = str(error)
            _trace(request_id, "agent.infer.repair", f"retrying after parameter/compiler validation failure question_id={question_id}")
            continue
        except Exception as error:
            _trace(request_id, "sql.compile", f"failed question_id={question_id} error={type(error).__name__}: {str(error)[:250]}")
            raise
        _trace(request_id, "sql.compile", f"complete question_id={question_id} elapsed_ms={(time.perf_counter() - compile_started) * 1000:.0f}")
        break

    if sql is None:
        raise ValueError(f"Could not compile query for selected unit: {metric_name}")

    execute_started = time.perf_counter()
    _trace(request_id, "db.execute", f"started question_id={question_id} read_only=true")
    try:
        with duckdb.connect(DATABASE_PATH, read_only=True) as connection:
            cursor = connection.execute(sql)
            columns = [description[0] for description in cursor.description]
            rows = cursor.fetchall()
    except Exception as error:
        _trace(request_id, "db.execute", f"failed question_id={question_id} error={type(error).__name__}: {str(error)[:250]}")
        raise
    _trace(
        request_id,
        "db.execute",
        f"complete question_id={question_id} columns={len(columns)} rows={len(rows)} "
        f"elapsed_ms={(time.perf_counter() - execute_started) * 1000:.0f}",
    )
    return {
        "id": question_id,
        "question": question,
        "type": "result",
        "metric": metric_name,
        "unit": _unit_card(unit),
        "semantic_query": inferred,
        "sql": sql,
        "columns": columns,
        "rows": rows,
        "retrieval": candidates,
        "rerank": rerank,
        "warnings": ["Approximate aggregate metric"] if unit.get("allow_approximate") else [],
    }


def _run_one(subquestion: dict, request_id: str) -> dict:
    question_id = subquestion["id"]
    question = subquestion["question"]
    retrieval_started = time.perf_counter()
    _trace(request_id, "unit.retrieve", f"started question_id={question_id} top_k=5")
    try:
        ranked_units = retrieve_units(question, top_k=5)
    except Exception as error:
        _trace(request_id, "unit.retrieve", f"failed error={type(error).__name__}: {str(error)[:250]}")
        raise
    _trace(
        request_id,
        "unit.retrieve",
        f"complete question_id={question_id} candidates="
        f"{','.join(item['unit']['name'] for item in ranked_units)} "
        f"elapsed_ms={(time.perf_counter() - retrieval_started) * 1000:.0f}",
    )
    candidates = [
        {**_unit_card(item["unit"]), "similarity": round(float(item["score"]), 4)}
        for item in ranked_units
    ]
    _trace_io(request_id, "unit_retriever", "input", {"query": question, "top_k": 5})
    _trace_io(request_id, "unit_retriever", "output", candidates)

    rerank = _call_agent(
        request_id,
        "reranker",
        RERANKER_AGENT_PROMPT,
        {"subquestion": question, "candidates": candidates},
        max_tokens=500,
    )
    selected_name = rerank.get("selected_unit")
    candidate_names = {candidate["name"] for candidate in candidates}
    if rerank.get("no_match") or selected_name not in candidate_names:
        _trace(request_id, "unit.rerank", f"no supported match question_id={question_id}")
        return {
            "id": question_id,
            "question": question,
            "type": "error",
            "code": "no_matching_metric",
            "message": "No matching metric found.",
            "retrieval": candidates,
            "rerank": rerank,
        }

    selected_unit = next(item["unit"] for item in ranked_units if item["unit"]["name"] == selected_name)
    _trace(
        request_id,
        "unit.rerank",
        f"selected={selected_name} confidence={rerank.get('confidence', 'unknown')} "
        f"question_id={question_id}",
    )

    if selected_unit.get("operation") != "multi_unit":
        return _execute_selected_unit(question, question_id, request_id, selected_unit, candidates, rerank)

    metric_catalog = load_metrics()
    member_results = []
    for index, member in enumerate(selected_unit.get("members") or []):
        member_name = member.get("unit")
        member_unit = metric_catalog.get(member_name)
        if not member_unit:
            member_results.append({
                "id": f"{question_id}_m{index + 1}",
                "question": question,
                "type": "error",
                "code": "invalid_multi_unit_member",
                "message": f"Configured member unit not found: {member_name}",
            })
            continue
        member_id = f"{question_id}_m{index + 1}"
        focus = str(member.get("focus_az", ""))
        _trace(request_id, "multi_unit.member", f"started parent={selected_name} member={member_name} question_id={member_id}")
        try:
            result = _execute_selected_unit(
                question,
                member_id,
                request_id,
                member_unit,
                candidates,
                rerank,
                focus=focus,
            )
            result["parent_unit"] = selected_name
            result["member_focus"] = focus
            member_results.append(result)
            _trace(request_id, "multi_unit.member", f"complete member={member_name} type={result['type']} question_id={member_id}")
        except Exception as error:
            _trace(request_id, "multi_unit.member", f"failed member={member_name} error={type(error).__name__}: {str(error)[:250]}")
            member_results.append({
                "id": member_id,
                "question": question,
                "type": "error",
                "code": "multi_unit_member_failed",
                "message": str(error),
                "parent_unit": selected_name,
                "metric": member_name,
            })
    return {"id": question_id, "type": "multi_unit", "subresults": member_results}


def _validate_dashboard(synthesis: dict, results: list) -> dict:
    dashboard = synthesis.get("dashboard") or {}
    valid_components = []
    component_types = {"metric", "bar", "line", "table"}
    for component in dashboard.get("components", []):
        if not isinstance(component, dict) or component.get("type") not in component_types:
            continue
        data_index = component.get("data_index")
        if not isinstance(data_index, int) or not 0 <= data_index < len(results):
            continue
        columns = set(results[data_index].get("columns", []))
        if component.get("type") in {"bar", "line"}:
            if component.get("x_key") not in columns or component.get("value_key") not in columns:
                continue
            if any(series not in columns for series in component.get("series", [])):
                continue
        valid_components.append(component)
    if len(results) > 1 and not valid_components:
        valid_components = [
            {
                "type": "table",
                "title": result.get("metric") or result.get("id", "Nəticə"),
                "data_index": index,
                "x_key": None,
                "value_key": None,
                "series": [],
            }
            for index, result in enumerate(results)
        ]
    return {
        "needed": bool(dashboard.get("needed")) or len(results) > 1,
        "title": str(dashboard.get("title") or "Analitik nəticələr")[:120],
        "components": valid_components,
    }


def run_agentic_question(question: str, request_id: str) -> dict:
    request_started = time.perf_counter()
    _trace(request_id, "agent.question", "started")
    question_plan = _call_agent(
        request_id,
        "question",
        QUESTION_AGENT_PROMPT,
        {"question": question},
        max_tokens=700,
    )
    subquestions = question_plan.get("subquestions")
    if not isinstance(subquestions, list) or not subquestions:
        raise ValueError("Question Agent did not return any subquestions")
    subquestions = [
        {
            "id": str(item.get("id") or f"q{index + 1}"),
            "question": str(item.get("question", "")).strip(),
            "purpose": str(item.get("purpose", "")).strip(),
        }
        for index, item in enumerate(subquestions[:4])
        if isinstance(item, dict) and str(item.get("question", "")).strip()
    ]
    if not subquestions:
        raise ValueError("Question Agent returned only empty subquestions")
    _trace(
        request_id,
        "agent.question",
        f"complete complex={bool(question_plan.get('is_complex'))} subquestions={len(subquestions)}",
    )

    results = []
    for item in subquestions:
        result = _run_one(item, request_id)
        if result.get("type") == "multi_unit":
            results.extend(result.get("subresults", []))
        else:
            results.append(result)
    unique_results = []
    seen_queries = set()
    duplicate_count = 0
    for result in results:
        semantic_query = result.get("semantic_query")
        if result.get("type") == "result" and semantic_query:
            signature = json.dumps(
                {"metric": result.get("metric"), "semantic_query": semantic_query},
                sort_keys=True,
                ensure_ascii=True,
                default=str,
            )
            if signature in seen_queries:
                duplicate_count += 1
                _trace(request_id, "result.deduplicate", f"suppressed duplicate metric={result.get('metric')} id={result.get('id')}")
                continue
            seen_queries.add(signature)
        unique_results.append(result)
    results = unique_results
    successful_results = [item for item in results if item.get("type") == "result"]
    if successful_results:
        _trace(request_id, "agent.synthesizer", f"started result_sets={len(successful_results)}")
        synthesis = _call_agent(
            request_id,
            "synthesizer",
            SYNTHESIZER_AGENT_PROMPT,
            {
                "original_question": question,
                "question_plan": question_plan,
                "results": [
                    {
                        "id": result["id"],
                        "question": result["question"],
                        "unit": result["unit"],
                        "columns": result["columns"],
                        "rows": result["rows"],
                        "warnings": result["warnings"],
                    }
                    for result in successful_results
                ],
            },
            max_tokens=900,
        )
        synthesis["dashboard"] = _validate_dashboard(synthesis, successful_results)
        _trace(request_id, "agent.synthesizer", "complete dashboard_spec_validated=true")
    else:
        synthesis = {
            "summary": "Uyğun analitik metrika tapılmadı.",
            "insights": [],
            "dashboard": {"needed": False, "title": "Analitik nəticələr", "components": []},
        }

    primary = successful_results[0] if successful_results else (results[0] if results else {})
    response = {
        "type": primary.get("type", "error"),
        "request_id": request_id,
        "question_plan": question_plan,
        "results": results,
        "deduplicated_result_count": duplicate_count,
        "synthesis": synthesis,
        "dashboard": synthesis["dashboard"],
        "insights": synthesis.get("insights", []),
        "retrieval": primary.get("retrieval", []),
        "rerank": primary.get("rerank"),
        "semantic_query": primary.get("semantic_query"),
        "sql": primary.get("sql"),
        "columns": primary.get("columns", []),
        "rows": primary.get("rows", []),
    }
    if primary.get("type") == "error":
        response.update({"code": primary.get("code"), "message": primary.get("message")})
    _trace(request_id, "request", f"complete total_ms={(time.perf_counter() - request_started) * 1000:.0f}")
    return response