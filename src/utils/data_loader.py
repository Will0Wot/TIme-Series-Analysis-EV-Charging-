"""Data loading and preparation utilities."""
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Tuple, Optional
from src.config import DATA_PATH


class DataLoader:
    """Load and prepare data for analysis."""

    @staticmethod
    def load_csv(filename: str) -> pd.DataFrame:
        """Load CSV file from data directory."""
        filepath = Path(DATA_PATH) / filename
        if not filepath.exists():
            raise FileNotFoundError(f"File not found: {filepath}")
        return pd.read_csv(filepath)

    @staticmethod
    def split_train_test(df: pd.DataFrame, test_size: float = 0.2) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Split data into train and test sets."""
        split_idx = int(len(df) * (1 - test_size))
        return df[:split_idx], df[split_idx:]
