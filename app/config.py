from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ollama_base_url: str = "http://localhost:11434"
    chat_model: str = "qwen2.5:3b"
    embedding_model: str = "bge-m3"
    index_dir: Path = Path("data/index")
    chunk_size: int = Field(default=800, ge=100, le=8000)
    chunk_overlap: int = Field(default=120, ge=0)
    top_k: int = Field(default=4, ge=1, le=10)
    max_context_chars: int = Field(default=6000, ge=100, le=32000)
    ollama_timeout: float = Field(default=120, gt=0)
    history_db: Path = Path("data/conversations.sqlite3")
    history_max_turns: int = Field(default=10, ge=1, le=100)
    max_history_chars: int = Field(default=4000, ge=100, le=16000)

    @model_validator(mode="after")
    def validate_overlap(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP 必须小于 CHUNK_SIZE")
        return self
