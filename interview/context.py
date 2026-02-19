from typing import List, Optional

from .models import InterviewSetting

""" 
    setting_id를 기반으로 DB 테이블의 데이터를 가져옴
    setting, skill_name, pre_questions 테이블에서 데이터를 가져온 후 LLM에게 넘겨주기 위해 
    String 데이터 생성
    -정기주- 
"""
class LLMContextService:
    @staticmethod
    def get_setting_context(setting_id: str, resume_text: Optional[str] = None) -> str:

        queryset = InterviewSetting.objects.prefetch_related(
            "settingskill_set__skill",
            "prequestion_set",
        )
        setting: InterviewSetting = queryset.get(setting_id=setting_id)

        skill_names: List[str] = []
        for setting_skill in setting.settingskill_set.all():
            skill_names.append(setting_skill.skill.skill)

        pre_questions = list(setting.prequestion_set.all())

        lines: List[str] = []

        lines.append("[InterviewSetting]")
        lines.append(f"- setting_id: {setting.setting_id}")
        lines.append(f"- question_count: {setting.question_count}")
        lines.append(f"- interviewer_style: {setting.interviewer_style}")
        lines.append(f"- interviewer_gender: {setting.interviewer_gender}")
        lines.append(f"- interviewer_appearance: {setting.interviewer_appearance}")
        lines.append(f"- position: {setting.position or '(없음)'}")
        lines.append("")

        lines.append("[Resume]")
        if resume_text:
            lines.append(resume_text)
        else:
            lines.append("(이력서 없음)")
        lines.append("")

        lines.append("[Skill]")
        for name in skill_names:
            lines.append(f"- {name}")
        lines.append("")

        lines.append("[PreQuestion]")
        for pre_question in pre_questions:
            lines.append(f"Q: {pre_question.question}")
            lines.append(f"A: {pre_question.answer}")
            lines.append("")

        return "\n".join(lines).strip()
