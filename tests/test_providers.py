"""The assistant's model adapters, run through the real SDKs against a local fake API.

No test reaches a real provider or needs a real key. The SDK tests are
skipped when the optional ``quantarium[assistant]`` extra is not installed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from quantarium import agents
from quantarium.agents import AgentSettings
from quantarium.providers import ProviderError, read_key
from quantarium.providers_openai import _to_chat
from quantarium.server import QuantariumServer
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
HAS_ANTHROPIC = importlib.util.find_spec("anthropic") is not None
HAS_OPENAI = importlib.util.find_spec("openai") is not None
KEY = "sk-test-not-a-real-key-1234567890"


class FakeApi:
    """A loopback HTTP server that answers with queued JSON replies and records requests."""

    def __init__(self, replies: list[tuple[int, dict]]) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                self.answer(body)

            def do_GET(self) -> None:
                self.answer(None)

            def answer(self, body) -> None:
                outer.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                       "body": body})
                reply = outer.replies.pop(0) if outer.replies else (500, {"error": "no reply"})
                status, payload, headers = (reply + ({},))[:3]
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def anthropic_message(content: list[dict], stop: str) -> dict:
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 3}}


def openai_completion(message: dict, finish: str) -> dict:
    return {"id": "c1", "object": "chat.completion", "created": 0, "model": "test-model",
            "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", **message}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


class AssistantWithProvider(unittest.TestCase):
    """A real Quantarium server whose assistant talks to a provider adapter."""

    provider = "anthropic"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.server = QuantariumServer(Workspace(Path(self.temp.name)), port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.api = self.server.api
        AgentSettings(self.api.workspace).enable(agents.CONFIRMATION, agents.NOTICE_VERSION)
        self.connection = self.api.command("agent-connection-create", {
            "name": "Model", "kind": "direct",
            "provider": {"provider": self.provider, "model": "claude-opus-5-5" if self.provider == "anthropic"
                         else "test-model", "key_env": "QUANTARIUM_TEST_KEY"}})["connection"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def run_chat(self, make_provider) -> dict:
        self.api.assistant.provider_for = make_provider
        chat = self.api.command("chat-create", {"connection_id": self.connection["id"]})
        self.api.command("chat-send", {"id": chat["id"], "text": "What cells do I have?"})
        deadline = time.monotonic() + 30
        while self.api.assistant.busy(chat["id"]) and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.api.get(f"agents/chats/{chat['id']}", {})


@unittest.skipUnless(HAS_ANTHROPIC, "needs the anthropic SDK (quantarium[assistant])")
class AnthropicTests(AssistantWithProvider):
    def test_a_tool_turn_on_the_wire(self) -> None:
        import anthropic
        from quantarium.providers_anthropic import AnthropicProvider

        fake = FakeApi([
            (200, anthropic_message([{"type": "thinking", "thinking": "", "signature": "sig-1"},
                                     {"type": "tool_use", "id": "toolu_1", "name": "list_cells", "input": {}}],
                                    "tool_use")),
            (200, anthropic_message([{"type": "text", "text": "You have no cells yet."}], "end_turn")),
        ])
        try:
            client = anthropic.Anthropic(api_key=KEY, base_url=fake.url, max_retries=0)
            chat = self.run_chat(lambda connection: AnthropicProvider(connection, client=client))
        finally:
            fake.close()
        self.assertEqual(chat["status"], "idle", chat.get("error"))
        self.assertEqual(chat["messages"][-1]["content"][0]["text"], "You have no cells yet.")
        first, second = (request["body"] for request in fake.requests)
        headers = {key.lower(): value for key, value in fake.requests[0]["headers"].items()}
        self.assertEqual(headers["x-api-key"], KEY)
        self.assertIn("server-side-fallback-2026-07-01", headers["anthropic-beta"])
        self.assertEqual((first["model"], first["fallbacks"], first["output_config"], first["cache_control"]),
                         ("claude-opus-5-5", "default", {"effort": "high"}, {"type": "ephemeral"}))
        self.assertNotIn("thinking", first)
        self.assertIn("list_cells", [tool["name"] for tool in first["tools"]])
        # The thinking block goes back unchanged, then the tool result.
        self.assertEqual(second["messages"][1]["content"][0], {"type": "thinking", "thinking": "", "signature": "sig-1"})
        result = second["messages"][2]["content"][0]
        self.assertEqual((result["type"], result["tool_use_id"], json.loads(result["content"])), ("tool_result", "toolu_1", []))
        self.assertEqual(chat["usage"]["input_tokens"], 26)

    def test_errors_and_refusals_never_carry_the_key(self) -> None:
        import anthropic
        from quantarium.providers_anthropic import AnthropicProvider

        fake = FakeApi([(401, {"type": "error", "error": {"type": "authentication_error",
                                                          "message": f"invalid x-api-key {KEY}"}})])
        try:
            client = anthropic.Anthropic(api_key=KEY, base_url=fake.url, max_retries=0)
            with self.assertRaises(ProviderError) as caught:
                AnthropicProvider(self.connection, client=client).complete("s", [{"role": "user", "content": "hi"}], [])
        finally:
            fake.close()
        self.assertEqual(caught.exception.code, "provider_auth")
        self.assertNotIn(KEY, str(caught.exception))
        fake = FakeApi([(200, {**anthropic_message([], "refusal"),
                               "stop_details": {"type": "refusal", "category": "cyber", "explanation": "x"}})])
        try:
            client = anthropic.Anthropic(api_key=KEY, base_url=fake.url, max_retries=0)
            reply = AnthropicProvider(self.connection, client=client).complete("s", [{"role": "user", "content": "hi"}], [])
        finally:
            fake.close()
        self.assertEqual(reply["content"][-1]["text"], "The model declined to continue this request (cyber).")


@unittest.skipUnless(HAS_OPENAI, "needs the openai SDK (quantarium[assistant])")
class OpenAITests(AssistantWithProvider):
    provider = "openai"

    def test_a_tool_turn_on_the_wire(self) -> None:
        import openai
        from quantarium.providers_openai import OpenAIProvider

        fake = FakeApi([
            (200, openai_completion({"content": None, "tool_calls": [{"id": "call_1", "type": "function",
                                     "function": {"name": "list_cells", "arguments": "{}"}}]}, "tool_calls")),
            (200, openai_completion({"content": "None yet."}, "stop")),
        ])
        try:
            client = openai.OpenAI(api_key=KEY, base_url=fake.url + "/v1", max_retries=0)
            chat = self.run_chat(lambda connection: OpenAIProvider(connection, client=client))
        finally:
            fake.close()
        self.assertEqual(chat["status"], "idle", chat.get("error"))
        self.assertEqual(chat["messages"][-1]["content"][0]["text"], "None yet.")
        first, second = (request["body"] for request in fake.requests)
        self.assertEqual(fake.requests[0]["path"], "/v1/chat/completions")
        self.assertEqual(first["messages"][0]["role"], "system")
        self.assertIn("list_cells", [tool["function"]["name"] for tool in first["tools"]])
        self.assertEqual([message["role"] for message in second["messages"]], ["system", "user", "assistant", "tool"])
        self.assertEqual(second["messages"][3]["tool_call_id"], "call_1")


@unittest.skipUnless(HAS_OPENAI and HAS_ANTHROPIC, "needs the SDKs (quantarium[assistant])")
class EndpointTests(AssistantWithProvider):
    """Quantarium's own SDK clients, pointed at a connection's endpoint."""

    def connect(self, provider: dict) -> dict:
        return self.api.command("agent-connection-create", {
            "name": f"Endpoint {len(self.api.agents.connections())}", "kind": "direct", "provider": provider})["connection"]

    def send(self, connection: dict) -> dict:
        self.connection = connection
        with mock.patch.dict(os.environ, {"QUANTARIUM_TEST_KEY": KEY}):
            from quantarium import providers
            return self.run_chat(providers.provider_for)

    def test_any_openai_compatible_endpoint(self) -> None:
        fake = FakeApi([(200, openai_completion({"content": "Hello from a local model."}, "stop"))])
        try:
            chat = self.send(self.connect({"preset": "custom", "protocol": "openai-chat", "endpoint": fake.url + "/v1",
                                           "model": "local/m", "key_env": "QUANTARIUM_TEST_KEY"}))
        finally:
            fake.close()
        self.assertEqual(chat["status"], "idle", chat.get("error"))
        request = fake.requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual({key.lower(): value for key, value in request["headers"].items()}["authorization"],
                         f"Bearer {KEY}")
        # Only OpenAI itself gets the renamed limit; other servers read max_tokens.
        self.assertIn("max_tokens", request["body"])
        self.assertNotIn("max_completion_tokens", request["body"])

    def test_an_anthropic_compatible_gateway_gets_no_anthropic_extras(self) -> None:
        fake = FakeApi([(200, anthropic_message([{"type": "text", "text": "Hi."}], "end_turn"))])
        try:
            chat = self.send(self.connect({"preset": "custom", "protocol": "anthropic-messages", "endpoint": fake.url,
                                           "model": "gateway-model", "key_env": "QUANTARIUM_TEST_KEY"}))
        finally:
            fake.close()
        self.assertEqual(chat["status"], "idle", chat.get("error"))
        request = fake.requests[0]
        self.assertEqual(request["path"].split("?")[0], "/v1/messages")
        for field in ("fallbacks", "output_config", "cache_control"):
            self.assertNotIn(field, request["body"])

    def test_a_request_without_tools_sends_no_tool_list(self) -> None:
        from quantarium import providers

        fake = FakeApi([(200, openai_completion({"content": "{}"}, "stop")),
                        (200, anthropic_message([{"type": "text", "text": "{}"}], "end_turn"))])
        try:
            for provider in ({"preset": "custom", "protocol": "openai-chat", "endpoint": fake.url + "/v1",
                              "model": "m", "key_store": "none"},
                             {"preset": "custom", "protocol": "anthropic-messages", "endpoint": fake.url,
                              "model": "m", "key_store": "none"}):
                connection = self.connect(provider)
                providers.provider_for(connection).complete("s", [{"role": "user", "content": [
                    {"type": "text", "text": "hi"}]}], [], max_tokens=50)
        finally:
            fake.close()
        for request in fake.requests:
            self.assertNotIn("tools", request["body"])

    def test_a_local_server_without_a_key(self) -> None:
        fake = FakeApi([(200, openai_completion({"content": "ok"}, "stop"))])
        try:
            chat = self.send(self.connect({"preset": "ollama", "endpoint": fake.url + "/v1", "model": "qwen3",
                                           "key_store": "none"}))
        finally:
            fake.close()
        self.assertEqual(chat["status"], "idle", chat.get("error"))
        headers = {key.lower(): value for key, value in fake.requests[0]["headers"].items()}
        self.assertEqual(headers["authorization"], "Bearer no-key")

    def test_a_redirect_is_never_followed_with_the_key(self) -> None:
        elsewhere = FakeApi([(200, openai_completion({"content": "stolen"}, "stop"))])
        fake = FakeApi([(307, {}, {"Location": elsewhere.url + "/v1/chat/completions"})])
        try:
            chat = self.send(self.connect({"preset": "custom", "protocol": "openai-chat", "endpoint": fake.url + "/v1",
                                           "model": "m", "key_env": "QUANTARIUM_TEST_KEY"}))
        finally:
            fake.close()
            elsewhere.close()
        self.assertEqual(chat["status"], "error")
        self.assertIn("redirect", chat["error"])
        self.assertEqual(elsewhere.requests, [])

    def test_listing_models_and_testing_a_connection(self) -> None:
        connection = self.connect({"preset": "custom", "protocol": "openai-chat", "endpoint": "http://127.0.0.1:9/v1",
                                   "model": "m", "key_env": "QUANTARIUM_TEST_KEY"})
        model = {"object": "model", "created": 0, "owned_by": "x"}
        fake = FakeApi([
            (200, {"object": "list", "data": [{"id": "b", **model}, {"id": "a", **model}]}),
            (200, openai_completion({"content": None, "tool_calls": [{"id": "c", "type": "function",
                                     "function": {"name": "ping", "arguments": "{}"}}]}, "tool_calls")),
            (200, openai_completion({"content": "I can't call tools."}, "stop")),
        ])
        try:
            data = json.loads(self.api.agents.path.read_text(encoding="utf-8"))
            for item in data["connections"]:
                item["endpoint"] = fake.url + "/v1"
            self.api.agents.path.write_text(json.dumps(data), encoding="utf-8")
            with mock.patch.dict(os.environ, {"QUANTARIUM_TEST_KEY": KEY}):
                listed = self.api.command("agent-connection-models", {"id": connection["id"]})
                works = self.api.command("agent-connection-test", {"id": connection["id"]})
                fails = self.api.command("agent-connection-test", {"id": connection["id"]})
        finally:
            fake.close()
        self.assertEqual(listed, {"models": ["a", "b"]})
        self.assertEqual(fake.requests[0]["path"], "/v1/models")
        self.assertEqual((works["ok"], works["tool_calls"]), (True, True))
        self.assertEqual([tool["function"]["name"] for tool in fake.requests[1]["body"]["tools"]], ["ping"])
        self.assertFalse(fails["ok"])
        self.assertIn("tool calls", fails["message"])
        updated = self.api.command("agent-connection-model", {"id": connection["id"], "model": "a"})
        self.assertEqual(updated["model"], "a")


class ConversionAndKeyTests(unittest.TestCase):
    def test_neutral_blocks_become_chat_messages(self) -> None:
        chat = _to_chat("sys", [
            {"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "text", "text": "(page)"}]},
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "", "signature": "x"},
                                              {"type": "text", "text": "Looking"},
                                              {"type": "tool_use", "id": "t1", "name": "list_cells", "input": {"a": 1}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "[]", "is_error": False}]},
        ])
        self.assertEqual(chat, [
            {"role": "system", "content": "sys"}, {"role": "user", "content": "hi\n\n(page)"},
            {"role": "assistant", "content": "Looking", "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": "list_cells", "arguments": '{"a": 1}'}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "[]"}])

    def test_keys_come_from_the_environment_only_when_asked(self) -> None:
        connection = {"id": "a_1", "key_env": "QUANTARIUM_TEST_KEY"}
        with mock.patch.dict(os.environ, {"QUANTARIUM_TEST_KEY": KEY}):
            self.assertEqual(read_key(connection), KEY)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QUANTARIUM_TEST_KEY", None)
            with self.assertRaises(ProviderError) as caught:
                read_key(connection)
        self.assertEqual(caught.exception.code, "provider_key_missing")

    def test_connection_settings_are_validated(self) -> None:
        settings = AgentSettings.__new__(AgentSettings)
        for kind, provider in (("direct", None), ("direct", {"provider": "other", "model": "m", "key_env": "K"}),
                               ("direct", {"provider": "openai", "model": "m"}),
                               ("direct", {"provider": "openai", "model": "m", "key_env": "lower"}),
                               ("direct", {"provider": "openai", "model": "m", "key_env": "K", "key": KEY}),
                               ("bridge", {"bridge": "gemini"}), ("nope", None)):
            with self.subTest(kind=kind, provider=provider), self.assertRaises(ValueError):
                settings._provider_settings(kind, provider)
        # The v0.3.0 form still works and becomes a preset of the protocol.
        self.assertEqual(settings._provider_settings("direct", {"provider": "anthropic", "model": "claude-opus-5-5",
                                                                "key_store": "keyring"}),
                         {"preset": "anthropic", "protocol": "anthropic-messages",
                          "endpoint": "https://api.anthropic.com", "model": "claude-opus-5-5", "key_store": "keyring"})
        self.assertEqual(settings._provider_settings("direct", {
            "preset": "custom", "protocol": "openai-chat", "endpoint": "https://llm.example.org/v1/",
            "model": "any/model:7b", "key_env": "EXAMPLE_KEY"}),
            {"preset": "custom", "protocol": "openai-chat", "endpoint": "https://llm.example.org/v1",
             "model": "any/model:7b", "key_env": "EXAMPLE_KEY"})
        self.assertEqual(settings._provider_settings("direct", {"preset": "ollama", "model": "qwen3",
                                                                "key_store": "none"})["endpoint"],
                         "http://localhost:11434/v1")
        for provider in ({"preset": "custom", "model": "m", "key_env": "K"},  # no protocol
                         {"preset": "custom", "protocol": "grpc", "endpoint": "https://x.org", "model": "m", "key_env": "K"},
                         {"preset": "anthropic", "protocol": "openai-chat", "model": "m", "key_env": "K"},
                         {"preset": "anthropic", "model": "m", "key_store": "none"},  # needs a key
                         {"preset": "openai", "model": "m", "key_store": "vault"},
                         {"preset": "custom", "protocol": "openai-chat", "endpoint": "http://llm.example.org/v1",
                          "model": "m", "key_env": "K"},
                         {"preset": "openai", "model": "line\nbreak", "key_env": "K"},
                         {"preset": "openai", "model": "m", "key_env": "K", "headers": {"x": "y"}}):
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                settings._provider_settings("direct", provider)

    def test_agent_app_settings_need_their_acknowledgement(self) -> None:
        from quantarium import presets

        settings = AgentSettings.__new__(AgentSettings)
        self.assertEqual(settings._provider_settings("bridge", {"bridge": "codex"}),
                         {"runtime": "codex", "profile": "codex-exec"})
        claude = presets.runtime_preset("claude-code")
        with self.assertRaises(ValueError):
            settings._provider_settings("bridge", {"runtime": "claude-code"})
        with self.assertRaises(ValueError):
            settings._provider_settings("bridge", {"runtime": "claude-code", "acknowledgement": "yes"})
        accepted = settings._provider_settings("bridge", {"runtime": "claude-code",
                                                          "acknowledgement": claude["acknowledgement"]})
        self.assertEqual((accepted["runtime"], accepted["profile"]), ("claude-code", "claude-code"))
        self.assertTrue(accepted["acknowledged_utc"] and accepted["acknowledgement_sha256"])
        self.assertEqual(settings._provider_settings("bridge", {"runtime": "custom-acp", "command": ["opencode", "acp"]}),
                         {"runtime": "custom-acp", "profile": "acp", "command": ["opencode", "acp"]})
        for provider in ({"runtime": "custom-acp"}, {"runtime": "custom-acp", "command": "opencode acp"},
                         {"runtime": "custom-acp", "command": []}, {"runtime": "codex", "command": ["sh"]},
                         {"runtime": "codex", "acknowledgement": "x"}, {"runtime": "gemini"}):
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                settings._provider_settings("bridge", provider)

    def test_endpoints_must_keep_keys_safe(self) -> None:
        from quantarium.presets import check_endpoint

        for url in ("https://api.example.com/v1", "http://127.0.0.1:11434/v1", "http://localhost:1234/v1",
                    "http://[::1]:8000"):
            self.assertEqual(check_endpoint(url + "/"), url)
        for url in ("http://api.example.com/v1", "https://user:pass@api.example.com", "https://x.org/v1?key=1",
                    "https://x.org/#f", "ftp://x.org", "api.example.com", "https://", "https://x.org/ v1", None,
                    # Other *.localhost names may be resolved by a DNS server (v0.5.4).
                    "http://ollama.localhost/v1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                check_endpoint(url)

    def test_v030_connections_are_read_in_the_new_form(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            settings = AgentSettings(Workspace(Path(temp)))
            settings.enable(agents.CONFIRMATION, agents.NOTICE_VERSION)
            data = json.loads(settings.path.read_text(encoding="utf-8"))
            data["connections"] = [
                {"id": "a_1", "name": "Claude", "kind": "direct", "tiers": ["observe"], "provider": "anthropic",
                 "model": "claude-opus-5-5", "key_env": "ANTHROPIC_API_KEY", "created_utc": "2026-10-21T00:00:00+00:00",
                 "revoked_utc": None},
                {"id": "a_2", "name": "Codex", "kind": "bridge", "tiers": ["observe"], "bridge": "codex",
                 "created_utc": "2026-10-21T00:00:00+00:00", "revoked_utc": None}]
            settings.path.write_text(json.dumps(data), encoding="utf-8")
            by_id = {item["id"]: item for item in settings.connections()}
            self.assertEqual({key: by_id["a_1"].get(key) for key in ("preset", "protocol", "endpoint", "provider")},
                             {"preset": "anthropic", "protocol": "anthropic-messages",
                              "endpoint": "https://api.anthropic.com", "provider": None})
            self.assertEqual((by_id["a_2"]["runtime"], by_id["a_2"]["profile"]), ("codex", "codex-exec"))
            settings.set_model("a_1", "claude-sonnet-5-5")  # a write keeps the new form
            stored = json.loads(settings.path.read_text(encoding="utf-8"))["connections"]
            self.assertNotIn("provider", stored[0])
            self.assertEqual(stored[0]["model"], "claude-sonnet-5-5")


class PresetFileTests(unittest.TestCase):
    """Contributors add presets as data; this keeps a bad one from breaking the Agents page."""

    def test_presets_are_complete_and_safe(self) -> None:
        from quantarium import presets

        data = presets.load()
        self.assertEqual(data["version"], 1)
        for kind in ("api", "runtimes"):
            ids = [item["id"] for item in data[kind]]
            self.assertEqual(len(ids), len(set(ids)), kind)
        for item in data["api"]:
            with self.subTest(preset=item["id"]):
                self.assertTrue(set(item) >= {"id", "name", "protocol", "endpoint", "key", "key_env", "models",
                                              "discover", "billing", "docs"})
                self.assertIn(item["key"], {"required", "none", "optional"})
                if item["id"] == "custom":
                    self.assertIsNone(item["endpoint"])
                    continue
                self.assertIn(item["protocol"], presets.PROTOCOLS)
                self.assertEqual(presets.check_endpoint(item["endpoint"]), item["endpoint"])
                if item["endpoint"].startswith("http://"):
                    self.assertEqual(item["key"], "none")  # plain HTTP only for local servers without keys
                self.assertTrue(item["docs"].startswith("https://"))
                self.assertTrue(item["key"] == "none" or agents._ENV_NAME.fullmatch(item["key_env"]))
                self.assertTrue(all(isinstance(model, str) for model in item["models"]))
        for item in data["runtimes"]:
            with self.subTest(preset=item["id"]):
                self.assertTrue(set(item) >= {"id", "name", "profile", "command", "experimental", "subscription",
                                              "billing", "conversations", "terms", "install", "docs",
                                              "acknowledgement"})
                self.assertIn(item["profile"], presets.PROFILES)
                self.assertTrue(item["docs"].startswith("https://"))
                if item["command"] is not None:
                    self.assertTrue(item["command"] and all(isinstance(part, str) for part in item["command"]))
                # A preset may set the app's own settings, never a secret or Quantarium's own variables.
                for name, value in (item.get("env") or {}).items():
                    self.assertTrue(agents._ENV_NAME.fullmatch(name) and isinstance(value, str), name)
                    self.assertFalse(name.startswith(("QUANTARIUM_", "APCA_")) or name.endswith(("_KEY", "_TOKEN", "_SECRET")), name)
                # An experimental subscription path needs the user's explicit acknowledgement.
                if item["subscription"] and item["experimental"]:
                    self.assertTrue(item["acknowledgement"])


if __name__ == "__main__":
    unittest.main()
