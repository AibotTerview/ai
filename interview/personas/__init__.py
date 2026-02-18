from pathlib import Path

_PERSONAS_DIR = Path(__file__).parent
_SECTION_FILES = ["role.txt", "personality.txt", "rules.txt"]

class PersonaService:
    """
        LLM에 넣을 personas 프롬프트를 가져옴
        - 정기주 -
    """
    @staticmethod
    def get_persona(self, name: str | None = None) -> str:
        persona_dir = _PERSONAS_DIR / name
        sections = []
        for filename in _SECTION_FILES:
            filepath = persona_dir / filename
            sections.append(filepath.read_text(encoding="utf-8").strip())

        return "\n\n".join(sections)

    @staticmethod
    def list_personas(self) -> list[str]:
        return sorted(
            d.name for d in _PERSONAS_DIR.iterdir()
            if d.is_dir() and not d.name.startswith("_")
        )
