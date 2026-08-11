# Multiwfn Vendor Notice

This directory contains the Linux x86_64 Multiwfn 3.8(dev) runtime shipped as
Willy's default Multiwfn backend. The executable is upstream-provided; its
settings file has only been sanitized to remove machine-specific helper paths.
It is resolved by the central environment registry and executed through the
managed process layer.

Upstream release: Multiwfn 3.8(dev), update date 2025-02-14.
Upstream page: http://sobereva.com/multiwfn/

Keep `LICENSE.txt` with every redistribution. The supplied license permits
Multiwfn as a free component of commercial code and requires the following
citations when Multiwfn is used in published work:

- Tian Lu and Feiwu Chen, Journal of Computational Chemistry 33, 580-592 (2012).
- Tian Lu, Journal of Chemical Physics 161, 082503 (2024).

Do not add examples, MtwfnFld data, graphical assets, or another platform's
binary to this directory without updating `manifest.json`, running the
component smoke tests, and reviewing distribution obligations.
