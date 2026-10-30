"""Content identities for the exact local artifacts loaded by the worker."""

import hashlib
import importlib.metadata
import json
import re
import sys
import uuid
from pathlib import Path
from types import CodeType


class IdentityUnavailable(Exception):
    """The running model's immutable content identity cannot be established."""


def _digest_tree(path: Path) -> str:
    """Hash names and bytes, not mutable paths, timestamps or model labels."""
    if not path.is_dir():
        raise IdentityUnavailable("model artifacts unavailable")
    digest = hashlib.sha256()
    try:
        files = []
        for entry in path.rglob("*"):
            # Hugging Face snapshots normally symlink individual files to blob
            # objects.  rglob does not traverse linked directories; accepting
            # one would omit content the model loader could actually consume.
            if entry.is_symlink() and not entry.is_file():
                raise IdentityUnavailable("unsupported model artifact link")
            if entry.is_file():
                files.append(entry)
            elif not entry.is_dir():
                raise IdentityUnavailable("unsupported model artifact type")
        if not files:
            raise IdentityUnavailable("model artifacts unavailable")
        files.sort()
        for file in files:
            relative = file.relative_to(path).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(file.stat().st_size.to_bytes(8, "big"))
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    except OSError:
        raise IdentityUnavailable("model artifacts unavailable") from None
    return digest.hexdigest()


def artifact_fingerprints(model_path: Path, adapter_path: Path | None) -> tuple[str, str | None]:
    return _digest_tree(model_path), _digest_tree(adapter_path) if adapter_path else None


def _hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _code_value(value: object) -> object:
    """Represent executable constants deterministically without file paths."""
    if isinstance(value, CodeType):
        return {
            "bytecode": value.co_code.hex(),
            "exceptiontable": value.co_exceptiontable.hex(),
            "consts": [_code_value(item) for item in value.co_consts],
            "names": value.co_names,
            "varnames": value.co_varnames,
            "freevars": value.co_freevars,
            "cellvars": value.co_cellvars,
            "args": [value.co_argcount, value.co_posonlyargcount, value.co_kwonlyargcount],
            "flags": value.co_flags,
        }
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return {"float": value.hex()}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, tuple):
        return {"tuple": [_code_value(item) for item in value]}
    if isinstance(value, frozenset):
        items = [_code_value(item) for item in value]
        return {"frozenset": sorted(items, key=lambda item: json.dumps(item, sort_keys=True))}
    raise IdentityUnavailable("encoding implementation cannot be identified")


def implementation_fingerprint(functions: dict[str, object]) -> str:
    """Hash the code actually loaded into memory, excluding filenames and line tables."""
    if not functions:
        raise IdentityUnavailable("encoding implementation cannot be identified")
    result = {}
    for name, function in sorted(functions.items()):
        function = getattr(function, "__wrapped__", function)
        code = getattr(function, "__code__", None)
        if not isinstance(code, CodeType):
            raise IdentityUnavailable("encoding implementation cannot be identified")
        result[name] = {
            "code": _code_value(code),
            "defaults": _code_value(getattr(function, "__defaults__", None)),
            "kwdefaults": {
                key: _code_value(value)
                for key, value in (getattr(function, "__kwdefaults__", None) or {}).items()
            },
        }
    return _hash({"python": list(sys.version_info[:3]), "functions": result})


def make_identity(artifacts: tuple[str, str | None], text_instruction: str, implementation_id: str) -> dict[str, str]:
    if not isinstance(implementation_id, str) or re.fullmatch(r"[0-9a-f]{64}", implementation_id) is None:
        raise IdentityUnavailable("encoding implementation cannot be identified")
    try:
        versions = {name: importlib.metadata.version(name) for name in (
            "sentence-transformers", "transformers", "torch", "pillow", "peft",
            "tokenizers", "numpy", "safetensors", "torchvision", "huggingface-hub", "accelerate"
        )}
    except importlib.metadata.PackageNotFoundError:
        raise IdentityUnavailable("encoding dependencies unavailable") from None
    space_id = _hash({"schema": 2, "model": artifacts[0], "adapter": artifacts[1],
                      "implementation": implementation_id, "normalize": True,
                      "image_mode": "RGB", "dependencies": versions})
    protocol_id = _hash({"schema": 1, "space_id": space_id, "text_instruction": text_instruction})
    return {"space_id": space_id, "protocol_id": protocol_id, "generation": uuid.uuid4().hex}


def valid_identity(value: object) -> bool:
    return (isinstance(value, dict) and set(value) == {"space_id", "protocol_id", "generation"}
            and all(isinstance(value.get(k), str) and re.fullmatch(pattern, value[k]) is not None
                    for k, pattern in (("space_id", r"[0-9a-f]{64}"),
                                       ("protocol_id", r"[0-9a-f]{64}"),
                                       ("generation", r"[0-9a-f]{32}"))))
