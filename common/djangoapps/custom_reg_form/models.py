from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import ugettext as _


class ExtraInfo(models.Model):
    """
    This model contains two extra fields that will be saved when a user registers.
    The form that wraps this model is in the forms.py file.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL)
    mobile = models.CharField(
        verbose_name=_("Mobile Number"),
        max_length=9,
        blank=True,
        null=True
    )
    nationality_id = models.CharField(
        verbose_name=_("National ID"),
        max_length=10,
        unique=True
    )
    date_of_birth_day = models.PositiveIntegerField(
        validators=[
            MaxValueValidator(30),
            MinValueValidator(1)
        ]
    )
    date_of_birth_month = models.PositiveIntegerField(
        validators=[
            MaxValueValidator(12),
            MinValueValidator(1)
        ]
    )
    date_of_birth_year = models.PositiveIntegerField()

    def __str__(self):
        return 'ExtraInfo: {}[{}]'.format(self.user.username, self.nationality_id)

    @property
    def date_of_birth(self):
        return '{}{}{}'.format(
            self.date_of_birth_year or '0000',
            str(self.date_of_birth_month or '').rjust(2, '0'),
            str(self.date_of_birth_day or '').rjust(2, '0')
        )

    @classmethod
    def has_national_id(cls, user):
        return bool(hasattr(user, 'extrainfo') and user.extrainfo.nationality_id)
