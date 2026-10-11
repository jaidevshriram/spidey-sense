# Vendored: DataFeel Python SDK

`datafeel-python/` is DataFeel's devkit Python SDK (`Datafeel-devkit-samples/Python` from the
DataFeel-devkit-quickstart download), **MIT license** per its `setup.py` (`license='MIT license'`).
Copyright © DataFeel. It's vendored unchanged so the repo installs without the original zip:
`pip install -e haptics/vendor/datafeel-python`.

The Bluetooth protocol in `haptics/dots/ble.py` (Nordic UART service 6e400001/2/3 carrying Modbus RTU)
was identified from DataFeel's public NuGet packages `Datafeel.NET.BLE` and `CallAndResponse.Transport.Ble`.
