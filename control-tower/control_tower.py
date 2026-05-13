import os
import time
from typing import Any

import requests
from flask import Flask, jsonify, request


app = Flask(__name__)


ORION_URL = os.getenv("ORION_URL", "http://localhost:1026")
FIWARE_SERVICE = os.getenv("FIWARE_SERVICE", "uav")
FIWARE_SERVICEPATH = os.getenv("FIWARE_SERVICEPATH", "/")
DRONE_ENTITIES = [
    drone.strip()
    for drone in os.getenv("DRONE_ENTITIES", "Drone1,Drone2").split(",")
    if drone.strip()
]
LOW_BATTERY_THRESHOLD = float(os.getenv("LOW_BATTERY_THRESHOLD", "20"))
NOTIFICATION_URL = os.getenv(
    "NOTIFICATION_URL",
    "http://control-tower:8080/notify",
)

HEADERS = {
    "fiware-service": FIWARE_SERVICE,
    "fiware-servicepath": FIWARE_SERVICEPATH,
}

JSON_HEADERS = {
    **HEADERS,
    "Content-Type": "application/json",
}

command_sent: set[str] = set()


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def get_attr_value(entity: dict[str, Any], attr_name: str) -> Any:
    attr = entity.get(attr_name)

    if not isinstance(attr, dict):
        return None

    return attr.get("value")


def send_return_to_base(entity_id: str) -> None:
    url = f"{ORION_URL}/v2/entities/{entity_id}/attrs"

    payload = {
        "return_to_base": {
            "type": "command",
            "value": "",
        }
    }

    response = requests.patch(
        url,
        headers=JSON_HEADERS,
        json=payload,
        timeout=5,
    )

    response.raise_for_status()

    log(f"[{entity_id}] Comando enviado ao Orion: return_to_base")


def evaluate_drone(entity: dict[str, Any]) -> None:
    entity_id = entity.get("id", "unknown")

    battery = get_attr_value(entity, "battery")
    x = get_attr_value(entity, "x")
    y = get_attr_value(entity, "y")
    status = get_attr_value(entity, "status")

    log(
        f"[{entity_id}] Notificação recebida: "
        f"battery={battery}, x={x}, y={y}, status={status}"
    )

    if battery is None:
        log(f"[{entity_id}] Decisão: nada. Bateria ainda não disponível.")
        return

    if status is None:
        log(f"[{entity_id}] Decisão: nada. Estado ainda não disponível.")
        return

    try:
        battery_value = float(battery)
    except ValueError:
        log(f"[{entity_id}] Decisão: nada. Valor de bateria inválido: {battery}")
        return

    if status == "charging":
        command_sent.discard(entity_id)
        log(f"[{entity_id}] Decisão: nada. Drone está a carregar.")
        return

    if status != "flying":
        log(f"[{entity_id}] Decisão: nada. Estado desconhecido: {status}")
        return

    if battery_value < LOW_BATTERY_THRESHOLD:
        if entity_id in command_sent:
            log(
                f"[{entity_id}] Decisão: nada. "
                f"Comando de regresso já tinha sido enviado."
            )
            return

        log(
            f"[{entity_id}] Decisão: mandar voltar à base. "
            f"Bateria {battery_value:.1f}% < {LOW_BATTERY_THRESHOLD:.1f}%."
        )

        send_return_to_base(entity_id)
        command_sent.add(entity_id)
        return

    if entity_id in command_sent:
        command_sent.discard(entity_id)

    log(
        f"[{entity_id}] Decisão: nada. "
        f"Bateria suficiente ({battery_value:.1f}%)."
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/notify", methods=["POST"])
def notify():
    notification = request.get_json(silent=True)

    if not notification:
        log("Notificação inválida recebida.")
        return jsonify({"error": "Invalid notification"}), 400

    subscription_id = notification.get("subscriptionId")
    data = notification.get("data", [])

    log(
        f"Notificação do Orion recebida. "
        f"subscriptionId={subscription_id}, entities={len(data)}"
    )

    for entity in data:
        evaluate_drone(entity)

    return jsonify({"status": "received"}), 200


def delete_existing_subscriptions() -> None:
    response = requests.get(
        f"{ORION_URL}/v2/subscriptions",
        headers=HEADERS,
        timeout=5,
    )

    response.raise_for_status()

    subscriptions = response.json()

    for subscription in subscriptions:
        description = subscription.get("description", "")

        if description == "Notify Control Tower about drone telemetry":
            subscription_id = subscription.get("id")

            if not subscription_id:
                continue

            delete_response = requests.delete(
                f"{ORION_URL}/v2/subscriptions/{subscription_id}",
                headers=HEADERS,
                timeout=5,
            )

            delete_response.raise_for_status()
            log(f"Subscrição antiga removida: {subscription_id}")


def create_subscription() -> None:
    watched_attrs = ["battery", "x", "y", "status"]

    entities = [
        {
            "id": entity_id,
            "type": "Drone",
        }
        for entity_id in DRONE_ENTITIES
    ]

    payload = {
        "description": "Notify Control Tower about drone telemetry",
        "subject": {
            "entities": entities,
            "condition": {
                "attrs": watched_attrs
            },
        },
        "notification": {
            "http": {
                "url": NOTIFICATION_URL
            },
            "attrs": watched_attrs,
        },
    }

    response = requests.post(
        f"{ORION_URL}/v2/subscriptions",
        headers=JSON_HEADERS,
        json=payload,
        timeout=5,
    )

    response.raise_for_status()

    subscription_id = response.headers.get("Location", "unknown")
    log(f"Subscrição criada no Orion: {subscription_id}")


def wait_for_orion() -> None:
    log("A aguardar pelo Orion...")

    while True:
        try:
            response = requests.get(
                f"{ORION_URL}/version",
                timeout=3,
            )

            if response.status_code == 200:
                log("Orion disponível.")
                return

        except requests.RequestException:
            pass

        time.sleep(2)


def startup() -> None:
    log("Control Tower iniciada em modo subscrição.")
    log(f"Orion URL: {ORION_URL}")
    log(f"Notification URL: {NOTIFICATION_URL}")
    log(f"Drones monitorizados: {', '.join(DRONE_ENTITIES)}")
    log(f"Limite de bateria baixa: {LOW_BATTERY_THRESHOLD}%")

    wait_for_orion()
    delete_existing_subscriptions()
    create_subscription()


if __name__ == "__main__":
    startup()

    app.run(
        host="0.0.0.0",
        port=8080,
    )
