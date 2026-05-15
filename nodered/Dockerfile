FROM nodered/node-red:latest

WORKDIR /data

COPY package.json /data/package.json
RUN npm install --no-update-notifier --no-fund --omit=dev

COPY flows.json /data/flows.json

WORKDIR /usr/src/node-red