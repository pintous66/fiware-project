import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

IOT_AGENT_URL = os.getenv('IOT_AGENT_URL', 'http://iot-agent:4041')
ORION_URL = os.getenv('ORION_URL', 'http://fiware-orion:1026')
FIWARE_SERVICE = os.getenv('FIWARE_SERVICE', 'uav')
FIWARE_SERVICEPATH = os.getenv('FIWARE_SERVICEPATH', '/')
APIKEY = os.getenv('APIKEY', 'dronekey')


def log(message: str) -> None:
    print(message, flush=True)


def request_json(method: str, url: str, headers: dict[str, str] | None = None, payload: Any | None = None) -> tuple[int, str]:
    request_headers = headers.copy() if headers else {}
    data = None
    if payload is not None:
        request_headers.setdefault('Content-Type', 'application/json')
        data = json.dumps(payload).encode('utf-8')

    request = Request(url, data=data, headers=request_headers, method=method)

    try:
        with urlopen(request, timeout=10) as response:
            body = response.read().decode('utf-8', errors='replace')
            return response.status, body
    except HTTPError as error:
        body = error.read().decode('utf-8', errors='replace')
        return error.code, body


def get_json(url: str, headers: dict[str, str]) -> tuple[int, str]:
    return request_json('GET', url, headers=headers)


def post_json(url: str, headers: dict[str, str], payload: Any) -> tuple[int, str]:
    return request_json('POST', url, headers=headers, payload=payload)


def wait_for_iot_agent() -> None:
    log('Waiting for the IoT Agent...')

    while True:
        try:
            status, _ = get_json(f'{IOT_AGENT_URL}/iot/about', headers={})
            if 200 <= status < 300:
                break
        except URLError:
            pass

        log('IoT Agent is not ready yet...')
        time.sleep(2)

    log('IoT Agent is available.')


def service_headers() -> dict[str, str]:
    return {
        'fiware-service': FIWARE_SERVICE,
        'fiware-servicepath': FIWARE_SERVICEPATH,
    }


def ensure_iot_service() -> None:
    log('Checking IoT service...')
    status, body = get_json(f'{IOT_AGENT_URL}/iot/services', headers=service_headers())
    if status < 200 or status >= 300:
        raise RuntimeError(f'Failed to query IoT services: HTTP {status} - {body}')

    if f'"apikey":"{APIKEY}"' in body:
        log('IoT service already exists.')
        return

    log('IoT service does not exist. Creating it...')
    payload = {
        'services': [
            {
                'apikey': APIKEY,
                'cbroker': ORION_URL,
                'entity_type': 'Drone',
                'resource': '/iot/json',
            }
        ]
    }

    status, body = post_json(f'{IOT_AGENT_URL}/iot/services', headers=service_headers(), payload=payload)
    if status < 200 or status >= 300:
        raise RuntimeError(f'Failed to create IoT service: HTTP {status} - {body}')

    log('IoT service created.')


def ensure_device(device_id: str, entity_name: str) -> None:
    log('')
    log(f'Checking device {device_id}...')

    status, body = get_json(f'{IOT_AGENT_URL}/iot/devices', headers=service_headers())
    if status < 200 or status >= 300:
        raise RuntimeError(f'Failed to query IoT devices: HTTP {status} - {body}')

    if f'"device_id":"{device_id}"' in body:
        log(f'Device {device_id} already exists.')
        return

    log(f'Device {device_id} does not exist. Creating it...')
    payload = {
        'devices': [
            {
                'device_id': device_id,
                'apikey': APIKEY,
                'entity_name': entity_name,
                'entity_type': 'Drone',
                'transport': 'MQTT',
                'attributes': [
                    {'object_id': 'b', 'name': 'battery', 'type': 'Number'},
                    {'object_id': 'x', 'name': 'x', 'type': 'Number'},
                    {'object_id': 'y', 'name': 'y', 'type': 'Number'},
                    {'object_id': 's', 'name': 'status', 'type': 'Text'},
                ],
                'commands': [
                    {'name': 'return_to_base', 'type': 'command'}
                ],
            }
        ]
    }

    status, body = post_json(f'{IOT_AGENT_URL}/iot/devices', headers=service_headers(), payload=payload)
    if status < 200 or status >= 300:
        raise RuntimeError(f'Failed to create device {device_id}: HTTP {status} - {body}')

    log(f'Device {device_id} created.')


def main() -> None:
    wait_for_iot_agent()
    ensure_iot_service()
    ensure_device('drone1', 'Drone1')
    ensure_device('drone2', 'Drone2')

    log('')
    log('Provisioning completed.')
    log('')
    log('Registered devices:')
    status, body = get_json(f'{IOT_AGENT_URL}/iot/devices', headers=service_headers())
    if status < 200 or status >= 300:
        raise RuntimeError(f'Failed to list registered devices: HTTP {status} - {body}')

    log(body)
    log('')
    log('System ready to receive telemetry on the following topics:')
    log(f'/json/{APIKEY}/drone1/attrs')
    log(f'/json/{APIKEY}/drone2/attrs')


if __name__ == '__main__':
    main()
