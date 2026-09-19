from openai import OpenAI

from scripts.config import API_KEY, BASE_URL

def query_chat_llm(prompt, model, temperature=0.7):
    # assert model in AVAILABLE_MODEL_INFO, f'Unknown model {model}'
    api_key = API_KEY.get(model, None)
    base_url = BASE_URL.get(model, None)
    if api_key is None:
        raise ValueError(f'API key for model {model} is not set. Please set it in API_KEY dictionary.')
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
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=prompt,
            temperature=temperature,
            n=1,
            stream=False,
            top_p=0.8
        )
        gen_result = response.choices[0].message.content
    except Exception as e:
        print(f'Error in chat completion: {e}')
        return None
        
    return gen_result

def query_llm(prompt, model, temperature=0.7):
    return query_chat_llm(prompt, model, temperature=temperature)
