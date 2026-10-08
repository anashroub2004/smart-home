"""scikit-learn 1.9.1 (Windows) failed inside its automatic early stopping: the model must still train."""
import unittest
from unittest import mock

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from ai import fit

REAL_FIT = HistGradientBoostingClassifier.fit


def broken_fit(self, X, y, sample_weight=None):
    if self.early_stopping is not False:
        raise ValueError("window shape cannot be larger than input array shape")
    return REAL_FIT(self, X, y, sample_weight=sample_weight)


class Fallback(unittest.TestCase):
    def test_trains_without_early_stopping(self):
        rng = np.random.default_rng(0)
        X = rng.normal(size=(300, 3))
        y = (X[:, 0] > 0).astype(int)
        with mock.patch.object(HistGradientBoostingClassifier, "fit", broken_fit):
            m = fit.fit_model(X, y, sample_weight=np.ones(300))
        self.assertFalse(m.early_stopping)
        self.assertGreater((m.predict(X) == y).mean(), 0.9)

    def test_other_errors_are_not_hidden(self):
        with self.assertRaises(ValueError):
            fit.fit_model(np.zeros((5, 2)), np.zeros(4))


if __name__ == "__main__":
    unittest.main()
