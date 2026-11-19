"""Small synchronous oneM2M HTTP adapter for a simulation agent."""
import json
import uuid
import requests


class OneM2MError(RuntimeError):
    pass


class CSEClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.trust_env = False

    def call(self, method, path='', origin='CAdmin', body=None, ty=None):
        headers = {'X-M2M-Origin': origin, 'X-M2M-RI': uuid.uuid4().hex,
                   'X-M2M-RVI': '4', 'Accept': 'application/json'}
        if body is not None:
            headers['Content-Type'] = 'application/json' + (f';ty={ty}' if ty else '')
        response = self.session.request(method, self.base_url + path, headers=headers,
                                        json=body, timeout=12)
        try:
            data = response.json()
        except ValueError:
            data = {'m2m:dbg': response.text[:300]}
        return response, data

    def require(self, method, path='', origin='CAdmin', body=None, ty=None, statuses=(200,)):
        response, data = self.call(method, path, origin, body, ty)
        rsc = response.headers.get('X-M2M-RSC')
        if response.status_code not in statuses or (rsc and not 2000 <= int(rsc) < 3000):
            raise OneM2MError(f'{method} {path}: HTTP {response.status_code}, RSC {rsc}: {data}')
        return data


class AgentHTTPAdapter:
    """Uses platform-provisioned resources and the agent's own Originator.

    No simulation movement or hazard algorithm is implemented here. The simulator
    calls these methods, interprets returned commands, and performs its own actions.
    """
    def __init__(self, base_url, ae_name, originator, map_path):
        self.client = CSEClient(base_url)
        self.ae_path = '/' + ae_name.strip('/')
        self.originator = originator
        self.map_path = map_path

    def _post(self, container, payload):
        return self.client.require('POST', self.ae_path + '/' + container, self.originator,
                                   {'m2m:cin': {'con': json.dumps(payload)}}, 4, (201,))['m2m:cin']

    def post_observation(self, payload):
        return self._post('observations', payload)

    def post_bid(self, payload):
        return self._post('bids', payload)

    def _latest(self, path):
        response, data = self.client.call('GET', path + '/la', self.originator)
        if response.status_code == 404:
            return None
        rsc = response.headers.get('X-M2M-RSC')
        if response.status_code != 200 or (rsc and int(rsc) != 2000):
            raise OneM2MError(f'Read latest: HTTP {response.status_code}, RSC {rsc}: {data}')
        return json.loads(data['m2m:cin']['con'])

    def latest_command(self):
        return self._latest(self.ae_path + '/commands')

    def latest_map(self):
        return self._latest(self.map_path)

    def close(self):
        self.client.session.close()
