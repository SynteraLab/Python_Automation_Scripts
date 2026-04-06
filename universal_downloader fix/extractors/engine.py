"""Strategy ordering and orchestration helpers."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Type

from .strategies.base import ExtractionStrategy


class StrategyExecutionEngine:
    """Resolves and groups strategies for staged execution."""

    DEFAULT_STAGE_ORDER = ["discovery", "player", "content", "browser"]

    def resolve(
        self,
        strategy_classes: Iterable[Type[ExtractionStrategy]],
        hints: Dict[str, Any],
    ) -> List[Type[ExtractionStrategy]]:
        strategies = list(dict.fromkeys(strategy_classes))
        strategy_order = hints.get("strategy_order") or []

        if strategy_order:
            order_map = {name: idx for idx, name in enumerate(strategy_order)}
            strategies.sort(
                key=lambda cls: (
                    order_map.get(getattr(cls, "NAME", cls.__name__), 10_000),
                    getattr(cls, "PRIORITY", 100),
                    getattr(cls, "NAME", cls.__name__),
                )
            )
            return strategies

        stage_order = {stage: idx for idx, stage in enumerate(self.DEFAULT_STAGE_ORDER)}
        strategies.sort(
            key=lambda cls: (
                stage_order.get(getattr(cls, "STAGE", "content"), 999),
                getattr(cls, "PRIORITY", 100),
                getattr(cls, "NAME", cls.__name__),
            )
        )
        return strategies

    def group_by_stage(
        self,
        strategy_classes: Iterable[Type[ExtractionStrategy]],
    ) -> List[List[Type[ExtractionStrategy]]]:
        grouped: Dict[str, List[Type[ExtractionStrategy]]] = defaultdict(list)
        for strategy_cls in strategy_classes:
            grouped[getattr(strategy_cls, "STAGE", "content")].append(strategy_cls)

        ordered_groups: List[List[Type[ExtractionStrategy]]] = []
        for stage in self.DEFAULT_STAGE_ORDER:
            if grouped.get(stage):
                ordered_groups.append(grouped[stage])

        for stage, values in grouped.items():
            if stage not in self.DEFAULT_STAGE_ORDER:
                ordered_groups.append(values)

        return ordered_groups
