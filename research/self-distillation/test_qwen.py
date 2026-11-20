"""Run a single Qwen inference directly on a GPU server with PyTorch."""

from local_inference import DEFAULT_LOCAL_MODEL, load_local_completion


def main():
    try:
        complete = load_local_completion(DEFAULT_LOCAL_MODEL, temperature=0.2, max_tokens=256)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    messages = [
        {"role": "user", "content": "Write a Python function that adds two numbers."},
    ]
    content, _ = complete(messages)
    print(content)


if __name__ == "__main__":
    main()
