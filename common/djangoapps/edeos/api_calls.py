"""
Send user's achievements to external service during the course progress
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


class EdeosApiClientError(object):
    # TODO
    pass


class EdeosApiClient(object):
    """
    Edeos API client.

    Sends requests to Edeos API directly.
    Responsible for API credentials issuing and `access_token` refreshing.
    Communicates with Edeos API endpoints directly.

    Inspired by:
        https://github.com/raccoongang/xblock-video/blob/dev/video_xblock/backends/brightcove.py
    """
    def __init__(self, client_id, client_secret):
        """
        Initialize Edeos API client.

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
        url = "http://195.160.222.156/oauth/token"  # TODO configure domain somewhere in settings
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
        """
        headers_ = {
            'Authorization': 'Bearer ' + self.access_token,
            'Content-type': 'application/json'
        }
        if headers is not None:
            headers_.update(headers)
        resp = requests.post(url, data=payload, headers=headers_)
        log.info("Edeos response status: {}".format(resp.status_code))
        if resp.status_code in (httplib.OK, httplib.CREATED):
            return resp.json()

        elif resp.status_code == httplib.UNAUTHORIZED and can_retry:
            self.access_token = self._refresh_access_token()
            return self.post(url, payload, headers, can_retry=False)

        # TODO handle errors

    def wallet_store(self):
        url = "/api/point/v1/wallet/store"  # TODO: pre-configure domain (here and below)

    def wallet_update(self):
        url = "/api/point/v1/wallet/update"

    def wallet_balance(self):
        url = "/api/point/v1/wallet/balance"

    def transactions(self):
        url = "/api/point/v1/transactions"

    def transactions_store(self):
        url = "/api/point/v1/transactions/store"


if __name__ == "__main__":
    client_id = ""
    client_secret = ""
    client = EdeosApiClient(client_id, client_secret)

    payload = {'course_id': 'course-v1:PartnerFY18Q3+DEV279x+course',
               'student_id': 'olena.persianova@gmail.com:example.com',
               'uid': '30_course-v1:PartnerFY18Q3+DEV279x+course',
               'event_type': 1,
               'org': 'PartnerFY18Q3'}
    api_domain = 'http://195.160.222.156'
    api_base_url = "/api/point/v1/"
    api_endpoint_url = "transactions/store"
    response = client.post(url="{}{}{}".format(api_domain, api_base_url, api_endpoint_url),
                           payload=payload)
    print("Edeos response: status - {}, content - {}".format(response.status_code, response.content))
