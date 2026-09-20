import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from openai import OpenAI, RateLimitError

from scripts.config import API_KEY, BASE_URL

def query_chat_llm(prompt, model, temperature=0.7):
    # assert model in AVAILABLE_MODEL_INFO, f'Unknown model {model}'
    api_key = API_KEY.get(model, None)
    base_url = BASE_URL.get(model, None)
    if not api_key:
        raise ValueError(f'API key for model {model} is not set. Export the corresponding OPENAI_API_KEY, QWEN_API_KEY, GLM_API_KEY, or DEEPSEEK_API_KEY environment variable before running.')
    if model == 'gpt-4o' or model == 'gpt-4o-2024-08-06':
        client = OpenAI(api_key=api_key, base_url=base_url)
        model_name = 'gpt-4o-2024-08-06'
    elif model in ('qwen/qwen3.8-27b:free', 'z-ai/glm-5.2:free'):
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=300)
        model_name = model
    elif model == 'qwen-32b':
        client = OpenAI(api_key=api_key, base_url=base_url)
        model_name = 'Qwen/Qwen3-32B'
        prompt[-1]['content'] = prompt[-1]['content'] + "/no_think"
    elif model == 'deepseek-v3-0324' or model == 'deepseek' or model == 'deepseek-chat':
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=300)
        model_name = 'deepseek-ai/DeepSeek-V3'
    # Handle rate limits here instead of stacking the SDK's short retries.
    client = client.with_options(max_retries=0)
    for attempt in range(5):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=prompt,
                temperature=temperature,
                n=1,
                stream=False,
                top_p=0.8
            )
            return response.choices[0].message.content
        except RateLimitError as e:
            if attempt == 4:
                print(f'Rate limit persists after 5 attempts: {e}', flush=True)
                return None
            delay = min(30 * (2 ** attempt), 120)
            retry_after = e.response.headers.get('retry-after')
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
                print(f'Rate limit requires waiting {delay:.0f}s; stopping. Retry later.', flush=True)
                return None
            print(f'Rate limited; waiting {delay:.0f}s before retry {attempt + 1}/4.', flush=True)
            time.sleep(delay)
        except Exception as e:
            print(f'Error in chat completion: {e}')
            return None

def query_llm(prompt, model, temperature=0.7):
    return query_chat_llm(prompt, model, temperature=temperature)
