import json
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

import providers


class ProviderCompatibilityTests(unittest.TestCase):
    def test_configured_providers_can_stream_text_and_tool_calls(self):
        for provider, config in providers.PROVIDERS.items():
            with self.subTest(provider=provider):
                self.check_stream(provider, config)

    def check_stream(self, provider, config):
        model = "test-chat-model"
        tool = {"type": "function", "function": {"name": "get_time"}}
        deltas = [
            {"content": "Checking"},
            {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                             "function": {"name": "get_time", "arguments": "{}"}}]},
        ]

        def respond(request):
            self.assertEqual(str(request.url), config["base_url"].rstrip("/") + "/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer test-provider-key")
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], model)
            self.assertEqual(payload["tools"], [tool])
            self.assertTrue(payload["stream"])
            events = [
                {"id": "response", "object": "chat.completion.chunk", "created": 1,
                 "model": model, "choices": [{"index": 0, "delta": delta}]}
                for delta in deltas
            ]
            stream = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
            return httpx.Response(200, text=stream + "data: [DONE]\n\n",
                                  headers={"content-type": "text/event-stream"})

        with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
            def sdk_client(**kwargs):
                original = kwargs.pop("http_client", None)
                if original:
                    original.close()
                return OpenAI(http_client=transport, **kwargs)

            with patch.object(providers, "OpenAI", side_effect=sdk_client), patch.object(
                providers, "provider_key", return_value="test-provider-key"
            ), patch.object(providers, "provider_enabled", return_value=True):
                client = providers.get_provider_client(provider)
                with client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": "What time is it?"}],
                    tools=[tool], stream=True,
                ) as stream:
                    chunks = list(stream)
            self.assertEqual(chunks[0].choices[0].delta.content, "Checking")
            call = chunks[1].choices[0].delta.tool_calls[0]
            self.assertEqual((call.function.name, call.function.arguments), ("get_time", "{}"))
