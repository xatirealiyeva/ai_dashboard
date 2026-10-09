import io
import logging
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agentic_pipeline
import domain_policy
from compiler import apply_default_period, compile_query, load_metrics, load_models
from unit_retrieval import load_unit_documents


class AgenticPipelineTests(unittest.TestCase):
    def setUp(self):
        self.portfolio_unit = next(
            item["unit"]
            for item in load_unit_documents()
            if item["unit"]["name"] == "portfel_balansi"
        )

    def _responses(self, questions):
        return {
            "question": {
                "is_complex": len(questions) > 1,
                "subquestions": [
                    {"id": f"q{index + 1}", "question": question, "purpose": "test"}
                    for index, question in enumerate(questions)
                ],
            },
            "reranker": {
                "selected_unit": "portfel_balansi",
                "no_match": False,
                "reason": "Portfolio balance query",
                "confidence": 0.99,
            },
            "infer": {"metric": "portfel_balansi", "filters": [], "group_by": []},
            "synthesizer": {
                "summary": "Portfolio balance result",
                "insights": [],
                "dashboard": {"needed": False, "title": "Portfolio", "components": []},
            },
        }

    def _run(self, questions):
        responses = self._responses(questions)

        def fake_agent(_request_id, agent_name, _prompt, payload, max_tokens):
            if agent_name == "infer":
                inferred = dict(responses["infer"])
                if "by currency" in payload["question"].lower():
                    inferred["group_by"] = ["CURRENCY_CODE"]
                return inferred
            return responses[agent_name]

        with patch.object(agentic_pipeline, "_call_agent", side_effect=fake_agent), patch.object(
            agentic_pipeline,
            "retrieve_units",
            return_value=[{"unit": self.portfolio_unit, "score": 0.9}],
        ):
            return agentic_pipeline.run_agentic_question(" ; ".join(questions), "test1234")

    def test_simple_question_reaches_duckdb(self):
        response = self._run(["What is the current portfolio balance?"])

        self.assertEqual(response["type"], "result")
        self.assertEqual(response["results"][0]["metric"], "portfel_balansi")
        self.assertAlmostEqual(response["rows"][0][0], 1040485.2)

    def test_multiple_subquestions_return_dashboard_tables(self):
        response = self._run(["Portfolio balance", "Portfolio balance by currency"])

        self.assertEqual(len(response["results"]), 2)
        self.assertTrue(response["dashboard"]["needed"])
        self.assertEqual(len(response["dashboard"]["components"]), 2)
        self.assertTrue(all(item["type"] == "table" for item in response["dashboard"]["components"]))

    def test_duplicate_semantic_queries_are_removed_before_synthesis(self):
        response = self._run(["Portfolio balance", "Portfolio balance"])

        self.assertEqual(len(response["results"]), 1)
        self.assertEqual(response["deduplicated_result_count"], 1)

    def test_all_executable_catalog_units_run_against_duckdb(self):
        import duckdb

        results = []
        with duckdb.connect(agentic_pipeline.DATABASE_PATH, read_only=True) as connection:
            for name, unit in load_metrics().items():
                if unit.get("operation") == "multi_unit":
                    continue
                query = {
                    "metric": name,
                    "filters": [],
                    "group_by": list(
                        (unit.get("parameters", {}).get("group_by") or {}).get("required", [])
                    ),
                }
                if (unit.get("parameters", {}).get("conditions") or {}).get("required"):
                    query["conditions"] = [
                        {"column": "interest_rate", "operator": ">=", "value": 10}
                    ]
                if unit.get("operation") == "compare":
                    query["period_a_filters"] = [
                        {"column": "bank_date", "operator": ">=", "value": "last_n_months:6"}
                    ]
                    query["period_b_filters"] = [
                        {"column": "bank_date", "operator": ">=", "value": "last_n_months:12"},
                        {"column": "bank_date", "operator": "<", "value": "last_n_months:6"},
                    ]
                if unit.get("operation") == "ranked_breakdown":
                    query["rank_direction"] = "desc"
                sql = compile_query(query)
                results.append(connection.execute(sql).fetchall())

        self.assertEqual(len(results), 25)

    def test_malformed_agent_json_is_repaired(self):
        result = agentic_pipeline._json_object(
            '{"summary":"Trend result","dashboard":{"needed":true "title":"Monthly trend"}}'
        )

        self.assertEqual(result["dashboard"]["title"], "Monthly trend")

    def test_empty_compact_filters_and_nested_date_object_are_normalized(self):
        unit = load_metrics()["portfel_hereketi"]
        model = load_models()["balance_movements"]
        semantic_query = domain_policy.enforce(
            {
                "metric": unit["name"],
                "filters": {
                    "product_code": [],
                    "type": [],
                    "bank_date": [
                        {
                            "column": "bank_date",
                            "operator": ">=",
                            "value": "last_n_days:30",
                        }
                    ],
                },
                "group_by": [],
            },
            unit,
            model,
        )

        self.assertEqual(
            semantic_query["filters"],
            [{"column": "bank_date", "operator": ">=", "value": "last_n_days:30"}],
        )
        sql = compile_query(semantic_query)
        self.assertNotIn("IN ()", sql)
        self.assertIn("last_n_days:30", repr(semantic_query))

    def test_empty_in_list_is_rejected_before_sql_execution(self):
        unit = load_metrics()["portfel_hereketi"]
        model = load_models()["balance_movements"]

        with self.assertRaisesRegex(ValueError, "IN but has no allowed values"):
            domain_policy.enforce(
                {
                    "metric": unit["name"],
                    "filters": [{"column": "type", "operator": "IN", "value": []}],
                    "group_by": [],
                },
                unit,
                model,
            )

    def test_compact_filter_map_is_normalized_and_allowlisted(self):
        semantic_query = {
            "metric": "portfel_balansi",
            "filters": {"CURRENCY_CODE": "USD"},
            "group_by": None,
        }

        agentic_pipeline._validate_inferred_query(semantic_query, self.portfolio_unit)

        self.assertEqual(
            semantic_query["filters"],
            [{"column": "CURRENCY_CODE", "operator": "=", "value": "USD"}],
        )
        self.assertEqual(semantic_query["group_by"], [])

    def test_inference_cannot_use_dimension_outside_unit_allowlist(self):
        semantic_query = {
            "metric": "portfel_balansi",
            "filters": [{"column": "term", "operator": "=", "value": 12}],
            "group_by": [],
        }

        with self.assertRaisesRegex(ValueError, "not allowed"):
            agentic_pipeline._validate_inferred_query(semantic_query, self.portfolio_unit)

    def test_default_period_is_applied_after_alias_normalization(self):
        unit = load_metrics()["satis_hecmi"]
        model = load_models()["sales"]
        semantic_query = domain_policy.enforce(
            {"metric": "satis_hecmi", "filters": [], "group_by": []},
            unit,
            model,
        )

        result = apply_default_period(semantic_query, unit, model)

        self.assertEqual(
            result["filters"],
            [{"column": "tarix", "operator": ">=", "value": "last_n_months:6"}],
        )

    def test_explicit_period_alias_suppresses_unit_default(self):
        unit = load_metrics()["satis_hecmi"]
        model = load_models()["sales"]
        semantic_query = domain_policy.enforce(
            {
                "metric": "satis_hecmi",
                "filters": [{"column": "bank_date", "operator": ">=", "value": "last_n_days:30"}],
                "group_by": [],
            },
            unit,
            model,
        )

        result = apply_default_period(semantic_query, unit, model)

        self.assertEqual(len(result["filters"]), 1)
        self.assertEqual(result["filters"][0]["column"], "tarix")
        self.assertEqual(result["filters"][0]["value"], "last_n_days:30")

    def test_required_group_by_is_added_from_unit_contract(self):
        unit = load_metrics()["axin_novu_strukturu"]
        model = load_models()["balance_movements"]

        result = domain_policy.enforce(
            {"metric": unit["name"], "filters": [], "group_by": []},
            unit,
            model,
        )

        self.assertEqual(result["group_by"], ["product_code", "type"])

    def test_multi_unit_executes_each_member_as_a_separate_result(self):
        composite_unit = next(
            item["unit"]
            for item in load_unit_documents()
            if item["unit"]["name"] == "satis_portfel_artim_muqayisesi"
        )

        def fake_agent(_request_id, agent_name, _prompt, payload, max_tokens):
            if agent_name == "question":
                return {
                    "is_complex": False,
                    "subquestions": [{"id": "q1", "question": "Compare sales and portfolio trends"}],
                }
            if agent_name == "reranker":
                return {
                    "selected_unit": "satis_portfel_artim_muqayisesi",
                    "no_match": False,
                    "reason": "The unit explicitly compares both trends",
                    "confidence": 0.99,
                }
            if agent_name == "synthesizer":
                return {
                    "summary": "Two series are ready",
                    "insights": [],
                    "dashboard": {"needed": False, "title": "Trend comparison", "components": []},
                }
            metric = payload["selected_unit"]["name"]
            return {"metric": metric, "filters": [], "group_by": []}

        with patch.object(agentic_pipeline, "_call_agent", side_effect=fake_agent), patch.object(
            agentic_pipeline,
            "retrieve_units",
            return_value=[{"unit": composite_unit, "score": 0.99}],
        ):
            result = agentic_pipeline.run_agentic_question("Compare both monthly trends", "multi1234")

        self.assertEqual(
            [item["metric"] for item in result["results"]],
            ["satis_ayliq_trend", "portfel_ayliq_trend"],
        )
        self.assertTrue(result["dashboard"]["needed"])
        self.assertEqual(len(result["dashboard"]["components"]), 2)

    def test_agent_io_logs_full_input_and_raw_and_parsed_outputs(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        agentic_pipeline.LOGGER.addHandler(handler)
        original_setting = os.environ.get("AGENT_IO_LOGGING")
        os.environ["AGENT_IO_LOGGING"] = "true"
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))]
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: fake_response)
            )
        )
        try:
            with patch.object(agentic_pipeline, "_client", return_value=fake_client):
                result = agentic_pipeline._call_agent(
                    "trace123",
                    "test_agent",
                    "Prompt azərbaycanca",
                    {"question": "salam"},
                    64,
                )
        finally:
            agentic_pipeline.LOGGER.removeHandler(handler)
            if original_setting is None:
                os.environ.pop("AGENT_IO_LOGGING", None)
            else:
                os.environ["AGENT_IO_LOGGING"] = original_setting

        logs = stream.getvalue()
        self.assertEqual(result, {"ok": True})
        self.assertIn("phase=agent.test_agent.io_input", logs)
        self.assertIn("Prompt az\\u0259rbaycanca", logs)
        self.assertIn("phase=agent.test_agent.io_output_raw", logs)
        self.assertIn("phase=agent.test_agent.io_output_parsed", logs)


if __name__ == "__main__":
    unittest.main()
