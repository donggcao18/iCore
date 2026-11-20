"""Direct GPU inference for the teacher and its smoke test."""


DEFAULT_LOCAL_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"


def load_local_completion(model_id, *, temperature, max_tokens):
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise ValueError("Local inference requires CUDA-enabled PyTorch and Transformers.") from exc

    if not torch.cuda.is_available():
        raise ValueError("PyTorch cannot access a CUDA GPU. Check nvidia-smi and the GPUs assigned to this process.")

    device = "cuda:0"
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(model_id, dtype=torch.float16).to(device)
    generation_options = {"max_new_tokens": max_tokens, "do_sample": temperature > 0}
    if temperature > 0:
        generation_options["temperature"] = temperature

    def complete(messages):
        inputs = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors="pt",
        ).to(device)
        with torch.inference_mode():
            output = model.generate(**inputs, **generation_options)
        prompt_tokens = inputs["input_ids"].shape[-1]
        generated = output[0][prompt_tokens:]
        completion_tokens = len(generated)
        content = tokenizer.decode(generated, skip_special_tokens=True)
        return content, {"finish_reason": "length" if completion_tokens == max_tokens else "stop",
                         "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                         "total_tokens": prompt_tokens + completion_tokens}

    return complete
