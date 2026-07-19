# MPMT — Multiparty Private Membership Test [![status](https://img.shields.io/badge/status-WIP-orange)]()

A RESEARCH PROTOTYPE Implementation of a MPMT scheme using C++ & Python: enabling parties to perform private membership test while keeping both query elements and multi-party set data confidential.

## Platform

- OS: Linux (tested on WSL2, Ubuntu 22.04)
- Architecture: x86-64 (AMD64)

## Dependencies

### Build Tools

| Tool | Version (tested) |
|------|-----------------|
| Clang | 15.0.0 |
| CMake | >= 3.25 |
| Ninja | >= 1.10 |

### C++ Libraries

| Library | Version (tested) |
|---------|-----------------|
| [emp-tool](https://github.com/emp-toolkit/emp-tool) | >=0.3.0 |
| [emp-ot](https://github.com/emp-toolkit/emp-ot) | >=0.3.0 |
| [emp-sh2pc](https://github.com/emp-toolkit/emp-sh2pc) | >=0.3.0 |
| libsodium | 1.0.18 (Ubuntu package: libsodium23) |
| [nlohmann/json](https://github.com/nlohmann/json) | 3.10.5 |

### Python Packages for Prot.

| Package | Version (tested) |
|---------|-----------------|
| Python | >= 3.10 (3.10.12) |
| nanobind | >= 2.13.0 |
| requests | >= 2.25 |
| cloudpickle | >= 3.1 |
| pytest | >= 9.1 |
| pytest-benchmark | >= 5.2 |

### Python Packages for Apps.

| Package | Version (tested) |
| Flask | (any recent) |

## Installation

```bash
pip install -e . -v
```

## Usage

See [tutorial/](./tutorial/) for Jupyter notebooks organised by layer.
They cover every class and method with runnable examples where possible,
and annotated code snippets for multi-party workflows.

## Roadmap

This project is a research prototype.

- [x] Core protocols: aggregation (join/update/quit/aggregate), query (dot product)
- [ ] More comprehensive testing and benchmarks
- [ ] Application service

## Disclaimer

This is academic research code. It has NOT been audited for production
security. Use at your own risk. No warranty, express or implied.

No license is granted at this time. The code is publicly visible for
review and research purposes.

## Reference

- ABY3: Mohassel and Rindal, *ABY^3: A Mixed Protocol Framework for Machine Learning*. CCS 2018. https://eprint.iacr.org/2018/403
- BGI16 DPF: Boyle, Gilboa, and Ishai, *Function Secret Sharing: Improvements and Extensions*. CCS 2016. https://eprint.iacr.org/2016/622
- emp-toolkit: https://github.com/emp-toolkit
- ``rvector.hpp`` SIMD packing inspired by [lemire/simdcomp](https://github.com/lemire/simdcomp)
