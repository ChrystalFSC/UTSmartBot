"""Google Gemini provider (google-genai SDK)."""
from google import genai
from google.genai import types

from .base import MAX_OUTPUT_TOKENS, MAX_TOOL_ROUNDS, STOPPED_TEXT, TEMPERATURE, call_tool

SAFETY_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)


class GeminiProvider:
    provider = "gemini"

    def __init__(self, model, api_key, system, tools, client=None):
        self.model = model
        self.client = client or genai.Client(
            api_key=api_key,
            # Fail fast and let the router try the next provider instead of retrying here.
            http_options=types.HttpOptions(timeout=30_000, retry_options=types.HttpRetryOptions(attempts=1)),
        )
        self.config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t["name"], description=t["description"],
                                          parameters_json_schema=t["parameters"])
                for t in tools
            ])],
            temperature=TEMPERATURE,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            safety_settings=[types.SafetySetting(category=c, threshold="BLOCK_MEDIUM_AND_ABOVE")
                             for c in SAFETY_CATEGORIES],
        )
        # Last round: no more tool calls, answer from the results gathered so far.
        self.final_config = self.config.model_copy(update={"tools": None})

    def run(self, history, prompt, last_resort=False):  # Gemini 429s are daily quotas; never wait
        contents = [types.Content(role=t["role"], parts=[types.Part(text=t["text"])]) for t in history]
        contents.append(types.Content(role="user", parts=[types.Part(text=prompt)]))

        for round_ in range(MAX_TOOL_ROUNDS + 1):
            calls, model_parts = [], []
            config = self.final_config if round_ == MAX_TOOL_ROUNDS else self.config
            stream = self.client.models.generate_content_stream(model=self.model, contents=contents, config=config)
            for chunk in stream:
                candidate = chunk.candidates[0] if chunk.candidates else None
                for part in (candidate.content.parts or []) if candidate and candidate.content else []:
                    # Keep every part as-is: function calls carry thought signatures the API needs back.
                    model_parts.append(part)
                    if part.function_call:
                        calls.append(part.function_call)
                    elif part.text and not part.thought:
                        yield {"type": "text", "text": part.text}
            if not calls:
                return

            contents.append(types.Content(role="model", parts=model_parts))
            responses = []
            for call in calls:
                event, result = call_tool(call.name, dict(call.args or {}))
                yield event
                responses.append(types.Part.from_function_response(name=call.name, response={"result": result}))
            contents.append(types.Content(role="user", parts=responses))
        yield {"type": "text", "text": STOPPED_TEXT}
