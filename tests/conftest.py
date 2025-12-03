"""Test configuration and fixtures."""
import pytest
import pandas as pd
import numpy as np


@pytest.fixture
def sample_timeseries_data():
    """Create sample time series data."""
    dates = pd.date_range(start='2025-01-01', periods=100, freq='H')
    values = np.random.randn(100) + 50
    return pd.DataFrame({"time": dates, "power_kw": values})
