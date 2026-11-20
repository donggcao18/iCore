"""Run a single Qwen inference directly on a GPU server with vLLM."""


def main():
    from vllm import LLM, SamplingParams

    llm = LLM(
        model="Qwen/Qwen2.5-Coder-7B-Instruct",
        dtype="float16",
        attention_config={"backend": "TRITON_ATTN"},
        max_model_len=8192,
    )
    messages = [
        {"role": "user", "content": "Write a Python function that adds two numbers."},
    ]
    outputs = llm.chat(messages, SamplingParams(temperature=0.2, max_tokens=256))
    print(outputs[0].outputs[0].text)


if __name__ == "__main__":
    main()
