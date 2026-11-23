from __future__ import annotations

from ..models.types import ModelSpec, Usage


def calculate_cost(spec: ModelSpec, usage: Usage) -> float:
    """按模型定价计算单次调用成本（元）。"""
    pricing = spec.pricing
    return (
        usage.input_tokens / 1_000_000 * pricing.input_per_mtok
        + usage.output_tokens / 1_000_000 * pricing.output_per_mtok
    )
