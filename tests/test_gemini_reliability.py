"""Offline regression coverage: no Gemini calls, MCP subprocesses or DB writes."""

import contextlib
import importlib
import io
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from google.genai import errors, types

from gemini_reliability import (
    GeminiQuotaError, QUOTA_MESSAGE, generate_content_with_retry,
)


def quota_error(delay=None, quota_id=None, value="5", headers=None):
    details = []
    if delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay})
    if quota_id is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": quota_id, "quotaValue": value}
        ]})
    return errors.ClientError(429, {"error": {
        "status": "RESOURCE_EXHAUSTED", "message": "RAW_PAYLOAD_MUST_NOT_REACH_UI",
        "details": details,
    }}, response=httpx.Response(429, headers=headers or {}))


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.sleep = patch("gemini_reliability.time.sleep").start()
        self.addCleanup(patch.stopall)

    def run_request(self, **kwargs):
        return generate_content_with_retry(self.client, model="gemini-3.6-flash", contents="test", **kwargs)

    def test_success_costs_one_call(self):
        result = object()
        self.client.models.generate_content.return_value = result
        self.assertIs(self.run_request(), result)
        self.client.models.generate_content.assert_called_once()
        self.sleep.assert_not_called()

    def test_retry_info_honored_and_only_request_retried(self):
        self.client.models.generate_content.side_effect = [quota_error("7.5s"), "real response"]
        self.assertEqual(self.run_request(), "real response")
        self.sleep.assert_called_once_with(7.5)
        self.assertEqual(self.client.models.generate_content.call_count, 2)
        calls = self.client.models.generate_content.call_args_list
        self.assertEqual(calls[0], calls[1])

    def test_retry_after_and_retry_info_use_longer_delay(self):
        self.client.models.generate_content.side_effect = [quota_error("4s", headers={"Retry-After": "9"}), "ok"]
        self.run_request()
        self.sleep.assert_called_once_with(9)

    def test_http_date_retry_after(self):
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20))
        self.client.models.generate_content.side_effect = [quota_error(headers={"Retry-After": future}), "ok"]
        self.run_request()
        self.assertGreater(self.sleep.call_args.args[0], 17)
        self.assertLessEqual(self.sleep.call_args.args[0], 20)

    def test_daily_or_zero_quota_never_retried_even_with_short_delay(self):
        for quota_id, value in [("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "20"),
                                ("GenerateRequestsPerMinute-FreeTier", "0")]:
            with self.subTest(quota_id=quota_id):
                self.client.reset_mock()
                self.client.models.generate_content.side_effect = quota_error("5s", quota_id, value)
                with self.assertRaisesRegex(GeminiQuotaError, "temporarily at its request limit"):
                    self.run_request()
                self.client.models.generate_content.assert_called_once()
                self.sleep.assert_not_called()

    def test_long_server_delay_is_not_shortened(self):
        self.client.models.generate_content.side_effect = quota_error("61s")
        with self.assertRaises(GeminiQuotaError):
            self.run_request()
        self.client.models.generate_content.assert_called_once()
        self.sleep.assert_not_called()

    def test_cumulative_wait_is_bounded(self):
        self.client.models.generate_content.side_effect = quota_error("40s")
        with self.assertRaises(GeminiQuotaError):
            self.run_request()
        self.assertEqual(self.client.models.generate_content.call_count, 2)
        self.sleep.assert_called_once_with(40)

    def test_missing_or_invalid_delay_uses_bounded_backoff(self):
        for delay in [None, "invalid", "NaNs", "-4s"]:
            with self.subTest(delay=delay):
                self.sleep.reset_mock()
                self.client.reset_mock()
                self.client.models.generate_content.side_effect = quota_error(delay)
                with self.assertRaises(GeminiQuotaError) as caught:
                    self.run_request()
                self.assertEqual(str(caught.exception), QUOTA_MESSAGE)
                self.assertEqual(self.client.models.generate_content.call_count, 3)
                self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [2, 4])

    def test_auth_and_programming_errors_not_retried(self):
        for exc in [errors.ClientError(403, {"message": "denied"}), TypeError("bad argument")]:
            with self.subTest(error=type(exc).__name__):
                self.client.reset_mock()
                self.client.models.generate_content.side_effect = exc
                with self.assertRaises(type(exc)):
                    self.run_request()
                self.client.models.generate_content.assert_called_once()
                self.sleep.assert_not_called()

    def test_transient_service_and_transport_failures_retry(self):
        for exc in [errors.ServerError(503, {"message": "busy"}), httpx.ReadTimeout("timeout")]:
            with self.subTest(error=type(exc).__name__):
                self.sleep.reset_mock()
                self.client.models.generate_content.side_effect = [exc, "ok"]
                self.assertEqual(self.run_request(), "ok")
                self.sleep.assert_called_once_with(2)

    def test_logs_keep_metadata_not_raw_payload_or_stdout(self):
        self.client.models.generate_content.side_effect = quota_error("5s", "RequestsPerDay", "20")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), self.assertLogs("gemini_reliability", level="WARNING") as logs:
            with self.assertRaises(GeminiQuotaError):
                self.run_request()
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("status=429", str(logs.output))
        self.assertIn("RequestsPerDay", str(logs.output))
        self.assertNotIn("RAW_PAYLOAD", str(logs.output))


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Never load .env or instantiate a live SDK client during test import.
        with patch("dotenv.load_dotenv"), patch("google.genai.Client"):
            cls.agent = importlib.import_module("agent")
            cls.ingestion = importlib.import_module("ingestion")

    def test_import_quota_failure_does_not_connect_or_write(self):
        client = MagicMock()
        client.models.generate_content.side_effect = quota_error("2s", "RequestsPerDay", "20")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "offline-test-only"}), \
             patch.object(self.ingestion.genai, "Client", return_value=client), \
             patch.object(self.ingestion, "get_clickhouse_client") as db:
            with self.assertRaises(GeminiQuotaError):
                self.ingestion.ingest_txt_screenplay("test screenplay")
            db.assert_not_called()

    def test_import_retry_writes_extracted_data_once_after_success(self):
        client, db = MagicMock(), MagicMock()
        extracted = self.ingestion.ScreenplayExtraction(
            production_title="Offline test", scenes=[{"scene_number": "1"}],
            character_states=[{"scene_number": "1", "character_name": "Test"}],
            props=[{"scene_number": "1", "prop_name": "Test prop"}],
        )
        client.models.generate_content.side_effect = [quota_error("3s"), SimpleNamespace(parsed=extracted)]
        db.query.return_value.result_rows = [(0,)]
        with patch.dict(os.environ, {"GEMINI_API_KEY": "offline-test-only"}), \
             patch.object(self.ingestion.genai, "Client", return_value=client), \
             patch.object(self.ingestion, "get_clickhouse_client", return_value=db), \
             patch("gemini_reliability.time.sleep"):
            result = self.ingestion.ingest_txt_screenplay("test screenplay")
        self.assertEqual(client.models.generate_content.call_count, 2)
        self.assertEqual(db.insert.call_count, 4)
        self.assertEqual(result["scene_count"], 1)
        for call in db.insert.call_args_list:
            self.assertIn(result["production_id"], call.args[1][0])

    def test_real_audit_code_uses_guardrail_and_mcp_boundary(self):
        sqls = [f"SELECT * FROM scenekeeper.{table} WHERE production_id = 'test_prod'"
                for table in ["scenes", "prop_tracker"]]
        tool_response = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(
            role="model", parts=[types.Part(function_call=types.FunctionCall(
                name="execute_clickhouse_query", args={"sql_query": sql})) for sql in sqls]
        ))])
        final = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(
            role="model", parts=[types.Part.from_text(text="Offline fixture report")]
        ))])
        client = MagicMock()
        client.models.generate_content.side_effect = [tool_response, quota_error("3s"), final]
        with patch.object(self.agent, "gemini_client", client), \
             patch.object(self.agent, "run_mcp_query", side_effect=["scene rows", "prop rows"]) as mcp, \
             patch("gemini_reliability.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            result = self.agent.query_agent("Audit entire production", "test_prod")
        self.assertEqual(result, "Offline fixture report")
        self.assertEqual(mcp.call_count, 2)  # Synthesis retry must not replay SQL.
        self.assertEqual([call.args[0] for call in mcp.call_args_list], sqls)
        self.assertEqual(client.models.generate_content.call_count, 3)
        last_contents = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("test_prod", last_contents[0].parts[0].text)
        self.assertEqual(len(last_contents[-2].parts), 2)

    def test_unscoped_query_still_blocked_before_mcp(self):
        with patch.object(self.agent, "run_mcp_query") as mcp:
            result = self.agent.execute_clickhouse_query("SELECT * FROM scenekeeper.scenes", "test_prod")
        self.assertIn("SQL Execution Blocked", result)
        mcp.assert_not_called()

    def test_streamlit_audit_quota_message(self):
        from streamlit.testing.v1 import AppTest
        with patch.object(self.agent, "query_agent", side_effect=GeminiQuotaError(QUOTA_MESSAGE)):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run()
            next(b for b in app.button if b.label == "Run Continuity Check").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.warning[0].value, QUOTA_MESSAGE)
        self.assertFalse(app.error)

    def test_streamlit_import_quota_keeps_active_production(self):
        from streamlit.testing.v1 import AppTest
        with patch.object(self.ingestion, "ingest_txt_screenplay", side_effect=GeminiQuotaError(QUOTA_MESSAGE)), \
             patch("streamlit.file_uploader", return_value=io.BytesIO(b"test screenplay")):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run()
            next(b for b in app.button if b.label == "Process & Import Screenplay").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.warning[0].value, QUOTA_MESSAGE)
        self.assertFalse(app.error)
        self.assertEqual(app.session_state["active_production_id"], "demo_sample")


if __name__ == "__main__":
    unittest.main()
