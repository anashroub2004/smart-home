"""The model used for every device (and presence): one place to build and fit it."""
from sklearn.ensemble import HistGradientBoostingClassifier

from . import settings as S


def new_model():
    return HistGradientBoostingClassifier(**S.MODEL_PARAMS)


def fit_model(X, y, sample_weight=None):
    """Fit one model. scikit-learn 1.9 can fail inside its automatic early stopping on this data
    ("window shape cannot be larger than input array shape", seen on Windows with 1.9.1): then train the same
    model without early stopping (all max_iter rounds) instead of leaving the device without a model."""
    try:
        return new_model().fit(X, y, sample_weight=sample_weight)
    except ValueError as e:
        if "window shape" not in str(e):
            raise
        params = dict(S.MODEL_PARAMS, early_stopping=False)
        return HistGradientBoostingClassifier(**params).fit(X, y, sample_weight=sample_weight)
