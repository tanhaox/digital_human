"""asset_tagging 单元测试."""
from __future__ import annotations

import pytest

from app.services.asset_tagging import (
    LOCATION_DOMESTIC,
    LOCATION_FOREIGN,
    ORIENTATION_LANDSCAPE,
    ORIENTATION_PORTRAIT,
    PEOPLE_NONE,
    PEOPLE_YES,
    SCENE_BUSINESS,
    SCENE_CITY,
    SCENE_FINANCE,
    SCENE_INDUSTRIAL,
    SCENE_NATURE,
    SCENE_TECH,
    SHOT_AERIAL,
    SHOT_ARCHITECTURE,
    SHOT_EMPTY,
    SHOT_PORTRAIT,
    SHOT_TRAFFIC,
    SOURCE_TYPE_CREATIVE,
    SOURCE_TYPE_FOOTAGE,
    infer_orientation,
    infer_tags,
)


class TestInferOrientation:
    def test_portrait(self) -> None:
        assert infer_orientation(1080, 1920) == ORIENTATION_PORTRAIT

    def test_landscape(self) -> None:
        assert infer_orientation(1920, 1080) == ORIENTATION_LANDSCAPE

    def test_equal_size_defaults_landscape(self) -> None:
        assert infer_orientation(1080, 1080) == ORIENTATION_LANDSCAPE

    def test_zero_defaults_landscape(self) -> None:
        assert infer_orientation(0, 0) == ORIENTATION_LANDSCAPE


class TestInferTags:
    def test_simple_city_footage(self) -> None:
        result = infer_tags("city skyline timelapse", 1920, 1080)
        assert result["orientation"] == ORIENTATION_LANDSCAPE
        assert result["source_type"] == SOURCE_TYPE_FOOTAGE
        assert result["location"] == LOCATION_FOREIGN
        assert SCENE_CITY in result["scenes"]
        assert result["people"] == PEOPLE_NONE
        assert SHOT_EMPTY in result["shot_types"]

    def test_domestic_tech(self) -> None:
        result = infer_tags("china technology startup office", 1920, 1080)
        assert result["location"] == LOCATION_DOMESTIC
        assert SCENE_TECH in result["scenes"]
        assert SCENE_BUSINESS in result["scenes"]

    def test_creative_animation(self) -> None:
        result = infer_tags("motion graphics intro", 1920, 1080)
        assert result["source_type"] == SOURCE_TYPE_CREATIVE

    def test_people_no_empty_shot(self) -> None:
        result = infer_tags("portrait of business woman", 1080, 1920)
        assert result["orientation"] == ORIENTATION_PORTRAIT
        assert result["people"] == PEOPLE_YES
        assert SHOT_PORTRAIT in result["shot_types"]
        assert SHOT_EMPTY not in result["shot_types"]

    def test_traffic_not_empty(self) -> None:
        result = infer_tags("busy traffic in shanghai", 1920, 1080)
        assert result["location"] == LOCATION_DOMESTIC
        assert SHOT_TRAFFIC in result["shot_types"]
        assert SHOT_EMPTY not in result["shot_types"]

    def test_aerial_industrial(self) -> None:
        result = infer_tags("aerial view of industrial factory", 1920, 1080)
        assert SHOT_AERIAL in result["shot_types"]
        assert SCENE_INDUSTRIAL in result["scenes"]

    def test_finance_keywords(self) -> None:
        result = infer_tags("stock market trading finance economy", 1920, 1080)
        assert SCENE_FINANCE in result["scenes"]

    def test_multiple_scenes(self) -> None:
        result = infer_tags("city nature landscape urban park", 1920, 1080)
        assert SCENE_CITY in result["scenes"]
        assert SCENE_NATURE in result["scenes"]

    def test_architecture_empty_still_no_people(self) -> None:
        # 建筑镜头不算强主体, 无人时仍应加空镜
        result = infer_tags("architecture building exterior", 1920, 1080)
        assert SHOT_ARCHITECTURE in result["shot_types"]
        assert SHOT_EMPTY in result["shot_types"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
