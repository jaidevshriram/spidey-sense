"""Scan for BLE devices and list services of anything that looks like DataFeel / El Jefe."""
import asyncio
from bleak import BleakScanner, BleakClient

async def main():
    found = await BleakScanner.discover(timeout=8.0, return_adv=True)
    hits = []
    for addr, (dev, adv) in found.items():
        name = dev.name or adv.local_name or ""
        print(f"{addr}  rssi={adv.rssi:4}  name={name!r}  uuids={adv.service_uuids}")
        if any(k in name.lower() for k in ("datafeel", "jefe", "dot", "df")):
            hits.append(dev)
    for dev in hits:
        print("\n== services on", dev.name, dev.address)
        async with BleakClient(dev) as c:
            for s in c.services:
                print(" svc", s.uuid, s.description)
                for ch in s.characteristics:
                    print("   chr", ch.uuid, ch.properties)

asyncio.run(main())
