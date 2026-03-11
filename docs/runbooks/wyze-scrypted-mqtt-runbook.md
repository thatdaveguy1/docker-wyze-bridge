# Wyze Bridge → Scrypted MQTT Motion Runbook

This runbook is written for remote execution over SSH and assumes you already control:
- the `local_docker_wyze_bridge_local` add-on (see `LIVE-DEPLOYMENT.md`).
- a reachable MQTT broker that both Home Assistant and Scrypted can touch.
- the SSH helper scripts (`scripts/ha_ssh.sh`, `.ha_ssh.env`).

## 1. Validate the add-on + broker setup via SSH

1. Check the add-on options and broker settings safely:
   ```sh
   scripts/ha_ssh.sh ha apps info local_docker_wyze_bridge_local --raw-json \
     | python3 -c 'import json,sys
   d=json.load(sys.stdin)["data"]
   o=d.get("options", {})
   safe={
     "slug": d.get("slug"),
     "state": d.get("state"),
     "motion_api": o.get("MOTION_API"),
     "mqtt_host": o.get("MQTT_HOST"),
     "mqtt_topic": o.get("MQTT_TOPIC", "wyzebridge"),
     "filter_names": o.get("FILTER_NAMES"),
   }
   print(json.dumps(safe, indent=2))'
   ```
   Expect `state` to read `started`, `motion_api` to be `true`, and `mqtt_host` to reference your broker.
2. Verify both Scrypted and HA can reach the broker (replace `<broker>`, `<user>`):
   ```sh
   ssh <scrypted-host> "nc -vz <broker> 1883"
   scripts/ha_ssh.sh curl -fsS http://<broker-ip>:1883 >/dev/null
   ```
   Work only with private network hosts. If `nc` or `curl` fail, correct routing before proceeding.

## 2. Configure MQTT motion emission in the Wyze Bridge add-on

1. In the HA UI:
   - Open the add-on options for `local_docker_wyze_bridge_local`.
   - Set `MOTION_API: true` and `MQTT: true`.
   - Supply `MQTT_HOST: <broker-host>:1883` and `MQTT_AUTH: <username>:<password>`.
   - Leave `MOTION_WEBHOOKS` blank.
   - Keep `MQTT_TOPIC` as `wyzebridge` unless you need a different namespace.
2. Apply options → the add-on should auto-restart; confirm with:
   ```sh
   scripts/ha_ssh.sh ha apps restart local_docker_wyze_bridge_local
   scripts/ha_ssh.sh ha apps logs local_docker_wyze_bridge_local | grep -E 'MQTT|API Motion'
   ```
   Look for lines like `Connecting to mqtt://…` and `API Motion Events Enabled`.

## 3. Probe live MQTT topics

1. From the Scrypted host, subscribe to the expected topics:
   ```sh
   mosquitto_sub -h <broker> -p 1883 -u '<user>' -P '<pass>' -v \
     -t 'wyzebridge/+/motion' -t 'wyzebridge/+/motion_ts'
   ```
2. Trigger motion on one camera (e.g., `garage`).
3. Validate you see payloads like:
   ```text
   wyzebridge/garage/motion 1
   wyzebridge/garage/motion_ts 1705...
   wyzebridge/garage/motion 2
   ```
4. If you need slug names, refer to `home_assistant/DOCS.md`, `FILTER_NAMES`, or the broker payload for `wyzebridge/<slug>/...`. Slugs follow `lower-case`, spaces → `-`.

## 4. Build Scrypted MQTT motion devices (per camera)

1. In Scrypted’s plugin store:
   - Install `@scrypted/mqtt`.
   - Use it as an MQTT client pointed at your existing broker rather than trying to build a custom webhook receiver.
2. In Scrypted’s MQTT plugin:
   - Add a new custom MQTT handler (name it `<camera>-wyze-motion`).
   - Set `Subscription URL` to:
     ```text
     mqtt://<user>:<pass>@<broker>/wyzebridge/<camera-slug>/
     ```
     (include credentials even if the plugin already has them; this avoids the default-auth bug in issue #1768).
   - Use this handler script:
     ```javascript
     mqtt.subscribe({
       'motion': value => {
         const text = String(value?.text ?? value ?? '').trim().toLowerCase();
         console.log('motion topic payload:', text);
         device.motionDetected = text === '1';
       },
     });
     mqtt.handleTypes(ScryptedInterface.MotionSensor);
     ```
3. Save and confirm `device.motionDetected` toggles when your MQTT client reports `1`/`2`.

## 5. Attach motion sensor to camera

1. In Scrypted, open the camera details → `Extensions` tab.
2. Enable `Custom Motion Sensor` extension and click its config tab.
3. Choose the `motion` device you just created (e.g., `Garage Wyze Motion`).
4. Save. Delay a few seconds for Scrypted to apply changes.

## 6. Final verification

1. Trigger motion and confirm:
   - MQTT handler logs show `motionDetected` toggling.
   - Camera’s own motion indicator in Scrypted turns on.
   - If you rely on recordings/HomeKit, confirm the motion event triggered recording or HomeKit notifications.
2. For additional sensors, repeat steps 4–6 per slug.

## Troubleshooting notes

- If MQTT messages appear but the Scrypted device does not flip, double-check that the handler’s `Subscription URL` includes credentials, and review Scrypted logs (`Details > Logs`) for MQTT errors.
- If camera motion never triggers after enabling `Custom Motion Sensor`, reopen the extension, re-select the motion device, and save again.
- To merge Wyze motion with another sensor later, use a Scrypted Device Group rather than the extension.

## Suggested next steps

1. Optionally document the finalized topic-to-camera mapping for reference.
2. Add this runbook reference to `tasks/todo.md` or a live deployment checklist before handing off.
