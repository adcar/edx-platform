"""
Various utils for communication with Edeos API.
"""
import logging
from urlparse import urlparse

from django.contrib.sites.models import Site
from opaque_keys.edx.keys import CourseKey
from xmodule.modulestore.django import modulestore

from api_calls import ALLOWED_EDEOS_API_ENDPOINTS_NAMES, EdeosApiClient

log = logging.getLogger(__name__)


def get_balance(request):
    return 120


def send_edeos_api_request(**kwargs):
    """
    Initialize Edeos API client and perform respective call.
    """
    api_scheme_host = '{uri.scheme}://{uri.netloc}/'.format(uri=urlparse(kwargs.get('base_url')))
    api_path = '{uri.path}'.format(uri=urlparse(kwargs.get('base_url')))
    edeos_client = EdeosApiClient(client_id=kwargs.get("key"),
                                  client_secret=kwargs.get("secret"),
                                  api_scheme_host=api_scheme_host,
                                  api_path=api_path)
    api_endpoint = kwargs.get("api_endpoint")
    if api_endpoint in ALLOWED_EDEOS_API_ENDPOINTS_NAMES:
        log.info("Data to be sent to Edeos endpoint {} - {}, api_scheme_host - {}, api_path - {}".
                 format(api_endpoint, kwargs.get('payload'), api_scheme_host, api_path))
        endpoint_to_call = getattr(edeos_client, api_endpoint)
        response = endpoint_to_call(payload=kwargs.get('payload'))
        return response
    else:
        log.exception("Disallowed Edeos endpoint name: '{}'".format(api_endpoint))
        return None


# TODO: this could go as a mixin
def prepare_edeos_data(model_obj, event_type):
    """
    Prepare and send event data to Edeos.

    Arguments:
        model_obj (instance of a subclass of `django.db.models.Model`): object to collect
            event data from, e.g. `StudentModule` obj.
        event_type (int): type of event to send.
            # TODO prepare event types mapping
    """
    EDEOS_FIELDS = (
        'edeos_base_url',
        'edeos_secret',
        'edeos_key',
    )

    def _is_valid(fields):
        for field in EDEOS_FIELDS:
            if not fields.get(field):
                log.error('Field "{}" is improperly configured.'.format(field))
                return False
        return True

    org = model_obj.course_id.org
    course_id = unicode(model_obj.course_id)
    course_key = CourseKey.from_string(course_id)
    course = modulestore().get_course(course_key)
    edeos_fields = {
        'edeos_secret': course.edeos_secret,
        'edeos_key': course.edeos_key,
        'edeos_base_url': course.edeos_base_url
    }
    if course.edeos_enabled:
        if _is_valid(edeos_fields):
            payload = {
                'student_id': model_obj.user.email,
                'course_id': course_id,
                'org': org,
                'lms_url': "{}.{}".format("lms", Site.objects.get_current().domain),
                'event_type': event_type,
            }
            data = {
                'payload': payload,
                'secret': course.edeos_secret,
                'key': course.edeos_key,
                'base_url': course.edeos_base_url,
                'api_endpoint': 'transactions_store'
            }
            return data
    return None
