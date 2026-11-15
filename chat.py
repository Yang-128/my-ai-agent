import json
import base64
import binascii
import re
import traceback
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from storage import (
    get_conversation,
    save_message,
    record_usage,
    record_turn_usage,
    record_provider_request,
    get_tool_settings,
)
from tools import get_all_tool_definitions, execute_tool
from providers import get_provider_client, get_provider_model

router = APIRouter()


class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    model: str = ""
    provider: str = "groq"
    selected_tools: list[str] | None = None
    images: list[str] = []


def validate_images(images):
    if len(images) > 4:
        raise HTTPException(status_code=413, detail="Maximum 4 images per message.")

    validated = []
    total_size = 0
    signatures = {
        "image/png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
        "image/webp": lambda data: (
            len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        ),
    }

    for image in images:
        if not isinstance(image, str) or "," not in image:
            raise HTTPException(status_code=400, detail="Invalid image data.")

        header, encoded = image.split(",", 1)
        mime = header.removeprefix("data:").removesuffix(";base64")

        if header != f"data:{mime};base64" or mime not in signatures:
            raise HTTPException(
                status_code=415,
                detail="Only PNG, JPEG, and WEBP images are supported."
            )

        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(status_code=400, detail="Invalid image encoding.")

        if not raw or len(raw) > 6 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Each image must be 6 MB or smaller.")

        if not signatures[mime](raw):
            raise HTTPException(status_code=400, detail="Image content does not match its file type.")

        total_size += len(raw)
        if total_size > 16 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Combined image size exceeds 16 MB.")

        validated.append(image)

    return validated


def _int_header(headers, name):
    try:
        value = headers.get(name)
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def save_provider_headers(provider, model, headers):
    if provider == "groq":
        record_usage(
            provider,
            model,
            requests_limit=_int_header(headers, "x-ratelimit-limit-requests"),
            requests_remaining=_int_header(headers, "x-ratelimit-remaining-requests"),
            requests_reset=headers.get("x-ratelimit-reset-requests"),
            tokens_limit=_int_header(headers, "x-ratelimit-limit-tokens"),
        )


def ai_decide_extensions(message, provider, model, available_tools):
    """Ask the selected AI model which MCP extensions are genuinely needed."""
    extensions = []

    for tool in available_tools:
        definition = tool.get("function", {})
        name = definition.get("name", "")

        if not name.startswith("mcp_"):
            continue

        extensions.append({
            "name": name,
            "description": definition.get("description", ""),
            "parameters": definition.get("parameters", {}),
        })

    if not extensions:
        return {
            "needs_extensions": False,
            "required_extensions": [],
            "reason": "No extensions are available.",
        }

    client = get_provider_client(provider)

    system_prompt = (
        "You are an extension-selection component for a personal AI agent. "
        "Determine which available extensions are necessary to fulfill the "
        "user's request. Select only extensions that provide capabilities "
        "directly needed for the task. Do not select tools merely because "
        "they might be useful. If the request can be answered without an "
        "extension, select none. Never invent extension names. "
        "Return only valid JSON with this exact structure: "
        '{"needs_extensions": true, '
        '"required_extensions": ["exact_tool_name"], '
        '"reason": "brief explanation"}. '
        "When no extension is needed, return false and an empty list."
    )

    user_prompt = (
        "User request:\n"
        + message
        + "\n\nAvailable extensions:\n"
        + json.dumps(extensions, ensure_ascii=False)
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=700,
        temperature=0,
    )

    content = response.choices[0].message.content or ""
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())

    try:
        decision = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            raise ValueError("The model returned an invalid extension decision.")
        decision = json.loads(match.group(0))

    available_names = {item["name"] for item in extensions}
    requested_names = decision.get("required_extensions", [])

    if not isinstance(requested_names, list):
        requested_names = []

    required = list(dict.fromkeys(
        name for name in requested_names
        if isinstance(name, str) and name in available_names
    ))

    return {
        "needs_extensions": bool(required),
        "required_extensions": required,
        "reason": str(decision.get("reason", ""))[:500],
    }


class ExtensionDecisionRequest(BaseModel):
    message: str
    provider: str = "groq"
    model: str = ""


@router.post("/api/ai-decide-extensions")
def decide_extensions(request: ExtensionDecisionRequest):
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:
        model = request.model or get_provider_model(request.provider)
        available = get_all_tool_definitions()
        return ai_decide_extensions(
            message, request.provider, model, available
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Extension assessment failed: {error}"
        )


@router.post("/api/chat")
def chat(request: ChatRequest):
    conversation = get_conversation(request.conversation_id)

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    message = request.message.strip()
    images = validate_images(request.images)

    if not message and not images:
        raise HTTPException(status_code=400, detail="Message or image is required.")

    if images:
        from providers import get_available_models
        available = get_available_models(request.provider)
        model_info = next(
            (item for item in available
             if item["id"] == (request.model or get_provider_model(request.provider))),
            None,
        )
        if not model_info or not model_info.get("capabilities", {}).get("image"):
            raise HTTPException(
                status_code=400,
                detail="The selected model does not support image input. Please choose a vision-capable model.",
            )

    try:
        client = get_provider_client(request.provider)
        selected_model = request.model or get_provider_model(request.provider)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    save_message(
        request.conversation_id,
        "user",
        message,
        images=images,
    )
    updated = get_conversation(request.conversation_id)

    messages = []
    for item in updated["messages"]:
        content = item["content"]
        if item.get("images"):
            content = []
            if item["content"]:
                content.append({"type": "text", "text": item["content"]})
            content.extend(
                {
                    "type": "image_url",
                    "image_url": {"url": image},
                }
                for image in item["images"]
            )
        messages.append({"role": item["role"], "content": content})

    def generate():
        full_reply = ""
        turn_input_tokens = 0
        turn_output_tokens = 0
        has_turn_usage = False

        try:
            tool_settings = get_tool_settings()
            requested_tools = (
                set(request.selected_tools)
                if request.selected_tools is not None
                else None
            )
            available_tools = [
                tool for tool in get_all_tool_definitions()
                if tool_settings.get(tool["function"]["name"], True)
                and (
                    requested_tools is None
                    or tool["function"]["name"] in requested_tools
                )
            ]
            tool_calling_supported = bool(available_tools)

            system_message = {
                "role": "system",
                "content": (
                    "You are a helpful personal AI assistant. "
                    "Use available tools when useful. "
                    "You can use MCP tools to interact with connected "
                    "services and files. Explain what you did clearly. "
                    "You may use conversation history provided in the "
                    "messages to maintain context. Never claim to remember "
                    "conversations that are not included in the messages. "
                    "When asked about previous conversations, use "
                    "mcp_search_conversations to find relevant discussions, "
                    "then mcp_get_conversation_history to retrieve details. "
                    "Base claims about past discussions on retrieved results."
                ),
            }

            history_intent = re.search(
                r"\\b(previous|prior|earlier|past|last)\\s+"
                r"(conversation|chat|discussion)|"
                r"\\b(we|you)\\s+(discussed|talked about|mentioned)|"
                r"\\bwhat did we discuss\\b|\\bremember when\\b",
                message,
                re.I,
            )

            if history_intent:
                stop_words = {
                    "previous", "prior", "earlier", "past", "last",
                    "conversation", "conversations", "discussion",
                    "discussed", "talked", "about", "mentioned",
                    "what", "were", "the", "did", "have", "with",
                    "from", "that", "this", "when", "remember",
                    "can", "you", "find", "tell", "show", "recall",
                    "our", "your", "for", "and", "was", "how",
                    "much", "many", "which", "where", "who",
                }

                terms = re.findall(r"[A-Za-z0-9.-]+", message)
                search_terms = list(dict.fromkeys(
                    term.lower() for term in terms
                    if len(term) >= 3 and term.lower() not in stop_words
                ))
                search_terms.sort(key=len, reverse=True)

                conversation_ids = []
                search_outputs = []

                try:
                    for query in search_terms[:5]:
                        search_result = execute_tool(
                            "mcp_search_conversations",
                            {"query": query, "limit": 5},
                        )
                        result_text = str(search_result)

                        if result_text and "No matching" not in result_text:
                            search_outputs.append(result_text)

                            for conversation_id in re.findall(
                                r"Conversation ID:\\s*([^\\s]+)",
                                result_text,
                            ):
                                if conversation_id not in conversation_ids:
                                    conversation_ids.append(conversation_id)

                    if conversation_ids:
                        histories = []

                        for conversation_id in conversation_ids[:3]:
                            history = execute_tool(
                                "mcp_get_conversation_history",
                                {
                                    "conversation_id": conversation_id,
                                    "limit": 50,
                                },
                            )
                            if history and "Conversation not found" not in str(history):
                                histories.append(str(history))

                        if histories:
                            system_message["content"] += (
                                "\\n\\nFull previous conversation histories "
                                "retrieved for this request. Use these as evidence:\\n\\n"
                                + "\\n\\n---\\n\\n".join(histories)
                            )
                        else:
                            system_message["content"] += (
                                "\\n\\nMatching conversations were found, "
                                "but their histories could not be retrieved."
                            )
                    elif search_outputs:
                        system_message["content"] += (
                            "\\n\\nPrevious conversation search results "
                            "and excerpts:\\n\\n"
                            + "\\n\\n".join(search_outputs[:3])
                        )
                    else:
                        system_message["content"] += (
                            "\\n\\nNo matching previous conversation records "
                            "were found."
                        )

                except Exception as error:
                    print(f"Conversation retrieval warning: {error}")
                    system_message["content"] += (
                        "\\n\\nPrevious conversation retrieval failed."
                    )

            result = None
            max_tool_rounds = 5

            for round_number in range(max_tool_rounds + 1):
                request_args = {
                    "model": selected_model,
                    "messages": [system_message, *messages],
                    "max_tokens": 4096,
                }
                if tool_calling_supported:
                    request_args["tools"] = available_tools
                    request_args["tool_choice"] = "auto"

                try:
                    response = client.chat.completions.with_raw_response.create(
                        **request_args
                    )
                except Exception as error:
                    error_body = getattr(error, "body", None) or {}
                    error_message = str(error).lower()
                    if (
                        tool_calling_supported
                        and request.provider == "groq"
                        and (
                            "tool calling" in error_message
                            or "tool calling" in str(error_body).lower()
                        )
                    ):
                        tool_calling_supported = False
                        result = None
                        break
                    raise

                headers = response.headers
                completion = response.parse()

                save_provider_headers(request.provider, selected_model, headers)

                input_tokens = 0
                output_tokens = 0

                if completion.usage:
                    input_tokens = completion.usage.prompt_tokens or 0
                    output_tokens = completion.usage.completion_tokens or 0

                    turn_input_tokens += input_tokens
                    turn_output_tokens += output_tokens
                    has_turn_usage = True

                    record_usage(
                        request.provider,
                        selected_model,
                        tokens_used=input_tokens + output_tokens,
                    )

                record_provider_request(
                    request.provider,
                    selected_model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

                result = completion.choices[0].message
                tool_calls = result.tool_calls or []

                if not tool_calls:
                    break

                if round_number >= max_tool_rounds:
                    messages.append({
                        "role": "assistant",
                        "content": result.content,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.function.name,
                                    "arguments": call.function.arguments,
                                },
                            }
                            for call in tool_calls
                        ],
                    })

                    for call in tool_calls:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": "Tool-call limit reached. Answer using the information already retrieved.",
                        })

                    result = None
                    break

                messages.append({
                    "role": "assistant",
                    "content": result.content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in tool_calls
                    ],
                })

                for call in tool_calls:
                    try:
                        arguments = json.loads(call.function.arguments)
                        output = execute_tool(call.function.name, arguments)
                    except Exception as error:
                        output = f"Tool error: {error}"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": str(output),
                    })

            if result is None:
                response = client.chat.completions.with_raw_response.create(
                    model=selected_model,
                    messages=[system_message, *messages],
                    max_tokens=4096,
                )

                headers = response.headers
                completion = response.parse()

                save_provider_headers(request.provider, selected_model, headers)

                input_tokens = 0
                output_tokens = 0

                if completion.usage:
                    input_tokens = completion.usage.prompt_tokens or 0
                    output_tokens = completion.usage.completion_tokens or 0

                    turn_input_tokens += input_tokens
                    turn_output_tokens += output_tokens
                    has_turn_usage = True

                    record_usage(
                        request.provider,
                        selected_model,
                        tokens_used=input_tokens + output_tokens,
                    )

                record_provider_request(
                    request.provider,
                    selected_model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

                result = completion.choices[0].message

            full_reply = result.content or ""

            for index in range(0, len(full_reply), 40):
                yield full_reply[index:index + 40]

            if has_turn_usage:
                record_turn_usage(
                    request.provider,
                    selected_model,
                    turn_input_tokens,
                    turn_output_tokens,
                )

            if full_reply:
                save_message(
                    request.conversation_id,
                    "assistant",
                    full_reply,
                )

        except Exception as error:
            response = getattr(error, "response", None)
            status_code = getattr(error, "status_code", None)

            details = [
                f"Exception: {type(error).__name__}: {error}",
                f"Status: {status_code}",
                f"Response body: {response.text if response is not None else None}",
                f"Error body: {getattr(error, 'body', None)}",
                traceback.format_exc(),
                "=" * 60,
            ]

            with open(
                Path(__file__).resolve().parent / "api_debug.log",
                "a",
                encoding="utf-8",
            ) as log:
                log.write("\n".join(details) + "\n")

            if request.provider == "gemini" and status_code == 429:
                yield "\n\n[Gemini is experiencing too much traffic right now. Please try again shortly or switch to another model.]"
            elif request.provider == "gemini" and status_code in (503, 504):
                yield "\n\n[Gemini is temporarily overloaded. Please try again shortly or switch to another model.]"
            elif request.provider == "gemini" and type(error).__name__ == "APITimeoutError":
                yield "\n\n[Gemini is taking too long to respond, possibly due to heavy traffic. Please try again or switch models.]"
            elif request.provider == "gemini" and type(error).__name__ == "APIConnectionError":
                yield "\n\n[Could not connect to Gemini. Please check your connection and try again.]"
            else:
                yield f"\n\n[Error: {error}]"

    return StreamingResponse(
        generate(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
