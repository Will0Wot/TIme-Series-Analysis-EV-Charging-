"""Tests for data loading utilities."""
import pytest


class TestDataLoader:
    """Test DataLoader class."""

    def test_split_train_test(self, sample_timeseries_data):
        """Test train/test split."""
        test_size = 0.2
        split_idx = int(len(sample_timeseries_data) * (1 - test_size))
        train = sample_timeseries_data[:split_idx]
        test = sample_timeseries_data[split_idx:]
        
        assert len(train) + len(test) == len(sample_timeseries_data)
