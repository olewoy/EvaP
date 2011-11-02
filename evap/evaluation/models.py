import random
import sys

from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from django.utils.translation import ugettext_lazy as _

from django_fsm.db.fields import FSMField, transition

# see evaluation.meta for the use of Translate in this file
from evap.evaluation.meta import LocalizeModelBase, Translate

from evap.fsr.models import EmailTemplate

class Semester(models.Model):
    """Represents a semester, e.g. the winter term of 2011/2012."""
    
    __metaclass__ = LocalizeModelBase
    
    name_de = models.CharField(max_length=100, unique=True, verbose_name=_(u"name (german)"))
    name_en = models.CharField(max_length=100, unique=True, verbose_name=_(u"name (english)"))
    
    name = Translate
    
    visible = models.BooleanField(verbose_name=_(u"visible"), default=False)
    created_at = models.DateField(verbose_name=_(u"created at"), auto_now_add=True)
    
    class Meta:
        ordering = ('created_at', 'name_de')
        verbose_name = _(u"semester")
        verbose_name_plural = _(u"semesters")
    
    def __unicode__(self):
        return self.name
    
    @property
    def can_be_deleted(self):
        for course in self.course_set.all():
            if not course.can_be_deleted:
                return False
        return True
    
    @classmethod
    def get_latest_or_none(cls):
        try:
            return cls.objects.all()[0]
        except IndexError:
            return None


class Questionnaire(models.Model):
    """A named collection of questions."""
    
    __metaclass__ = LocalizeModelBase
    
    name_de = models.CharField(max_length=100, unique=True, verbose_name=_(u"name (german)"))
    name_en = models.CharField(max_length=100, unique=True, verbose_name=_(u"name (english)"))
    
    name = Translate
    
    description_de = models.TextField(verbose_name=_(u"description (german)"), blank=True, null=True)
    description_en = models.TextField(verbose_name=_(u"description (english)"), blank=True, null=True)
    
    description = Translate
    
    teaser_de = models.TextField(verbose_name=_(u"teaser (german)"), blank=True, null=True)
    teaser_en = models.TextField(verbose_name=_(u"teaser (english)"), blank=True, null=True)
    
    teaser = Translate
    
    obsolete = models.BooleanField(verbose_name=_(u"obsolete"), default=False)
    
    class Meta:
        ordering = ('name_de',)
        verbose_name = _(u"questionnaire")
        verbose_name_plural = _(u"questionnaires")
    
    def __unicode__(self):
        return self.name

    def used_in_course_kinds(self):
        return {
            "general": self.general_courses.values_list('kind', flat=True).order_by().distinct(),
            "primary_lecturer": self.primary_courses.values_list('kind', flat=True).order_by().distinct(),
            "secondary_lecturer": self.secondary_courses.values_list('kind', flat=True).order_by().distinct(),
        }
    
    @property
    def can_be_deleted(self):
        return not (self.general_courses.exists() or self.primary_courses.exists() or self.secondary_courses.exists())


class Course(models.Model):
    """Models a single course, e.g. the Math 101 course of 2002."""
    
    __metaclass__ = LocalizeModelBase
    
    state = FSMField(default='new', protected=True)

    semester = models.ForeignKey(Semester, verbose_name=_(u"semester"))
    
    name_de = models.CharField(max_length=100, verbose_name=_(u"name (german)"))
    name_en = models.CharField(max_length=100, verbose_name=_(u"name (english)"))
    name = Translate
    
    # type of course: lecture, seminar, project
    kind = models.CharField(max_length=100, verbose_name=_(u"type"))
    
    # bachelor, master, d-school course
    study = models.CharField(max_length=100, verbose_name=_(u"study"))
    
    # students that are allowed to vote
    participants = models.ManyToManyField(User, verbose_name=_(u"participants"), blank=True)
    
    # students that already voted
    voters = models.ManyToManyField(User, verbose_name=_(u"voters"), blank=True, related_name='+')
    
    # two kinds of lecturers, e.g. professor and TAs
    primary_lecturers = models.ManyToManyField(User, verbose_name=_(u"primary lecturers"), blank=True, related_name='primary_courses')
    secondary_lecturers = models.ManyToManyField(User, verbose_name=_(u"secondary lecturers"), blank=True, related_name='secondary_courses')
    
    # different kinds of questionnaires
    general_questions = models.ManyToManyField(Questionnaire, blank=True, verbose_name=_("course questionnaires"), related_name="general_courses")
    primary_lecturer_questions = models.ManyToManyField(Questionnaire, blank=True, verbose_name=_("primary lecturer questionnaires"), related_name="primary_courses")
    secondary_lecturer_questions = models.ManyToManyField(Questionnaire, blank=True, verbose_name=_("secondary lecturer questionnaires"), related_name="secondary_courses")
    
    # when the evaluation takes place
    vote_start_date = models.DateField(null=True, verbose_name=_(u"first date to vote"))
    vote_end_date = models.DateField(null=True, verbose_name=_(u"last date to vote"))
    
    class Meta:
        ordering = ('semester', 'name_de')
        unique_together = (
            ('semester', 'name_de'),
            ('semester', 'name_en'),
        )
        verbose_name = _(u"course")
        verbose_name_plural = _(u"courses")
    
    def __unicode__(self):
        return self.name
    
    def is_fully_checked(self):
        """Shortcut for finding out whether all text answers to this course have been checked"""
        return not self.textanswer_set.filter(checked=False).exists()
    
    def can_user_vote(self, user):
        """Returns whether the user is allowed to vote on this course."""
        return user in self.participants.all() and user not in self.voters.all()
    
    def can_fsr_edit(self):
        return self.state in ['new', 'pendingLecturerApproval', 'pendingFsrApproval', 'approved', 'inEvaluation']
    
    def can_fsr_delete(self):
        return not (self.textanswer_set.exists() or self.gradeanswer_set.exists() or not self.can_fsr_edit())
    
    def can_fsr_review(self):
        return not self.is_fully_checked() and self.state in ['inEvaluation', 'pendingForReview']
    
    def can_fsr_approve(self):
        return self.state in ['new', 'pendingLecturerApproval', 'pendingFsrApproval']
    
    @transition(source='new', target='pendingLecturerApproval')
    def ready_for_lecturer(self):
        EmailTemplate.get_review_template().send(self)
    
    @transition(source='pendingLecturerApproval', target='pendingFsrApproval')
    def lecturer_approve(self):
        pass
    
    @transition(source=['new', 'pendingLecturerApproval', 'pendingFsrApproval'], target='approved')
    def fsr_approve(self):
        pass
    
    @transition(source='approved', target='inEvaluation')
    def evaluation_begin(self):
        pass
    
    @transition(source='inEvaluation', target='pendingForReview')
    def evaluation_end(self):
        pass
    
    @transition(source='pendingForReview', target='pendingPublishing', conditions=[is_fully_checked])
    def review_finished(self):
        pass
    
    @transition(source='pendingPublishing', target='published')
    def publish(self):
        pass
    
    @transition(source='published', target='pendingPublishing')
    def revoke(self):
        pass
    
    def has_enough_questionnaires(self):
        return self.general_questions.exists() \
            and (not self.primary_lecturers.exists() or self.primary_lecturer_questions.exists()) \
            and (not self.secondary_lecturers.exists() or self.secondary_lecturer_questions.exists())
    
    def is_user_lecturer(self, user):
        for lecturer in self.primary_lecturers.all():
            if user == lecturer:
                return True
            if lecturer.get_profile().proxies.filter(pk=user.id).exists():
                return True
        
        for lecturer in self.secondary_lecturers.all():
            if user == lecturer:
                return True
            if lecturer.get_profile().proxies.filter(pk=user.id).exists():
                return True
        
        return False
    
    def warnings(self):
        result = []
        if not self.primary_lecturers.exists():
            result.append(_(u"No primary lecturer assigned."))
        if not self.has_enough_questionnaires():
            result.append(_(u"Not enough questionnaires assigned."))
        return result
    
    @property
    def textanswer_set(self):
        """Pseudo relationship to all text answers for this course"""
        return TextAnswer.objects.filter(course=self)
    
    @property
    def gradeanswer_set(self):
        """Pseudo relationship to all grade answers for this course"""
        return GradeAnswer.objects.filter(course=self)


class Question(models.Model):
    """A question including a type."""
    
    __metaclass__ = LocalizeModelBase
    
    QUESTION_KINDS = (
        (u"T", _(u"Text Question")),
        (u"G", _(u"Grade Question"))
    )
    
    questionnaire = models.ForeignKey(Questionnaire)
    text_de = models.TextField(verbose_name=_(u"question text (german)"))
    text_en = models.TextField(verbose_name=_(u"question text (english)"))
    kind = models.CharField(max_length=1, choices=QUESTION_KINDS,
                            verbose_name=_(u"kind of question"))
    
    text = Translate
    
    class Meta:
        order_with_respect_to = 'questionnaire'
        verbose_name = _(u"question")
        verbose_name_plural = _(u"questions")
    
    def answer_class(self):
        if self.kind == u"T":
            return TextAnswer
        elif self.kind == u"G":
            return GradeAnswer
        else:
            raise Exception("Unknown answer kind: %r" % self.kind)
    
    def is_grade_question(self):
        return self.answer_class() == GradeAnswer
    
    def is_text_question(self):
        return self.answer_class() == TextAnswer


class Answer(models.Model):
    """An abstract answer to a question. For anonymity purposes, the answering
    user ist not stored in the object. Concrete subclasses are `GradeAnswer` and
    `TextAnswer`."""
    
    question = models.ForeignKey(Question)
    course = models.ForeignKey(Course, related_name="+")
    lecturer = models.ForeignKey(User, related_name="+", blank=True, null=True, on_delete=models.SET_NULL)
    
    class Meta:
        abstract = True
        verbose_name = _(u"answer")
        verbose_name_plural = _(u"answers")


class GradeAnswer(Answer):
    """A Likert-scale answer to a question with `1` being *strongly agree* and `5`
    being *strongly disagree*."""
    
    answer = models.IntegerField(verbose_name = _(u"answer"))
    
    class Meta:
        verbose_name = _(u"grade answer")
        verbose_name_plural = _(u"grade answers")


class TextAnswer(Answer):
    """A free-form text answer to a question (usually a comment about a course
    or a lecturer)."""
    
    elements_per_page = 2
    
    censored_answer = models.TextField(verbose_name = _(u"censored answer"), blank=True, null=True)
    original_answer = models.TextField(verbose_name = _(u"original answer"), blank=True)
    
    checked = models.BooleanField(verbose_name = _(u"answer checked"))
    hidden = models.BooleanField(verbose_name = _(u"hide answer"))
    
    # True if the user wants the comment published, even if anonymity cannot be assured
    publication_desired = models.BooleanField(verbose_name = _(u"publication desired"))
    
    class Meta:
        verbose_name = _(u"text answer")
        verbose_name_plural = _(u"text answers")
    
    def _answer_get(self):
        return self.censored_answer or self.original_answer
    
    def _answer_set(self, value):
        self.original_answer = value
        self.censored_answer = None
    
    answer = property(_answer_get, _answer_set)


class UserProfile(models.Model):
    user = models.OneToOneField(User)
    
    # extending first_name and last_name from the user
    title = models.CharField(verbose_name=_(u"Title"), max_length=30, blank=True, null=True)
    
    # picture of the user
    picture = models.ImageField(verbose_name=_(u"Picture"), upload_to="pictures", blank=True, null=True)
    
    # proxies of the user, which can also manage their courses
    proxies = models.ManyToManyField(User, related_name="proxied_users", blank=True)
    
    # true if the user is member of the student representatives
    fsr = models.BooleanField(verbose_name=_(u"Student representative"), default=False)
    
    # key for url based logon of this user
    logon_key = models.IntegerField(blank=True, null=True)
    
    class Meta:
        verbose_name = _('user')
        verbose_name_plural = _('users')
    
    @property
    def full_name(self):
        if self.user.last_name:
            name = self.user.last_name
            if self.user.first_name:
                name = self.user.first_name + " " + name
            if self.title:
                name = self.title + " " + name
            return name
        else:
            return self.user.username
    
    def has_courses(self):
        latest_semester = Semester.get_latest_or_none()
        if latest_semester is None:
            return False
        else:
            return latest_semester.course_set.filter(participants__pk=self.user.id).exists()
    
    def lectures_courses(self):
        latest_semester = Semester.get_latest_or_none()
        if latest_semester is None:
            return False
        else:
            return latest_semester.course_set.filter(primary_lecturers__pk=self.user.id).exists()
    
    def generate_logon_key(self):
        while True:
            key = random.randrange(0, sys.maxint)
            if not UserProfile.objects.filter(logon_key=key).exists():
                # key not yet used
                self.logon_key = key
                break
    
    @staticmethod
    @receiver(post_save, sender=User)
    def create_user_profile(sender, instance, created, **kwargs):
        """Creates a UserProfile object whenever a User is created."""
        if created:
            UserProfile.objects.create(user=instance)
