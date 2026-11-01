# EPANET example networks

`Net1.inp`, `Net2.inp` and `Net3.inp` are EPANET's example networks as bundled with WNTR 1.5.0
(`wntr/library/networks/`, Revised BSD licence). They are not byte-for-byte the copies in
OWA EPANET 2.3.5's `example-networks/` (Windows line endings, and the newer tank `Overflow`
column). `tests/integration/test_roundtrip.py` sends each through GIS layers and back.
