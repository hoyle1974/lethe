import os
from unittest.mock import patch

import pytest


def test_config_defaults():
    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True):
        from lethe.config import Config

        cfg = Config()
        assert cfg.google_cloud_project == "test-project"
        assert cfg.lethe_collection == "nodes"
        assert cfg.lethe_embedding_model == "text-embedding-005"
        assert cfg.lethe_llm_model == "gemini-2.5-flash"
        assert cfg.lethe_collision_detection is True
        assert cfg.lethe_similarity_threshold == 0.25
        assert cfg.lethe_entity_threshold == 0.15
        assert cfg.lethe_region == "us-central1"


def test_config_missing_project():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(Exception):
            from lethe.config import Config

            Config(_env_file=None)
