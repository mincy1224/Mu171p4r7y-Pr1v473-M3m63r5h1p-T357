//  @author  mincy
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>

#include <cstdint>
#include <sodium/randombytes.h>

#include "common.hpp"
#include "bind_common.hpp"

namespace nb = nanobind;
using namespace scucse::crypto;
using bind::_toBytes;

// ———  module entry  ———

void bind_util(nb::module_& m)
{
    using namespace nb::literals;

    // ——— cryptographic randomness ———

    m.def(
        "ring_rand",
        [](uint64_t ell) -> uint32_t { return ringRand(ell); },
        "ell"_a,
        "Cryptographically secure random element in Z_{2^ell}, 1 ≤ ell ≤ 31."
    );

    m.def(
        "get_key_128bits",
        []() -> nb::bytes {
            uint8_t key[16];
            randombytes_buf(key, sizeof(key));
            return nb::bytes(reinterpret_cast<const char*>(key), sizeof(key));
        },
        "Generate a cryptographically secure random 128-bit (16-byte) key via libsodium."
    );

    // ——— ring arithmetic (scalar) ———

    m.def(
        "ring_mask",
        [](uint64_t ell) -> uint64_t {
            if (ell >= 64) return ~UINT64_C(0);
            return (UINT64_C(1) << ell) - 1;
        },
        "ell"_a,
        "Return the ring mask for Z_{2^ELL}."
    );

    m.def(
        "ring_add",
        [](uint64_t ell, uint64_t a, uint64_t b) -> uint64_t {
            uint64_t mask = (ell >= 64) ? ~UINT64_C(0) : ((UINT64_C(1) << ell) - 1);
            return (a + b) & mask;
        },
        "ell"_a, "a"_a, "b"_a,
        "Ring addition: (a + b) mod 2^ELL."
    );

    m.def(
        "ring_sub",
        [](uint64_t ell, uint64_t a, uint64_t b) -> uint64_t {
            uint64_t mask = (ell >= 64) ? ~UINT64_C(0) : ((UINT64_C(1) << ell) - 1);
            return (a - b) & mask;
        },
        "ell"_a, "a"_a, "b"_a,
        "Ring subtraction: (a - b) mod 2^ELL."
    );

    m.def(
        "ring_mul",
        [](uint64_t ell, uint64_t a, uint64_t b) -> uint64_t {
            uint64_t mask = (ell >= 64) ? ~UINT64_C(0) : ((UINT64_C(1) << ell) - 1);
            return (a * b) & mask;
        },
        "ell"_a, "a"_a, "b"_a,
        "Ring multiplication: (a * b) mod 2^ELL."
    );

    m.def(
        "ring_mod",
        [](uint64_t ell, uint64_t val, uint64_t mod) -> uint64_t {
            uint64_t mask = (ell >= 64) ? ~UINT64_C(0) : ((UINT64_C(1) << ell) - 1);
            if (val > mask)
                throw std::invalid_argument(
                    "ring_mod: val out of range for Z_{2^" + std::to_string(ell) + "}");
            if (mod > mask)
                throw std::invalid_argument(
                    "ring_mod: mod out of range for Z_{2^" + std::to_string(ell) + "}");
            if (mod == 0)
                throw std::invalid_argument("ring_mod: mod must not be zero");
            return val % mod;
        },
        "ell"_a, "val"_a, "mod"_a,
        "Ring modulo: val % mod (both operands must be in Z_{2^ELL})."
    );

    // ——— NetIO helpers ———
    //
    //  NetIO objects are stored as opaque uintptr_t values in Python
    //  because NetIO is not registered as a nanobind type.  The helpers
    //  cast to/from raw pointers.  Channel() constructs the NetIO and
    //  Channel.__del__() destroys it via _netio_delete.

    m.def(
        "NetIO_connect",
        [](const char* host, int port) -> uintptr_t {
            auto* p = new emp::NetIO(host, port, /*quiet=*/true);
            return reinterpret_cast<uintptr_t>(p);
        },
        nb::call_guard<nb::gil_scoped_release>(),
        "host"_a, "port"_a,
        "Create a NetIO client connected to *host:port*.  Retries until the server is ready."
    );

    m.def(
        "NetIO_listen",
        [](int port) -> uintptr_t {
            auto* p = new emp::NetIO(nullptr, port, /*quiet=*/true);
            return reinterpret_cast<uintptr_t>(p);
        },
        nb::call_guard<nb::gil_scoped_release>(),
        "port"_a,
        "Create a NetIO server listening on *port*.  Blocks until a client connects."
    );

    m.def(
        "_netio_delete",
        [](uintptr_t ptr) {
            delete reinterpret_cast<emp::NetIO*>(ptr);
        },
        "ptr"_a,
        "Delete a NetIO instance.  Called from Channel.__del__."
    );

    m.def(
        "_netio_flush",
        [](uintptr_t ptr) {
            reinterpret_cast<emp::NetIO*>(ptr)->flush();
        },
        nb::call_guard<nb::gil_scoped_release>(),
        "ptr"_a,
        "Flush the NetIO send buffer."
    );

    m.def(
        "_netio_send",
        [](uintptr_t ptr, nb::object data) {
            auto* p = reinterpret_cast<emp::NetIO*>(ptr);
            PyObject* py_buf = data.ptr();
            char* buf = nullptr;
            Py_ssize_t len = 0;
            if (PyBytes_Check(py_buf)) {
                buf = PyBytes_AsString(py_buf);
                len = PyBytes_Size(py_buf);
            } else if (PyByteArray_Check(py_buf)) {
                buf = PyByteArray_AsString(py_buf);
                len = PyByteArray_Size(py_buf);
            } else {
                throw std::invalid_argument("_netio_send: data must be bytes or bytearray");
            }
            p->send_data(buf, len);
            p->flush();
        },
        nb::call_guard<nb::gil_scoped_release>(),
        "ptr"_a, "data"_a,
        "Send raw bytes over the NetIO (with flush)."
    );

    m.def(
        "_netio_recv",
        [](uintptr_t ptr, nb::object buf) {
            auto* p = reinterpret_cast<emp::NetIO*>(ptr);
            PyObject* py_buf = buf.ptr();
            if (!PyByteArray_Check(py_buf))
                throw std::invalid_argument("_netio_recv: buf must be a bytearray");
            char* data = PyByteArray_AsString(py_buf);
            Py_ssize_t len = PyByteArray_Size(py_buf);
            p->recv_data(data, len);
        },
        nb::call_guard<nb::gil_scoped_release>(),
        "ptr"_a, "buf"_a,
        "Receive raw bytes from the NetIO into a pre-allocated bytearray."
    );

    m.def(
        "_netio_clear_counters",
        [](uintptr_t ptr) {
            auto* p = reinterpret_cast<emp::NetIO*>(ptr);
            p->send_counter = 0;
            p->recv_counter = 0;
        },
        "ptr"_a,
        "Reset send/recv byte counters to zero."
    );

    m.def(
        "NetIO_from_socket",
        [](int sock_fd) -> uintptr_t {
            auto* p = new emp::NetIO(sock_fd, /*quiet=*/true);
            return reinterpret_cast<uintptr_t>(p);
        },
        nb::call_guard<nb::gil_scoped_release>(),
        "sock_fd"_a,
        "Wrap an existing connected socket fd into a NetIO instance. "
        "NetIO takes ownership of the fd and closes it on destruction."
    );

    m.def(
        "_netio_as_iochannel",
        [](uintptr_t ptr) -> uintptr_t {
            auto* netio = reinterpret_cast<emp::NetIO*>(ptr);
            return reinterpret_cast<uintptr_t>(static_cast<emp::IOChannel*>(netio));
        },
        "ptr"_a,
        "Return the IOChannel* pointer as an integer, for passing to protocol constructors."
    );

    // ——— local AES-DM hash ———

    m.def(
        "hash_aes_dm",
        [](nb::object preimage, nb::object key, uint64_t ell) -> uint32_t {
            auto [pt, ptLen] = _toBytes(preimage);
            auto [ky, kyLen] = _toBytes(key);
            return hashAESDM(pt, ptLen, ky, kyLen, ell);
        },
        "preimage"_a, "key"_a, "ell"_a,
        "Local AES-DM hash (no network).\n"
        "  preimage, key — bytes or str (str encoded as UTF-8).\n"
        "  Both are zero-padded to 16 bytes internally."
    );
}
