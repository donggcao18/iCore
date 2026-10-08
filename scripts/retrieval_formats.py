"""Generator-facing schemas shared with the original retrieval outputs."""

CODE_FIELDS = (
    "obj_name", "node_type", "path", "code_start_line", "code_end_line",
    "code_content", "parent",
)
TEST_FIELDS = ("name", "file", "code_content")


def split_code_documents(documents: dict) -> tuple[dict, dict]:
    """Separate original-format code objects from oracle-only annotations."""
    if not isinstance(documents, dict):
        raise ValueError("Code retrieval must map object identifiers to documents")
    output, metadata = {}, {}
    for identifier, document in documents.items():
        if document is None:
            output[identifier] = None  # Original retrieval supports misses.
            continue
        if not isinstance(document, dict) or any(field not in document for field in CODE_FIELDS):
            raise ValueError(f"Code document lacks original retrieval fields: {identifier}")
        if (any(not isinstance(document[field], str) for field in ("obj_name", "node_type", "path", "code_content"))
                or any(type(document[field]) is not int for field in ("code_start_line", "code_end_line"))
                or document["parent"] is not None and not isinstance(document["parent"], str)):
            raise ValueError(f"Code document has invalid original retrieval field types: {identifier}")
        output[identifier] = {field: document[field] for field in CODE_FIELDS}
        extra = {key: value for key, value in document.items() if key not in CODE_FIELDS}
        if extra:
            metadata[identifier] = extra
    return output, metadata


def test_retrieval(instances: dict) -> dict:
    """Export exactly {instance_id: [{name, file, code_content}, ...]}."""
    if not isinstance(instances, dict):
        raise ValueError("Test retrieval must map instance identifiers to lists")
    output = {}
    for instance_id, tests in instances.items():
        if not isinstance(tests, list):
            raise ValueError(f"Test retrieval entry must be a list: {instance_id}")
        output[instance_id] = []
        for test in tests:
            if not isinstance(test, dict) or any(
                    not isinstance(test.get(field), str) or not test[field].strip() for field in TEST_FIELDS):
                raise ValueError(f"Test document lacks original retrieval fields: {instance_id}")
            output[instance_id].append({field: test[field] for field in TEST_FIELDS})
    return output
