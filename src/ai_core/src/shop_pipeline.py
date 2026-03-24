"""
Shop camera pipeline.

Extends BasePipeline with shop-specific features.
Currently a skeleton — ready for future additions such as:
- Customer counting per time period
- Heatmap generation
- Customer vs staff distinction
- Dwell time tracking
"""

from __future__ import annotations

from .base_pipeline import BasePipeline


class ShopPipeline(BasePipeline):
    """Pipeline for shop cameras. Inherits generic detection + recognition from BasePipeline."""

    pass
