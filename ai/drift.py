"""Routine-change detection.

The test period (last 14 days) is split in two halves. If the model's F1 on the most recent 7 days is
clearly lower (0.15+) than on the 7 days before, the routine probably changed (summer holiday, new work
hours, Ramadan ...). Then the device is marked "relearning": its act threshold goes up by 0.10 until the
next nightly training no longer sees the drop — the AI suggests more and acts less while it re-learns.
The time-decay weights in training make the new routine dominate within days.
"""
import numpy as np
from sklearn.metrics import f1_score

from . import settings as S


def detect(index, y_true, y_pred, min_positives=14):
    """index: timestamps of the test rows. -> dict(drift, f1_recent, f1_before)."""
    idx = np.asarray(index)
    if len(idx) == 0:
        return dict(drift=False, f1_recent=None, f1_before=None)
    mid = idx.max() - np.timedelta64(7, "D")
    recent, before = idx > mid, idx <= mid
    yt, yp = np.asarray(y_true), np.asarray(y_pred)
    if recent.sum() < S.SLOTS_PER_DAY * 3 or before.sum() < S.SLOTS_PER_DAY * 3:
        return dict(drift=False, f1_recent=None, f1_before=None)
    if yt[recent].sum() < min_positives or yt[before].sum() < min_positives:   # too rare to judge (~30 min a day)
        return dict(drift=False, f1_recent=None, f1_before=None)
    f_recent = f1_score(yt[recent], yp[recent], zero_division=0)
    f_before = f1_score(yt[before], yp[before], zero_division=0)
    judged = f_before >= 0.4                       # a weak model can't tell a routine change from noise
    return dict(drift=bool(judged and f_before - f_recent >= S.DRIFT_DROP),
                f1_recent=round(float(f_recent), 3), f1_before=round(float(f_before), 3))
