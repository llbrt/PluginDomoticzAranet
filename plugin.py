#!/usr/bin/env python3
"""
<plugin key="AranetScanner" name="Aranet Sensors" author="llbrt" version="1.0.0"
        externallink="https://github.com/llbrt/PluginDomoticzAranet">
    <description>
        <h2>Aranet Sensors</h2><br/>
        Scans for nearby Aranet4 / Aranet2 / AranetRn (Radon) / Aranet&#9762; (Radiation)
        devices over Bluetooth LE and decodes their current readings directly
        from the advertisement's manufacturer data (via aranet4.client.Aranet4Advertisement)
        -- no pairing or GATT connection is made. One DomoticzEx device is
        created per discovered sensor (keyed by its Bluetooth MAC address),
        with some Units depending on the values reported by the device like CO2, Temperature,
        Humidity, Pressure, Battery, Status, Radiation dose rate, Radon concentration.

        Requires the "aranet4" Python provided with this plugin.py and the module "bleak".
    </description>
    <params>
        <param field="Mode1" label="Scan duration (s)" width="60px" required="true" default="8"/>
        <param field="Mode2" label="Heartbeat interval (s)" width="60px" required="true" default="30"/>
        <param field="Mode6" label="Debug logging" width="100px">
            <options>
                <option label="On" value="Debug"/>
                <option label="Off" value="Normal" default="true"/>
            </options>
        </param>
    </params>
</plugin>
"""

import asyncio

import DomoticzEx as Domoticz
from bleak import BleakScanner

from aranet4.client import Aranet4, Aranet4Advertisement, AranetType, Color

# --- BLE service UUIDs, straight from aranet4.client so they can never
# drift out of sync with the library doing the actual decoding.
SERVICE_UUID_NEW = Aranet4.SERVICE_SAF_TEHNIKA        # v1.2.0 and later
SERVICE_UUID_OLD = Aranet4.SERVICE_SAF_TEHNIKA_OLD    # until v1.2.0

DEFAULT_SCAN_DURATION = 8
DEFAULT_HEARTBEAT = 30

# --- Domoticz plugin -------------------------------------------------------

class BasePlugin:
    def __init__(self):
        self.scan_duration = DEFAULT_SCAN_DURATION

    def onStart(self):
        if Parameters.get("Mode6") == "Debug":
            Domoticz.Debugging(1)
            Domoticz.Debug("Debug mode enabled")

        try:
            self.scan_duration = int(Parameters.get("Mode1", DEFAULT_SCAN_DURATION))
        except ValueError:
            self.scan_duration = DEFAULT_SCAN_DURATION

        try:
            heartbeat = int(Parameters.get("Mode2", DEFAULT_HEARTBEAT))
        except ValueError:
            heartbeat = DEFAULT_HEARTBEAT

        Domoticz.Heartbeat(heartbeat)
        Domoticz.Log("Aranet Sensors started")
        self.scan_and_update()

    def onStop(self):
        Domoticz.Log("Aranet Sensors stopped")

    def onHeartbeat(self):
        #self.scan_and_update()
        pass

    # -- scanning -----------------------------------------------------
    def scan_and_update(self):
        try:
            asyncio.run(self._scan_and_update_async())
        except Exception as exc:
            Domoticz.Error(f"Aranet scan failed: {exc}")

    async def _scan_and_update_async(self):
        found = {}

        def _on_detect(device, advertisement_data):
            self._process_advertisement(found, device, advertisement_data)

        scanner = BleakScanner(
            detection_callback=_on_detect,
            service_uuids=[SERVICE_UUID_NEW, SERVICE_UUID_OLD],
        )
        await scanner.start()
        await asyncio.sleep(self.scan_duration)
        await scanner.stop()

        Domoticz.Debug(f"Aranet scan finished, processed {len(found)} device(s)")

    def _process_advertisement(self, found, device, advertisement_data):
        if device.address in found:
            return
        found[device.address] = advertisement_data

        # Aranet4Advertisement does the full decode itself (basic info +
        # type detection + all fields). Its .rssi is populated too, but we
        # never read it since we don't need/display RSSI here.
        advertisement = Aranet4Advertisement(device, advertisement_data)
        reading = advertisement.readings
        if reading is None or reading.type == AranetType.UNKNOWN:
            return

        self._ensure_device(device, reading)
        self._update_units(device, reading)

    # -- Domoticz device/unit management -------------------------------
    def _ensure_device(self, device, reading):
        mac = device.address
        base_name = device.name or mac

        if mac not in Devices:
            Domoticz.Log(f"New Aranet device found: {base_name} ({mac}) - {reading.type.name}")

            if reading.type == AranetType.ARANET4:
                Domoticz.Device(DeviceID=mac)
                Domoticz.Unit(Name=base_name, Unit=1, TypeName="Temp+Hum+Baro", Subtype=1, DeviceID=mac, Used=1).Create()
                Domoticz.Unit(Name=base_name, Unit=2, TypeName="Alert", DeviceID=mac, Used=1).Create()
                Domoticz.Unit(Name=base_name, Unit=3, TypeName="Air Quality", DeviceID=mac, Used=1).Create()

            elif reading.type == AranetType.ARANET2:
                Domoticz.Device(DeviceID=mac)
                Domoticz.Unit(Name=base_name, Unit=1, TypeName="Temp+Hum", Subtype=1, DeviceID=mac, Used=1).Create()

            elif reading.type == AranetType.ARANET_RADIATION:
                # TODO: decode status (green, yellow, red) and add alert unit
                Domoticz.Device(DeviceID=mac)
                Domoticz.Unit(Name=base_name, Unit=3, TypeName="Custom", DeviceID=mac, Used=1, Options={"Custom": "1;µSv/h"}).Create()

            elif reading.type == AranetType.ARANET_RADON:
                Domoticz.Device(DeviceID=mac)
                Domoticz.Unit(Name=base_name, Unit=1, TypeName="Temp+Hum+Baro", Subtype=1, DeviceID=mac, Used=1).Create()
                Domoticz.Unit(Name=base_name, Unit=2, TypeName="Alert", DeviceID=mac, Used=1).Create()
                Domoticz.Unit(Name=base_name, Unit=3, TypeName="Custom", DeviceID=mac, Used=1, Options={"Custom": "1;Bq/m³"}).Create()

    def _update_units(self, device, reading):
        mac = device.address
        base_name = device.name or mac

        if mac in Devices:
            Domoticz.Debug(f"Update Aranet device: {base_name} ({mac}) - {reading.type.name}")

            if reading.type == AranetType.ARANET4:
                temperature = reading.temperature
                humidity = reading.humidity
                pressure = reading.pressure
                battery_level = reading.battery

                humidity_status = self._humidity_status(temperature, humidity)
                Devices[mac].Units[1].nValue = 0
                Devices[mac].Units[1].sValue = f"{temperature:.1f};{humidity:.0f};{humidity_status:.0f};{pressure:.0f};7"
                Devices[mac].Units[1].BatteryLevel = battery_level
                Devices[mac].Units[1].Update()

                self._update_alert(Devices[mac].Units[2], reading.status)

                Devices[mac].Units[3].nValue = reading.co2
                Devices[mac].Units[3].Update()

            elif reading.type == AranetType.ARANET2:
                temperature = reading.temperature
                humidity = reading.humidity
                battery_level = reading.battery

                humidity_status = self._humidity_status(temperature, humidity)
                Devices[mac].Units[1].nValue = 0
                Devices[mac].Units[1].sValue = f"{temperature:.1f};{humidity:.0f};{humidity_status:.0f}"
                Devices[mac].Units[1].BatteryLevel = battery_level
                Devices[mac].Units[1].Update()

            elif reading.type == AranetType.ARANET_RADIATION:
                # TODO: decode status (green, yellow, red)
                radiation_rate = reading.radiation_rate
                battery_level = reading.battery

                Devices[mac].Units[3].nValue = int(radiation_rate / 1000)
                Devices[mac].Units[3].sValue = f"{radiation_rate / 1000:.02f}"
                Devices[mac].Units[3].BatteryLevel = battery_level
                Devices[mac].Units[3].Update()

            elif reading.type == AranetType.ARANET_RADON:
                radon_concentration = reading.radon_concentration
                temperature = reading.temperature
                humidity = reading.humidity
                pressure = reading.pressure
                battery_level = reading.battery

                humidity_status = self._humidity_status(temperature, humidity)
                Devices[mac].Units[1].nValue = 0
                Devices[mac].Units[1].sValue = f"{temperature:.1f};{humidity:.0f};{humidity_status:.0f};{pressure:.0f};7"
                Devices[mac].Units[1].BatteryLevel = battery_level
                Devices[mac].Units[1].Update()

                self._update_alert(Devices[mac].Units[2], reading.status)

                Devices[mac].Units[3].nValue = radon_concentration
                Devices[mac].Units[3].sValue = str(radon_concentration)
                Devices[mac].Units[3].BatteryLevel = battery_level
                Devices[mac].Units[3].Update()

    def _humidity_status(self, temperature, humidity):
        if humidity <= 30:
            return 2          # DRY
        elif humidity >= 70:
            return 3          # WET
        elif humidity >= 35 and humidity <= 65 and temperature >= 19 and temperature <= 26:
            return 1          # COMFORTABLE
        return 0              # NORMAL

    def _update_alert(self, unit, status):
        unit.sValue = status.name
        match status:
            case Color.GREEN:
                unit.nValue = 1
            case Color.YELLOW:
                unit.nValue = 2
            case Color.RED:
                unit.nValue = 4
            case _:
                unit.nValue = 0
        unit.Update()

global _plugin
_plugin = BasePlugin()


def onStart():
    global _plugin
    _plugin.onStart()


def onStop():
    global _plugin
    _plugin.onStop()


def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()
