from pydantic_settings import BaseSettings


class Config(BaseSettings):
    google_cloud_project: str
    lethe_collection: str = "nodes"
    lethe_embedding_model: str = "text-embedding-005"
    lethe_llm_model: str = "gemini-2.5-flash"
    lethe_collision_detection: bool = True
    lethe_similarity_threshold: float = 0.25
    lethe_entity_threshold: float = 0.15
    lethe_region: str = "us-central1"
    lethe_max_hot_edges: int = 20
    log_level: str = "info"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
