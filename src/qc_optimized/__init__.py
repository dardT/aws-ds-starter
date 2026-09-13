"""Modélisation SECOM optimisée, distincte de la baseline pédagogique."""

from qc_optimized.secom import (
    ThresholdSelection,
    build_logistic_model,
    operating_metrics,
    select_f2_threshold,
)

__all__ = [
    "ThresholdSelection",
    "build_logistic_model",
    "operating_metrics",
    "select_f2_threshold",
]
