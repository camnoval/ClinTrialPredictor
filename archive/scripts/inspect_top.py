#!/usr/bin/env python3
"""Print the real TOP CSV schema so you don't guess column names."""
import sys
from pathlib import Path


def main(path_str):
    path = Path(path_str)
    if not path.exists():
        print(f"not found: {path}")
        return 1
    import pandas as pd
    df = pd.read_csv(path)
    print(f"{path}: {df.shape[0]:,} rows x {df.shape[1]} cols\n")
    for col in df.columns:
        print(f"  {col:<24} {str(df[col].dtype):<10} non-null={df[col].notna().sum():<8} unique={df[col].nunique()}")
    print("\nfirst 3 rows:")
    with pd.option_context("display.max_columns", None, "display.width", 160):
        print(df.head(3).to_string())
    for c in ("label", "outcome", "y"):
        if c in df.columns:
            print(f"\n{c} balance:\n{df[c].value_counts(dropna=False).to_string()}")
            break
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python scripts/inspect_top.py <csv>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
