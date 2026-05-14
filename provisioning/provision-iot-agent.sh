#!/bin/sh

set -e

IOT_AGENT_URL="http://iot-agent:4041"
ORION_URL="http://fiware-orion:1026"

FIWARE_SERVICE="uav"
FIWARE_SERVICEPATH="/"
APIKEY="dronekey"

echo "A aguardar pelo IoT Agent..."

until curl -s -f "$IOT_AGENT_URL/iot/about" > /dev/null; do
  echo "IoT Agent ainda não está pronto..."
  sleep 2
done

echo "IoT Agent disponível."

echo "A verificar serviço IoT..."

SERVICES_RESPONSE=$(curl -s "$IOT_AGENT_URL/iot/services" \
  -H "fiware-service: $FIWARE_SERVICE" \
  -H "fiware-servicepath: $FIWARE_SERVICEPATH")

echo "$SERVICES_RESPONSE" | grep -q "\"apikey\":\"$APIKEY\"" || {
  echo "Serviço IoT não existe. A criar..."

  curl -iX POST "$IOT_AGENT_URL/iot/services" \
    -H "Content-Type: application/json" \
    -H "fiware-service: $FIWARE_SERVICE" \
    -H "fiware-servicepath: $FIWARE_SERVICEPATH" \
    -d "{
      \"services\": [
        {
          \"apikey\": \"$APIKEY\",
          \"cbroker\": \"$ORION_URL\",
          \"entity_type\": \"Drone\",
          \"resource\": \"/iot/json\"
        }
      ]
    }"

  echo "Serviço IoT criado."
}

create_device() {
  DEVICE_ID="$1"
  ENTITY_NAME="$2"

  echo ""
  echo "A verificar device $DEVICE_ID..."

  DEVICES_RESPONSE=$(curl -s "$IOT_AGENT_URL/iot/devices" \
    -H "fiware-service: $FIWARE_SERVICE" \
    -H "fiware-servicepath: $FIWARE_SERVICEPATH")

  echo "$DEVICES_RESPONSE" | grep -q "\"device_id\":\"$DEVICE_ID\"" || {
    echo "Device $DEVICE_ID não existe. A criar..."

    curl -iX POST "$IOT_AGENT_URL/iot/devices" \
      -H "Content-Type: application/json" \
      -H "fiware-service: $FIWARE_SERVICE" \
      -H "fiware-servicepath: $FIWARE_SERVICEPATH" \
      -d "{
        \"devices\": [
          {
            \"device_id\": \"$DEVICE_ID\",
            \"apikey\": \"$APIKEY\",
            \"entity_name\": \"$ENTITY_NAME\",
            \"entity_type\": \"Drone\",
            \"transport\": \"MQTT\",
            \"attributes\": [
              {
                \"object_id\": \"b\",
                \"name\": \"battery\",
                \"type\": \"Number\"
              },
              {
                \"object_id\": \"x\",
                \"name\": \"x\",
                \"type\": \"Number\"
              },
              {
                \"object_id\": \"y\",
                \"name\": \"y\",
                \"type\": \"Number\"
              },
              {
                \"object_id\": \"s\",
                \"name\": \"status\",
                \"type\": \"Text\"
              }
            ],
            \"commands\": [
              {
                \"name\": \"return_to_base\",
                \"type\": \"command\"
              }
            ]
          }
        ]
      }"

    echo "Device $DEVICE_ID criado."
  }
}

create_device "drone1" "Drone1"
create_device "drone2" "Drone2"

echo ""
echo "Provisionamento concluído."

echo ""
echo "Devices registados:"
curl -s "$IOT_AGENT_URL/iot/devices" \
  -H "fiware-service: $FIWARE_SERVICE" \
  -H "fiware-servicepath: $FIWARE_SERVICEPATH"

echo ""
echo ""
echo "Sistema pronto para receber telemetria nos tópicos:"
echo "/json/$APIKEY/drone1/attrs"
echo "/json/$APIKEY/drone2/attrs"