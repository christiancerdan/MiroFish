#!/usr/bin/env python3
"""Evaluate dated binary forecasts without importing the Flask application."""
import argparse
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
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    module_path = Path(__file__).resolve().parents[1] / "app" / "services" / "forecast_evaluation.py"
    spec = importlib.util.spec_from_file_location("forecast_evaluation", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        dataset = json.loads(args.dataset.read_text(encoding="utf-8"), object_pairs_hook=_unique_object, parse_constant=_nonfinite_constant)
        result = module.evaluate_forecasts(dataset, bins=args.bins, bootstrap_samples=args.bootstrap_samples, seed=args.seed)
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
