from pathlib import Path

_PERSONAS_DIR = Path(__file__).parent
_SECTION_FILES = ["role.txt", "personality.txt", "rules.txt"]

class PersonaService:

    @staticmethod
    def get_persona(name: str) -> str:
        persona_dir = _PERSONAS_DIR / name
        sections = []
        for filename in _SECTION_FILES:
            filepath = persona_dir / filename
            sections.append(filepath.read_text(encoding="utf-8").strip())
        return "\n\n".join(sections)

