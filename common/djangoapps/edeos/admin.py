""" Django admin pages for edeox app """
from ratelimitbackend import admin

from django.conf import settings
from edeos.models import UserSocialLink
from edeos.tasks import send_api_request
from edeos.utils import get_user_id


class UserSocialLinkAdmin(admin.ModelAdmin):
    """ Admin interface for the User model. """

    def get_readonly_fields(self, request, obj=None):
        """
        Allows staff to approve social links.
        """
        django_readonly = super(UserSocialLinkAdmin, self).get_readonly_fields(request, obj)

        if obj and not obj.is_verified and not request.user.is_superuser:
            return django_readonly + ('user_profile', 'platform', 'social_link')
        elif obj and not request.user.is_superuser:
            return django_readonly + ('user_profile', 'platform', 'social_link', 'is_verified')

        return django_readonly

    def save_model(self, request, obj, form, change):
        if 'is_verified' in form.changed_data and obj.is_verified is True:
            edeos_event_data = {
                'payload': {
                    'student_id': get_user_id(obj.user_profile.user),
                    'client_id': getattr(settings, 'EDEOS_API_KEY'),
                    'event_type': 7,
                    'event_details': {
                        'event_type_verbose': 'social_profile_approval',
                        "social_network": obj.platform,
                    }
                },
                "api_endpoint": "transactions_store",
                "key": getattr(settings, 'EDEOS_API_KEY'),
                "secret": getattr(settings, 'EDEOS_API_SECRET'),
                "base_url": getattr(settings, 'EDEOS_API_URL')
            }
            send_api_request(edeos_event_data)
        super(UserSocialLinkAdmin, self).save_model(request, obj, form, change)


admin.site.register(UserSocialLink, UserSocialLinkAdmin)
