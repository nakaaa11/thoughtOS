from dataclasses import dataclass, field


@dataclass
class ExtractResult:
    title: str | None
    content: str
    created_at: str | None
    extra_metadata: dict = field(default_factory=dict)
