import os
import time
from datetime import datetime, timezone
from typing import Any

import requests
from flask import Flask, jsonify, request
from pymongo import MongoClient
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError


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
LOGS_DB_URL = os.getenv("LOGS_DB_URL", "mongodb://logs-db:27017")
LOGS_DB_NAME = os.getenv("LOGS_DB_NAME", "control_tower_logs")
LOGS_DB_COLLECTION = os.getenv("LOGS_DB_COLLECTION", "notifications")

HEADERS = {
    "fiware-service": FIWARE_SERVICE,
    "fiware-servicepath": FIWARE_SERVICEPATH,
}

JSON_HEADERS = {
    **HEADERS,
    "Content-Type": "application/json",
}

command_sent: set[str] = set()
logs_collection = None


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def get_attr_value(entity: dict[str, Any], attr_name: str) -> Any:
    attr = entity.get(attr_name)

    if not isinstance(attr, dict):
        return None

    return attr.get("value")


def connect_logs_db() -> None:
    global logs_collection

    client = MongoClient(LOGS_DB_URL, serverSelectionTimeoutMS=3000)
    client.admin.command("ping")
    logs_collection = client[LOGS_DB_NAME][LOGS_DB_COLLECTION]


def wait_for_logs_db() -> None:
    log("Waiting for the logs database...")

    while True:
        try:
            connect_logs_db()
            log("Logs database is available.")
            return
        except (ServerSelectionTimeoutError, PyMongoError):
            time.sleep(2)


def persist_notification(notification: dict[str, Any]) -> None:
    if logs_collection is None:
        log("Logs database not initialized; notification was not persisted.")
        return

    document = {
        "subscription_id": notification.get("subscriptionId"),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "data": notification.get("data", []),
        "raw_notification": notification,
    }

    try:
        logs_collection.insert_one(document)
    except PyMongoError as error:
        log(f"Failed to persist notification: {error}")


def serialize_log_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(entry.get("_id", "")),
        "subscription_id": entry.get("subscription_id"),
        "received_at": entry.get("received_at"),
        "data": entry.get("data", []),
        "raw_notification": entry.get("raw_notification", {}),
    }


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
        f"[{entity_id}] Notification received: "
        f"battery={battery}, x={x}, y={y}, status={status}"
    )

    if battery is None:
        log(f"[{entity_id}] Decision: nothing to do. Battery is not available yet.")
        return

    if status is None:
        log(f"[{entity_id}] Decision: nothing to do. Status is not available yet.")
        return

    try:
        battery_value = float(battery)
    except ValueError:
        log(f"[{entity_id}] Decision: nothing to do. Invalid battery value: {battery}")
        return

    if status == "charging":
        command_sent.discard(entity_id)
        log(f"[{entity_id}] Decision: nothing to do. Drone is charging.")
        return

    if status != "flying":
        log(f"[{entity_id}] Decision: nothing to do. Unknown status: {status}")
        return

    if battery_value < LOW_BATTERY_THRESHOLD:
        if entity_id in command_sent:
            log(
                f"[{entity_id}] Decision: nothing to do. "
                f"Return command was already sent."
            )
            return

        log(
            f"[{entity_id}] Decision: send return-to-base command. "
            f"Battery {battery_value:.1f}% < {LOW_BATTERY_THRESHOLD:.1f}%."
        )

        send_return_to_base(entity_id)
        command_sent.add(entity_id)
        return

    if entity_id in command_sent:
        command_sent.discard(entity_id)

    log(
        f"[{entity_id}] Decision: nothing to do. "
        f"Battery is sufficient ({battery_value:.1f}%)."
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/notify", methods=["POST"])
def notify():
    notification = request.get_json(silent=True)

    if not notification:
        log("Invalid notification received.")
        return jsonify({"error": "Invalid notification"}), 400

    subscription_id = notification.get("subscriptionId")
    data = notification.get("data", [])

    log(
        f"Orion notification received. "
        f"subscriptionId={subscription_id}, entities={len(data)}"
    )

    persist_notification(notification)

    for entity in data:
        evaluate_drone(entity)

    return jsonify({"status": "received"}), 200


@app.route("/logs/<int:count>", methods=["GET"])
def get_logs(count: int):
    if count <= 0:
        return jsonify({"error": "count must be a positive integer"}), 400

    if logs_collection is None:
        return jsonify({"error": "Logs database is not available"}), 503

    try:
        documents = list(
            logs_collection.find().sort("_id", -1).limit(count)
        )
    except PyMongoError as error:
        log(f"Failed to fetch logs: {error}")
        return jsonify({"error": "Failed to fetch logs"}), 500

    return jsonify(
        {
            "count": len(documents),
            "logs": [serialize_log_entry(document) for document in documents],
        }
    ), 200


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
            log(f"Old subscription removed: {subscription_id}")


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
    log(f"Subscription created in Orion: {subscription_id}")


def wait_for_orion() -> None:
    log("Waiting for Orion...")

    while True:
        try:
            response = requests.get(
                f"{ORION_URL}/version",
                timeout=3,
            )

            if response.status_code == 200:
                log("Orion is available.")
                return

        except requests.RequestException:
            pass

        time.sleep(2)


def startup() -> None:
    log("Control Tower started in subscription mode.")
    log(f"Orion URL: {ORION_URL}")
    log(f"Notification URL: {NOTIFICATION_URL}")
    log(f"Monitored drones: {', '.join(DRONE_ENTITIES)}")
    log(f"Low battery threshold: {LOW_BATTERY_THRESHOLD}%")

    wait_for_orion()
    wait_for_logs_db()
    delete_existing_subscriptions()
    create_subscription()


if __name__ == "__main__":
    startup()

    app.run(
        host="0.0.0.0",
        port=8080,
    )
