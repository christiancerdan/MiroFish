#!/usr/bin/env python3
"""Score separate input, outcome, frozen protocol and prediction JSON artifacts."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _nonfinite_constant(value):
    raise ValueError(f"Nonfinite JSON number: {value}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inputs", "outcomes", "protocol", "predictions"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    module_path = Path(__file__).resolve().parents[1] / "app" / "services" / "comparative_benchmark.py"
    spec = importlib.util.spec_from_file_location("comparative_benchmark", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        input_bytes = args.inputs.read_bytes()
        values = [json.loads(content, object_pairs_hook=_unique_object, parse_constant=_nonfinite_constant)
                  for content in (input_bytes, args.outcomes.read_bytes(), args.protocol.read_bytes(), args.predictions.read_bytes())]
        result = module.evaluate_benchmark(*values, inputs_file_sha256=hashlib.sha256(input_bytes).hexdigest())
        rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except (ValueError, OSError, TypeError) as error:
        sys.stderr.write(json.dumps({"success": False, "error": str(error)}) + "\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
