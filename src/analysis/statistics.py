"""Statistical analysis utilities."""
import pandas as pd
import numpy as np


class TimeSeriesAnalysis:
    """Perform statistical analysis on time series data."""

    @staticmethod
    def calculate_descriptive_stats(data: pd.Series) -> dict:
        """Calculate descriptive statistics."""
        return {
            "mean": float(data.mean()),
            "median": float(data.median()),
            "std": float(data.std()),
            "min": float(data.min()),
            "max": float(data.max()),
        }
