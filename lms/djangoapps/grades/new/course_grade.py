"""
CourseGrade Class
"""
from abc import abstractmethod
from collections import OrderedDict, defaultdict
from logging import getLogger

from django.conf import settings
from lazy import lazy

from xmodule import block_metadata_utils

from ..models import PersistentCourseGrade
from .subsection_grade import ZeroSubsectionGrade
from .subsection_grade_factory import SubsectionGradeFactory
from openedx.core.djangoapps.signals.signals import COURSE_GRADE_CHANGED, COURSE_GRADE_UPDATED_CREATED


log = getLogger(__name__)


def uniqueify(iterable):
    return OrderedDict([(item, None) for item in iterable]).keys()


class CourseGradeBase(object):
    """
    Base class for Course Grades.
    """
    def __init__(self, user, course_data, percent=0, letter_grade=None, passed=False, force_update_subsections=False):
        self.user = user
        self.course_data = course_data

        self.percent = percent
        self.passed = passed

        # Convert empty strings to None when reading from the table
        self.letter_grade = letter_grade or None
        self.force_update_subsections = force_update_subsections

    def __unicode__(self):
        return u'Course Grade: percent: {}, letter_grade: {}, passed: {}'.format(
            unicode(self.percent),
            self.letter_grade,
            self.passed,
        )

    @property
    def attempted(self):
        """
        Returns whether at least one problem was attempted
        by the user in the course.
        """
        return False

    @lazy
    def graded_subsections_by_format(self):
        """
        Returns grades for the subsections in the course in
        a dict keyed by subsection format types.
        """
        subsections_by_format = defaultdict(OrderedDict)
        for chapter in self.chapter_grades.itervalues():
            for subsection_grade in chapter['sections']:
                if subsection_grade.graded:
                    graded_total = subsection_grade.graded_total
                    if graded_total.possible > 0:
                        subsections_by_format[subsection_grade.format][subsection_grade.location] = subsection_grade
        return subsections_by_format

    @lazy
    def chapter_grades(self):
        """
        Returns a dictionary of dictionaries.
        The primary dictionary is keyed by the chapter's usage_key.
        The secondary dictionary contains the chapter's
        subsection grades, display name, and url name.
        """
        course_structure = self.course_data.structure
        grades = OrderedDict()
        for chapter_key in course_structure.get_children(self.course_data.location):
            grades[chapter_key] = self._get_chapter_grade_info(course_structure[chapter_key], course_structure)
        return grades

    @lazy
    def subsection_grades(self):
        """
        Returns an ordered dictionary of subsection grades,
        keyed by subsection location.
        """
        subsection_grades = defaultdict(OrderedDict)
        for chapter in self.chapter_grades.itervalues():
            for subsection_grade in chapter['sections']:
                subsection_grades[subsection_grade.location] = subsection_grade
        return subsection_grades

    @lazy
    def problem_scores(self):
        """
        Returns a dict of problem scores keyed by their locations.
        """
        problem_scores = {}
        for chapter in self.chapter_grades.itervalues():
            for subsection_grade in chapter['sections']:
                problem_scores.update(subsection_grade.problem_scores)
        return problem_scores

    def score_for_chapter(self, chapter_key):
        """
        Returns the aggregate weighted score for the given chapter.
        Raises:
            KeyError if the chapter is not found.
        """
        earned, possible = 0.0, 0.0
        chapter_grade = self.chapter_grades[chapter_key]
        for section in chapter_grade['sections']:
            earned += section.graded_total.earned
            possible += section.graded_total.possible
        return earned, possible

    def score_for_module(self, location):
        """
        Calculate the aggregate weighted score for any location in the course.
        This method returns a tuple containing (earned_score, possible_score).
        If the location is of 'problem' type, this method will return the
        possible and earned scores for that problem. If the location refers to a
        composite module (a vertical or section ) the scores will be the sums of
        all scored problems that are children of the chosen location.
        """
        if location in self.problem_scores:
            score = self.problem_scores[location]
            return score.earned, score.possible
        children = self.course_data.structure.get_children(location)
        earned, possible = 0.0, 0.0
        for child in children:
            child_earned, child_possible = self.score_for_module(child)
            earned += child_earned
            possible += child_possible
        return earned, possible

    @lazy
    def grader_result(self):
        """
        Returns the result from the course grader.
        """
        course = self.course_data.course
        course.set_grading_policy(course.grading_policy)
        return course.grader.grade(
            self.graded_subsections_by_format,
            generate_random_scores=settings.GENERATE_PROFILE_SCORES,
        )

    @property
    def summary(self):
        """
        Returns the grade summary as calculated by the course's grader.
        DEPRECATED: To be removed as part of TNL-5291.
        """
        # TODO(TNL-5291) Remove usages of this deprecated property.
        grade_summary = self.grader_result
        grade_summary['percent'] = self.percent
        grade_summary['grade'] = self.letter_grade
        return grade_summary

    def _get_chapter_grade_info(self, chapter, course_structure):
        """
        Helper that returns a dictionary of chapter grade information.
        """
        chapter_subsection_grades = self._get_subsection_grades(course_structure, chapter.location)
        return {
            'display_name': block_metadata_utils.display_name_with_default_escaped(chapter),
            'url_name': block_metadata_utils.url_name_for_block(chapter),
            'sections': chapter_subsection_grades,
        }

    def _get_subsection_grades(self, course_structure, chapter_key):
        """
        Returns a list of subsection grades for the given chapter.
        """
        return [
            self._get_subsection_grade(course_structure[subsection_key])
            for subsection_key in uniqueify(course_structure.get_children(chapter_key))
        ]

    @abstractmethod
    def _get_subsection_grade(self, subsection):
        """
        Abstract method to be implemented by subclasses for returning
        the grade of the given subsection.
        """
        raise NotImplementedError


class ZeroCourseGrade(CourseGradeBase):
    """
    Course Grade class for Zero-value grades when no problems were
    attempted in the course.
    """
    def _get_subsection_grade(self, subsection):
        return ZeroSubsectionGrade(subsection, self.course_data)


class CourseGrade(CourseGradeBase):
    """
    Course Grade class when grades are updated or read from storage.
    """
    def __init__(self, user, course_data, *args, **kwargs):
        super(CourseGrade, self).__init__(user, course_data, *args, **kwargs)
        self._subsection_grade_factory = SubsectionGradeFactory(user, course_data=course_data)

    def update(self):
        """
        Updates the grade for the course. Also updates subsection grades
        if self.force_update_subsections is true, via the lazy call
        to self.grader_result.
        """
        grade_cutoffs = self.course_data.course.grade_cutoffs
        self.percent = self._compute_percent(self.grader_result)
        self.letter_grade = self._compute_letter_grade(grade_cutoffs, self.percent)
        self.passed = self._compute_passed(grade_cutoffs, self.percent)

    @lazy
    def attempted(self):
        """
        Returns whether any of the subsections in this course
        have been attempted by the student.
        """
        for chapter in self.chapter_grades.itervalues():
            for subsection_grade in chapter['sections']:
                if subsection_grade.all_total.first_attempted:
                    return True
        return False

    def _get_subsection_grade(self, subsection):
        # Pass read_only here so the subsection grades can be persisted in bulk at the end.
        if self.force_update_subsections:
            return self._subsection_grade_factory.update(subsection)
        else:
            return self._subsection_grade_factory.create(subsection, read_only=True)

    @staticmethod
    def _compute_percent(grader_result):
        """
        Computes and returns the grade percentage from the given
        result from the grader.
        """
        return round(grader_result['percent'] * 100 + 0.05) / 100

    @staticmethod
    def _compute_letter_grade(grade_cutoffs, percent):
        """
        Computes and returns the course letter grade given the
        inputs, as defined in the grading_policy (e.g. 'A' 'B' 'C')
        or None if not passed.
        """
        letter_grade = None

        # Possible grades, sorted in descending order of score
        descending_grades = sorted(grade_cutoffs, key=lambda x: grade_cutoffs[x], reverse=True)
        for possible_grade in descending_grades:
            if percent >= grade_cutoffs[possible_grade]:
                letter_grade = possible_grade
                break

        return letter_grade

    @staticmethod
    def _compute_passed(grade_cutoffs, percent):
        """
        Computes and returns whether the given percent value
        is a passing grade according to the given grade cutoffs.
        """
        nonzero_cutoffs = [cutoff for cutoff in grade_cutoffs.values() if cutoff > 0]
        success_cutoff = min(nonzero_cutoffs) if nonzero_cutoffs else None
        return success_cutoff and percent >= success_cutoff

    def _signal_listeners_when_grade_computed(self):
        """
        Signal all listeners when grades are computed.
        """
        responses = COURSE_GRADE_CHANGED.send_robust(
            sender=None,
            user=self.user,  # TODO test, `self.student` (here and below)
            course_grade=self,
            course_key=self.course.id,
            deadline=self.course.end
        )

        for receiver, response in responses:
            log.debug(
                'Signal fired when student grade is calculated. Receiver: %s. Response: %s',
                receiver, response
            )

    def _signal_listeners_when_grade_updated_created(self):
        """
        Signal all listeners when grades are computed.
        """
        responses = COURSE_GRADE_UPDATED_CREATED.send_robust(
            sender=None,
            user=self.user,
            course_grade=self,
            course_key=self.course.id,
            deadline=self.course.end
        )

        for receiver, response in responses:
            log.debug(
                'Signal fired when student grade is calculated. Receiver: %s. Response: %s',
                receiver, response
            )

    def compute_and_update(self, read_only=False):
        """
        Computes the grade for the given student and course.

        If read_only is True, doesn't save any updates to the grades.
        """
        subsections_total = sum(len(chapter['sections']) for chapter in self.chapter_grades)

        total_graded_subsections = sum(len(x) for x in self.graded_subsections_by_format.itervalues())
        subsections_created = len(self._subsection_grade_factory._unsaved_subsection_grades)  # pylint: disable=protected-access
        subsections_read = subsections_total - subsections_created
        blocks_total = len(self.locations_to_scores)
        if not read_only:
            self._subsection_grade_factory.bulk_create_unsaved()
            grading_policy_hash = self.get_grading_policy_hash(self.course.location, self.course_data.structure)
            PersistentCourseGrade.update_or_create_course_grade(
                user_id=self.user.id,
                course_id=self.course.id,
                course_version=self.course_version,
                course_edited_timestamp=self.course_edited_timestamp,
                grading_policy_hash=grading_policy_hash,
                percent_grade=self.percent,
                letter_grade=self.letter_grade or "",
                passed=self.passed,
            )
            self._signal_listeners_when_grade_updated_created()

        self._signal_listeners_when_grade_computed()
        self._log_event(
            log.warning,
            u"compute_and_update, read_only: {0}, subsections read/created: {1}/{2}, blocks accessed: {3}, total "
            u"graded subsections: {4}".format(
                read_only,
                subsections_read,
                subsections_created,
                blocks_total,
                total_graded_subsections,
            )
        )

    def _log_event(self, log_func, log_statement):
        """
        Logs the given statement, for this instance.
        """
        log_func(u"Persistent Grades: CourseGrade.{0}, course: {1}, user: {2}".format(
            log_statement,
            self.course.id,
            self.student.id
        ))
