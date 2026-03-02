import socketpool
import ssl
import wifi

import adafruit_requests


def make_device_id(mac_bytes):
    if isinstance(mac_bytes, (bytes, bytearray)) and len(mac_bytes) == 6:
        return bytes(mac_bytes).hex()
    return ""

class ServerMatchClient:
    def __init__(self, base_url, app_key, timeout_s=2.0):
        self.base_url = (base_url or "").rstrip("/")
        self.app_key = app_key or ""
        self.timeout_s = timeout_s

        self._pool = socketpool.SocketPool(wifi.radio)
        self._ssl_context = ssl.create_default_context()
        self._session = adafruit_requests.Session(self._pool, self._ssl_context)

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "X-APP-KEY": self.app_key,
        }

    def _request(self, method, path, payload=None):
        url = "{}{}".format(self.base_url, path)
        response = None
        try:
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout_s,
                )
            except TypeError:
                response = self._session.request(
                    method=method,
                    url=url,
                    headers=self._headers(),
                    json=payload,
                )

            status = int(getattr(response, "status_code", 0) or 0)
            body = None
            try:
                body = response.json()
            except Exception:
                body = None

            if 200 <= status < 300:
                return {
                    "ok": True,
                    "status_code": status,
                    "data": body,
                    "error_code": None,
                    "error_message": None,
                }

            err_code = "HTTP_{}".format(status)
            err_message = "HTTP {}".format(status)
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict):
                    if err.get("code"):
                        err_code = str(err.get("code"))
                    if err.get("message"):
                        err_message = str(err.get("message"))

            return {
                "ok": False,
                "status_code": status,
                "data": body,
                "error_code": err_code,
                "error_message": err_message,
            }

        except Exception as ex:
            return {
                "ok": False,
                "status_code": 0,
                "data": None,
                "error_code": "NETWORK_ERROR",
                "error_message": str(ex),
            }
        finally:
            try:
                if response is not None:
                    response.close()
            except Exception:
                pass

    def put_interest(self, device_id, interest_blurb):
        payload = {"interest_blurb": interest_blurb}
        return self._request("PUT", "/v1/interests/{}".format(device_id), payload)

    def get_interest(self, device_id):
        return self._request("GET", "/v1/interests/{}".format(device_id), None)

    def post_observe(self, observer_device_id, observations):
        payload = {
            "observer_device_id": observer_device_id,
            "observations": observations,
        }
        return self._request("POST", "/v1/proximity/observe", payload)

    def post_match(self, device_id_a, device_id_b, return_rationale=False):
        payload = {
            "device_id_a": device_id_a,
            "device_id_b": device_id_b,
            "return_rationale": bool(return_rationale),
        }
        return self._request("POST", "/v1/match", payload)
