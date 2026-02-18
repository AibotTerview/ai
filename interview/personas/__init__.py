from pathlib import Path

_PERSONAS_DIR = Path(__file__).parent
_SECTION_FILES = ["role.txt", "personality.txt", "rules.txt"]


class PersonaService:
    DEFAULT_PERSONA = "FORMAL"

    @classmethod
    def _load_persona(cls, name: str) -> str:
        persona_dir = _PERSONAS_DIR / name
        if not persona_dir.is_dir():
            raise KeyError(
                f"페르소나 '{name}'가 없어요. "
                f"사용 가능: {cls.list_personas()}"
            )
        sections = []
        for filename in _SECTION_FILES:
            filepath = persona_dir / filename
            if filepath.exists():
                sections.append(filepath.read_text(encoding="utf-8").strip())
        return "\n\n".join(sections)

    @classmethod
    def get_persona(cls, name: str | None = None) -> str:
        if name is None:
            name = cls.DEFAULT_PERSONA
        return cls._load_persona(name)

    @classmethod
    def list_personas(cls) -> list[str]:
        return sorted(
            d.name for d in _PERSONAS_DIR.iterdir()
            if d.is_dir() and not d.name.startswith("_")
        )
