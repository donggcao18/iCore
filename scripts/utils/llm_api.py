import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from openai import OpenAI, APIError, RateLimitError

from scripts.config import API_KEY, BASE_URL

def create_chat_completion(client, **kwargs):
    """Retry transient API errors; buffer streams so failed chunks never escape."""
    client = client.with_options(max_retries=0)
    for attempt in range(5):
        try:
            response = client.chat.completions.create(**kwargs)
            if not kwargs.get('stream'):
                return response
            # Callers execute tools only after receiving this complete response.
            # A failed attempt's partial text/tool arguments are discarded.
            try:
                return list(response)
            finally:
                response.close()
        except APIError as e:
            status = getattr(e, 'status_code', None)
            message = str(e).lower()
            retryable = (
                isinstance(e, RateLimitError)
                or status == 429
                or (status is not None and 500 <= status < 600)
                or (status is None and any(text in message for text in (
                    'temporarily overloaded', 'temporarily unavailable',
                    'temporarily rate-limited',
                )))
            )
            if not retryable:
                raise
            if attempt == 4:
                print('Temporary API failure persists after 5 attempts.', flush=True)
                raise
            delay = min(30 * (2 ** attempt), 120)
            error_response = getattr(e, 'response', None)
            retry_after = error_response.headers.get('retry-after') if error_response is not None else None
            if retry_after:
                try:
                    server_delay = float(retry_after)
                except ValueError:
                    try:
                        server_delay = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError, OverflowError):
                        server_delay = 0
                delay = max(delay, server_delay)
            if delay > 300:
                print(f'Provider requires waiting {delay:.0f}s; stopping. Retry later.', flush=True)
                raise
            print(f'Temporary API failure; waiting {delay:.0f}s before retry {attempt + 1}/4.', flush=True)
            time.sleep(delay)


def query_chat_llm(prompt, model, temperature=0.7):
    if model not in API_KEY:
        raise ValueError(f'Unknown model {model}. Add its API key and base URL entries in scripts/config.py.')
    api_key = API_KEY.get(model, None)
    base_url = BASE_URL.get(model, None)
    if not api_key:
        raise ValueError(f'API key for model {model} is not set. Export the corresponding OPENAI_API_KEY, QWEN_API_KEY, GLM_API_KEY, or DEEPSEEK_API_KEY environment variable before running.')
    if model == 'gpt-4o' or model == 'gpt-4o-2024-08-06':
        client = OpenAI(api_key=api_key, base_url=base_url)
        model_name = 'gpt-4o-2024-08-06'
    elif model == 'qwen-32b':
        client = OpenAI(api_key=api_key, base_url=base_url)
        model_name = 'Qwen/Qwen3-32B'
        prompt[-1]['content'] = prompt[-1]['content'] + "/no_think"
    elif model == 'deepseek-v3-0324' or model == 'deepseek' or model == 'deepseek-chat':
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=300)
        model_name = 'deepseek-ai/DeepSeek-V3'
    else:
        # Configured provider model IDs are sent unchanged (including OpenRouter).
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=300)
        model_name = model
    try:
        response = create_chat_completion(
            client,
            model=model_name,
            messages=prompt,
            temperature=temperature,
            n=1,
            stream=False,
            top_p=0.8
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f'Error in chat completion: {e}')
        return None

def query_llm(prompt, model, temperature=0.7):
    return query_chat_llm(prompt, model, temperature=temperature)
