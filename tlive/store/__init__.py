"""Run storage: every swap to Parquet, flagged swaps and sandwiches to DuckDB. Nothing is deleted."""
from .writer import Store

__all__ = ["Store"]
