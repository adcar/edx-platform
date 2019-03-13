"""
Send users' achievements to external service.

e.g. students' achievements are sent during the course progress.
"""
import base64
import httplib
import logging

import requests

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


log = logging.getLogger(__name__)


class GammaEdeosAPIClient(object):
    """
    Send user data to the Gamification server.

    Data is then sent to Edeos by Gamma.
    """

    def __init__(self):
        self.is_enabled = settings.FEATURES.get('ENABLE_EDEOS', False)
        if self.is_enabled:
            self.EDEOS_PROPERTIES = settings.FEATURES.get('EDEOS_PROPERTIES', {})
            if not self.EDEOS_PROPERTIES:
                raise ImproperlyConfigured(
                    "You must set `EDEOS_PROPERTIES` when "
                    "`FEATURES['ENABLE_EDEOS']` is True."
                )
            required_params = ("API_URL", "APP_KEY", "APP_SECRET")
            for param in required_params:
                if param not in self.EDEOS_PROPERTIES:
                    raise ImproperlyConfigured(
                        "You must set `{}` in `EDEOS_PROPERTIES`".format(param)
                    )

    def api_call(self, course_id, org, username, event_type, uid):
        data = {
            'course_id': course_id,
            'org': org,
            'username': username,
            'event_type': event_type,
            'uid': uid,
        }
        headers = {
            'App-key': self.EDEOS_PROPERTIES['APP_KEY'],
            'App-secret': self.EDEOS_PROPERTIES['APP_SECRET']
        }
        requests.put(
            self.EDEOS_PROPERTIES['API_URL']+'gamma-profile/',
            data=data,
            headers=headers,
            verify=False
        )


class EdeosApiBaseClientError(Exception):
    """
    Base class for Edeos API exceptions.

    Subclasses should provide `error_message`.
    """
    error_message = 'An exception occurred.'

    def __init__(self, detail=None):
        """
        Initialization of exceptions base class object.
        """
        self.detail = detail if detail is not None else self.error_message
        super(EdeosApiBaseClientError, self).__init__(detail)

    def __str__(self):
        """
        Override string representation of exceptions base class object.
        """
        return self.detail


class EdeosApiClientError(EdeosApiBaseClientError):
    error_message = 'Edeos API error occurred.'


class EdeosApiClientErrorUnauthorized(EdeosApiBaseClientError):
    error_message = 'Unauthorized call to Edeos API.'


class EdeosBaseApiClient(object):
    """
    Low-level Edeos API client.

    Sends requests to Edeos API directly.
    Responsible for API credentials issuing and `access_token` refreshing.

    Inspired by:
        https://github.com/raccoongang/xblock-video/blob/dev/video_xblock/backends/brightcove.py
    """
    def __init__(self, client_id, client_secret):
        """
        Initialize base Edeos API client.

        Arguments:
            client_id (str): Edeos client id.
            client_secret (str): Edeos client secret.
        """
        self.api_key = client_id
        self.api_secret = client_secret
        if client_id and client_secret:
            self.access_token = self._refresh_access_token()
        else:
            self.access_token = ""

    def _refresh_access_token(self, scope=""):
        """
        Request new access token to send with requests to Edeos.

        Arguments:
            scope (str): OAuth permission scope.

        Returns:
            access_token (str): access token.
        """
        # TODO pre-configure domain
        url = "http://195.160.222.156/oauth/token"
        params = {
            "grant_type": "client_credentials",
            "scope": scope
        }
        auth_string = base64.encodestring(
            '{}:{}'.format(self.api_key, self.api_secret)
        ).replace('\n', '')
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + auth_string
        }
        try:
            resp = requests.post(url, headers=headers, data=params)
            if resp.status_code == httplib.OK:
                result = resp.json()
                return result['access_token']
        except IOError:
            log.exception("Connection issue. Couldn't refresh Edeos API access token.")
            return None

    def post(self, url, payload, headers=None, can_retry=True):
        """
        Issue REST POST request to a given URL.

        Arguments:
            url (str): url to send a request to.
            payload (dict): request data.
            headers (dict): request headers.
            can_retry (bool): indication if requests sending should be retried.

        Returns:
              resp (dict): Edeos response.
        """
        headers_ = {
            'Authorization': 'Bearer ' + self.access_token,
            'Content-type': 'application/json'
        }
        if headers is not None:
            headers_.update(headers)
        resp = requests.post(url, data=payload, headers=headers_)
        log.info("Edeos response: status {}, content {}".format(resp.status_code, resp.content))
        if resp.status_code in (httplib.OK, httplib.CREATED):
            return resp.json()
        elif resp.status_code == httplib.UNAUTHORIZED and can_retry:
            self.access_token = self._refresh_access_token()
            return self.post(url, payload, headers, can_retry=False)
        elif resp.status_code == httplib.UNAUTHORIZED:
            raise EdeosApiClientErrorUnauthorized
        else:
            raise EdeosApiClientError


class EdeosApiClient(EdeosBaseApiClient):
    """
    High-level Edeos API client.

    Communicates with Edeos API endpoints directly.
    """
    def __init__(self, client_id, client_secret, base_url):
        """
        Initialize high-level Edeos API client.

        Arguments:
            client_id (str): Edeos client id.
            client_secret (str): Edeos client secret.
            base_url (url): base API url, e.g.
                "http://111.111.111.111/api/point/v1/".
        """
        self.base_url = base_url
        super(EdeosApiClient, self).__init__(client_id, client_secret)

    def call_api(self, endpoint_url, payload):
        try:
            response = client.post(
                url="{}{}".format(self.base_url, endpoint_url),
                payload=payload)
            log.info("Edeos '{}' response: status - {}, content - {}".
                     format(endpoint_url, response.status_code, response.content))
            return response
        except (EdeosApiClientError, EdeosApiClientErrorUnauthorized) as e:
            print("Edeos '{}' call failed. {}".format(endpoint_url, e.__class__.error_message))
            return None
        except ValueError as e:
            log.exception("Edeos '{}' call failed. {}".format(endpoint_url, e.message))
            return None

    # TODO validate payload below (required/optional params)

    def wallet_store(self, payload):
        return self.call_api("wallet/store", payload)

    def wallet_update(self, payload):
        return self.call_api("wallet/update", payload)

    def wallet_balance(self, payload):
        return self.call_api("wallet/balance", payload)

    def transactions(self, payload):
        return self.call_api("transactions", payload)

    def transactions_store(self, payload):
        """
        Store new event data.

        Event examples: course enrollment, certificate issuing.

        Arguments:
             payload (dict): data on an event to send to Edeos, e.g.
                 {
                   'course_id': 'course-v1:PartnerFY18Q3+DEV279x+course',
                   'student_id': 'test@gmail.com:example.com',
                   'uid': '30_course-v1:PartnerFY18Q3+DEV279x+course',
                   'event_type': 1,
                   'org': 'PartnerFY18Q3'
                 }

        Returns:
              response (dict): Edeos response.
        """
        return self.call_api("transactions/store", payload)


if __name__ == "__main__":
    client_id = ""
    client_secret = ""
    client = EdeosApiClient(client_id, client_secret, "http://195.160.222.156/api/point/v1/")

    payload = {'course_id': 'course-v1:PartnerFY18Q3+DEV279x+course',
               'student_id': 'olena.persianova@gmail.com:example.com',
               'uid': '30_course-v1:PartnerFY18Q3+DEV279x+course',
               'event_type': 1,
               'org': 'PartnerFY18Q3'}
    # Endpoints consume different payloads, sure thing
    response = client.transactions_store(payload=payload)
    response1 = client.wallet_store(payload=payload)
    response2 = client.wallet_update(payload=payload)
    response3 = client.wallet_balance(payload=payload)
    response4 = client.transactions(payload=payload)

