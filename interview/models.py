from django.db import models

class Interview(models.Model):
    interview_id = models.CharField(primary_key=True, max_length=36)
    setting = models.OneToOneField('InterviewSetting', models.DO_NOTHING)
    duration = models.BigIntegerField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    ai_overall_review = models.TextField(blank=True, null=True)
    interview_name = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'interview'


class InterviewMaterial(models.Model):
    material_id = models.CharField(primary_key=True, max_length=36)
    interview = models.ForeignKey(Interview, models.DO_NOTHING)
    material_type = models.CharField(max_length=20)
    file_path = models.CharField(max_length=255)
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'interview_material'


class InterviewQuestion(models.Model):
    question_id = models.CharField(primary_key=True, max_length=36)
    interview = models.ForeignKey(Interview, models.DO_NOTHING)
    question = models.TextField()
    answer = models.TextField(blank=True, null=True)
    feedback = models.TextField(default='')  # AI 평가 결과 저장
    created_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'interview_question'


class InterviewScore(models.Model):
    score_id = models.CharField(primary_key=True, max_length=36)
    interview = models.ForeignKey(Interview, models.DO_NOTHING)
    score_type = models.CharField(max_length=30, blank=True, null=True)
    score = models.IntegerField(blank=True, null=True)
    evaludation = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'interview_score'

class InterviewSetting(models.Model):
    setting_id = models.CharField(primary_key=True, max_length=36)
    user = models.ForeignKey('User', models.DO_NOTHING)
    question_count = models.IntegerField()
    interviewer_style = models.CharField(max_length=20)
    interviewer_gender = models.CharField(max_length=20)
    interviewer_appearance = models.CharField(max_length=20)
    created_at = models.DateTimeField()
    resume_uri = models.CharField(max_length=255, blank=True, null=True)
    position = models.CharField(max_length=20, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'interview_setting'


class PreQuestion(models.Model):
    pre_question_id = models.CharField(primary_key=True, max_length=36)
    setting = models.ForeignKey(InterviewSetting, models.DO_NOTHING)
    question = models.TextField()
    answer = models.TextField()

    class Meta:
        managed = False
        db_table = 'pre_question'


class SettingSkill(models.Model):
    # Django usually needs a single primary key, but this is a composite key table.
    # We might not need to query this directly for saving results, so keeping as is or ignoring.
    # Inspectdb put a composite PK fake field.
    setting = models.ForeignKey(InterviewSetting, models.DO_NOTHING, primary_key=True)
    skill = models.ForeignKey('Skill', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'setting_skill'
        unique_together = (('setting', 'skill'),)


class Skill(models.Model):
    skill_id = models.CharField(primary_key=True, max_length=36)
    skill = models.CharField(unique=True, max_length=100)

    class Meta:
        managed = False
        db_table = 'skill'


class User(models.Model):
    user_id = models.CharField(primary_key=True, max_length=36)
    email = models.CharField(max_length=255)
    password = models.CharField(max_length=255)
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField()
    oauth = models.CharField(max_length=20, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'user'
