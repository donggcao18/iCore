"""Run a single Qwen inference directly on a GPU server with PyTorch."""


def main():
    import torch

    if not torch.cuda.is_available():
        raise SystemExit("PyTorch cannot access a CUDA GPU. Check nvidia-smi and the GPUs assigned to this process.")

    from transformers import AutoModelForCausalLM, AutoTokenizer

    model_id = "Qwen/Qwen2.5-Coder-7B-Instruct"
    device = "cuda:0"
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(model_id, dtype=torch.float16).to(device)
    messages = [
        {"role": "user", "content": "Write a Python function that adds two numbers."},
    ]
    inputs = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors="pt",
    ).to(device)
    with torch.inference_mode():
        output = model.generate(**inputs, max_new_tokens=256, do_sample=True, temperature=0.2)
    prompt_length = inputs["input_ids"].shape[-1]
    print(tokenizer.decode(output[0][prompt_length:], skip_special_tokens=True))


if __name__ == "__main__":
    main()
