"""
Various utils for communication with Edeos API.
"""
import logging
from urlparse import urlparse

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
