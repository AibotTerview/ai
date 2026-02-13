import os
import django

if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.settings")
    django.setup()

from ai_bot.models import InterviewSetting


class LLMContextService:

    @staticmethod
    def get_setting_context(setting_id: str) -> str:
        setting = (
            InterviewSetting.objects
            .prefetch_related("settingskill_set__skill", "prequestion_set")
            .get(setting_id=setting_id)
        )

        skills = [
            ss.skill.skill
            for ss in setting.settingskill_set.all()
        ]

        pre_questions = setting.prequestion_set.all().order_by("pre_question_id")

        lines = [
            "[InterviewSetting]",
            f"- setting_id: {setting.setting_id}",
            f"- question_count: {setting.question_count}",
            f"- interviewer_style: {setting.interviewer_style}",
            f"- interviewer_gender: {setting.interviewer_gender}",
            f"- interviewer_appearance: {setting.interviewer_appearance}",
            f"- position: {setting.position or '(없음)'}",
            f"- resume_uri: {setting.resume_uri or '(없음)'}",
            "",
            "[Skill]",
        ]

        if skills:
            lines.extend([f"- {s}" for s in skills])
        else:
            lines.append("- (없음)")

        lines.append("")
        lines.append("[PreQuestion]")

        for pq in pre_questions:
            lines.append(f"Q: {pq.question}")
            lines.append(f"A: {pq.answer}")
            lines.append("")

        return "\n".join(lines).strip()
