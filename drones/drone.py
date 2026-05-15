import json
import os
import random
import time
from dataclasses import dataclass

import paho.mqtt.client as mqtt


@dataclass
class DroneConfig:
    drone_id: str
    api_key: str
    mqtt_host: str
    mqtt_port: int
    movement_interval: float
    movement_step: float
    battery_loss_per_move: float
    charge_rate: float


class DroneSimulator:
    MIN_COORD = 0.0
    MAX_COORD = 100.0

    STATUS_FLYING = "flying"
    STATUS_CHARGING = "charging"

    COMMAND_RETURN_TO_BASE = "return_to_base"

    def __init__(self, config: DroneConfig):
        self.config = config

        self.x = 0.0
        self.y = 0.0
        self.battery = random.uniform(50.0, 100.0)
        self.status = self.STATUS_FLYING

        self.returning_to_base = False

        self.attrs_topic = f"/json/{self.config.api_key}/{self.config.drone_id}/attrs"

        # Expected topic in this setup, because telemetry uses /json/...
        self.command_topics = [
            f"/json/{self.config.api_key}/{self.config.drone_id}/cmd",

            # Alternative topics for compatibility with some IoT Agent JSON configurations.
            f"json/{self.config.api_key}/{self.config.drone_id}/cmd",
            f"/{self.config.api_key}/{self.config.drone_id}/cmd",
            f"{self.config.api_key}/{self.config.drone_id}/cmd",
        ]

        self.command_result_topics = [
            f"/json/{self.config.api_key}/{self.config.drone_id}/cmdexe",
            f"json/{self.config.api_key}/{self.config.drone_id}/cmdexe",
            f"/{self.config.api_key}/{self.config.drone_id}/cmdexe",
            f"{self.config.api_key}/{self.config.drone_id}/cmdexe",
        ]

        self.client = mqtt.Client(client_id=f"{self.config.drone_id}-simulator")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def connect(self) -> None:
        print(
            f"[{self.config.drone_id}] Connecting to MQTT "
            f"{self.config.mqtt_host}:{self.config.mqtt_port}"
        )

        self.client.connect(
            self.config.mqtt_host,
            self.config.mqtt_port,
            keepalive=60,
        )

        self.client.loop_start()

    def on_connect(self, client, userdata, flags, rc) -> None:
        if rc != 0:
            print(f"[{self.config.drone_id}] Error connecting to MQTT. Code: {rc}")
            return

        print(f"[{self.config.drone_id}] Connected to MQTT.")

        for topic in self.command_topics:
            client.subscribe(topic)
            print(f"[{self.config.drone_id}] Listening for commands on: {topic}")

        self.publish_telemetry()

    def on_message(self, client, userdata, msg) -> None:
        payload = msg.payload.decode("utf-8", errors="replace")

        print(
            f"[{self.config.drone_id}] Message received on topic "
            f"{msg.topic}: {payload}"
        )

        command_name = self.extract_command_name(payload)

        if command_name == self.COMMAND_RETURN_TO_BASE:
            self.handle_return_to_base_command()
        else:
            print(
                f"[{self.config.drone_id}] Ignored or unknown command: "
                f"{command_name}"
            )

    def extract_command_name(self, payload: str) -> str | None:
        """
        The IoT Agent JSON can send MQTT commands with slightly different payloads,
        depending on the version/configuration.

        Possible examples:
        {"return_to_base": ""}
        {"return_to_base": true}
        return_to_base
        """

        payload = payload.strip()

        if not payload:
            return None

        try:
            data = json.loads(payload)

            if isinstance(data, dict):
                if self.COMMAND_RETURN_TO_BASE in data:
                    return self.COMMAND_RETURN_TO_BASE

                # If it arrives as {"command": "return_to_base"}
                command = data.get("command")
                if command == self.COMMAND_RETURN_TO_BASE:
                    return self.COMMAND_RETURN_TO_BASE

                # If it arrives as {"name": "return_to_base"}
                name = data.get("name")
                if name == self.COMMAND_RETURN_TO_BASE:
                    return self.COMMAND_RETURN_TO_BASE

            if isinstance(data, str):
                return data

        except json.JSONDecodeError:
            return payload

        return None

    def handle_return_to_base_command(self) -> None:
        print(f"[{self.config.drone_id}] Command received: return to base.")

        self.returning_to_base = True
        self.status = self.STATUS_FLYING

        self.publish_command_result(
            command_name=self.COMMAND_RETURN_TO_BASE,
            result="accepted",
        )

    def publish_command_result(self, command_name: str, result: str) -> None:
        payload = json.dumps(
            {
                command_name: result
            }
        )

        # Publishes to the first topic, which matches the /json/... format.
        topic = self.command_result_topics[0]

        self.client.publish(topic, payload)
        print(
            f"[{self.config.drone_id}] Command result published on "
            f"{topic}: {payload}"
        )

    def publish_telemetry(self) -> None:
        payload = {
            "b": round(self.battery, 1),
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "s": self.status,
        }

        self.client.publish(self.attrs_topic, json.dumps(payload))

        print(
            f"[{self.config.drone_id}] Telemetry sent to {self.attrs_topic}: "
            f"{payload}"
        )

    def run(self) -> None:
        self.connect()

        try:
            while True:
                self.tick()
                time.sleep(self.config.movement_interval)

        except KeyboardInterrupt:
            print(f"[{self.config.drone_id}] Shutting down simulator...")

        finally:
            self.client.loop_stop()
            self.client.disconnect()

    def tick(self) -> None:
        if self.status == self.STATUS_CHARGING:
            self.charge()
            self.publish_telemetry()
            return

        if self.returning_to_base:
            self.move_towards_base()
        else:
            self.random_move()

        self.consume_battery()
        self.publish_telemetry()

    def random_move(self) -> None:
        axis = random.choice(["x", "y"])
        direction = random.choice([-1, 1])
        movement = direction * self.config.movement_step

        old_x = self.x
        old_y = self.y

        if axis == "x":
            self.x = self.clamp(self.x + movement)
        else:
            self.y = self.clamp(self.y + movement)

        print(
            f"[{self.config.drone_id}] Random move: "
            f"({old_x:.1f}, {old_y:.1f}) -> ({self.x:.1f}, {self.y:.1f})"
        )

    def move_towards_base(self) -> None:
        old_x = self.x
        old_y = self.y

        if self.x > 0:
            self.x = max(0.0, self.x - self.config.movement_step)
        elif self.y > 0:
            self.y = max(0.0, self.y - self.config.movement_step)

        print(
            f"[{self.config.drone_id}] Returning to base: "
            f"({old_x:.1f}, {old_y:.1f}) -> ({self.x:.1f}, {self.y:.1f})"
        )

        if self.x == 0.0 and self.y == 0.0:
            print(f"[{self.config.drone_id}] Reached base. Charging.")
            self.status = self.STATUS_CHARGING
            self.returning_to_base = False

    def consume_battery(self) -> None:
        self.battery = max(
            0.0,
            self.battery - self.config.battery_loss_per_move,
        )

    def charge(self) -> None:
        old_battery = self.battery

        self.battery = min(
            100.0,
            self.battery + self.config.charge_rate,
        )

        print(
            f"[{self.config.drone_id}] Charging: "
            f"{old_battery:.1f}% -> {self.battery:.1f}%"
        )

        if self.battery >= 80.0:
            self.battery = 80.0
            self.status = self.STATUS_FLYING
            print(
                f"[{self.config.drone_id}] Charge reached 80%. "
                f"Returning to flying state."
            )

    def clamp(self, value: float) -> float:
        return max(self.MIN_COORD, min(self.MAX_COORD, value))


def load_config() -> DroneConfig:
    return DroneConfig(
        drone_id=os.getenv("DRONE_ID", "drone1"),
        api_key=os.getenv("IOTA_API_KEY", "dronekey"),
        mqtt_host=os.getenv("MQTT_HOST", "localhost"),
        mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
        movement_interval=float(os.getenv("MOVEMENT_INTERVAL", "2")),
        movement_step=float(os.getenv("MOVEMENT_STEP", "1")),
        battery_loss_per_move=float(os.getenv("BATTERY_LOSS_PER_MOVE", "0.1")) + random.uniform(0, 1),
        charge_rate=float(os.getenv("CHARGE_RATE", "2")),
    )


if __name__ == "__main__":
    start_delay = float(os.getenv("START_DELAY", "0"))

    if start_delay > 0:
        print(f"Waiting {start_delay} seconds before starting the drone...")
        time.sleep(start_delay)

    config = load_config()
    drone = DroneSimulator(config)
    drone.run()
    
    
