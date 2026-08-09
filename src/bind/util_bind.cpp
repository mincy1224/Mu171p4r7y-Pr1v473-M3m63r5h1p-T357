//  @author  mincy
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>

#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <unordered_map>

#include <sodium/randombytes.h>

#include "common.hpp"
#include "bind_common.hpp"

namespace nb = nanobind;
using namespace scucse::crypto;
using bind::_toBytes;
using bind::_getByteBuffer;

// ———  NetIO shared_ptr registry  ————————————————————————————————————————
//  NetIO instances are managed via std::shared_ptr so that protocol objects
//  (DpfDealerT, DpfEvaluatorT, Rss3T, Add2T, RingTransport) can share a
//  single IOChannel safely.  Python holds an opaque uintptr_t handle; C++
//  wrappers call netio_acquire() to obtain a shared_ptr copy.
namespace {
    std::mutex                      g_registry_mutex;
    std::unordered_map<uintptr_t, std::shared_ptr<emp::NetIO>> g_registry;
    std::atomic<uintptr_t>          g_next_handle{1};
}

namespace scucse::crypto::bind {

std::shared_ptr<emp::IOChannel> netio_acquire(uintptr_t handle)
{
    std::lock_guard lock(g_registry_mutex);
    auto it = g_registry.find(handle);
    if (it == g_registry.end())
        throw std::runtime_error(
            "netio_acquire: handle " + std::to_string(handle) + " no longer valid");
    return it->second;  // copies shared_ptr, increments ref count
}

void netio_release(uintptr_t handle)
{
    std::lock_guard lock(g_registry_mutex);
    g_registry.erase(handle);
    // NetIO is only deleted when the last shared_ptr copy is destroyed.
}

uintptr_t netio_register(std::shared_ptr<emp::NetIO> sp)
{
    uintptr_t h = g_next_handle.fetch_add(1);
    std::lock_guard lock(g_registry_mutex);
    g_registry[h] = std::move(sp);
    return h;
}

} // namespace scucse::crypto::bind

namespace {

    /// Look up a NetIO shared_ptr from its handle. The returned shared_ptr
    /// keeps the NetIO alive for the duration of the call, preventing
    /// concurrent release from destroying it mid-operation.
    inline std::shared_ptr<emp::NetIO> _lookup(uintptr_t handle)
    {
        std::lock_guard lock(g_registry_mutex);
        auto it = g_registry.find(handle);
        if (it == g_registry.end())
            throw std::runtime_error(
                "NetIO handle " + std::to_string(handle) + " is no longer valid");
        return it->second;
    }

} // anonymous namespace

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
            if (ell < 1 || ell > 31)
                throw std::invalid_argument(
                    "ring_mask: ell must be in [1, 31], got " + std::to_string(ell));
            return (UINT64_C(1) << ell) - 1;
        },
        "ell"_a,
        "Return the ring mask for Z_{2^ELL}, 1 ≤ ell ≤ 31."
    );

    m.def(
        "ring_add",
        [](uint64_t ell, uint64_t a, uint64_t b) -> uint64_t {
            if (ell < 1 || ell > 31)
                throw std::invalid_argument(
                    "ring_add: ell must be in [1, 31], got " + std::to_string(ell));
            uint64_t mask = (UINT64_C(1) << ell) - 1;
            if (a > mask)
                throw std::invalid_argument(
                    "ring_add: a=" + std::to_string(a)
                    + " out of range for Z_{2^" + std::to_string(ell) + "}");
            if (b > mask)
                throw std::invalid_argument(
                    "ring_add: b=" + std::to_string(b)
                    + " out of range for Z_{2^" + std::to_string(ell) + "}");
            return (a + b) & mask;
        },
        "ell"_a, "a"_a, "b"_a,
        "Ring addition: (a + b) mod 2^ELL.  a, b must be in Z_{2^ELL}."
    );

    m.def(
        "ring_sub",
        [](uint64_t ell, uint64_t a, uint64_t b) -> uint64_t {
            if (ell < 1 || ell > 31)
                throw std::invalid_argument(
                    "ring_sub: ell must be in [1, 31], got " + std::to_string(ell));
            uint64_t mask = (UINT64_C(1) << ell) - 1;
            if (a > mask)
                throw std::invalid_argument(
                    "ring_sub: a=" + std::to_string(a)
                    + " out of range for Z_{2^" + std::to_string(ell) + "}");
            if (b > mask)
                throw std::invalid_argument(
                    "ring_sub: b=" + std::to_string(b)
                    + " out of range for Z_{2^" + std::to_string(ell) + "}");
            return (a - b) & mask;
        },
        "ell"_a, "a"_a, "b"_a,
        "Ring subtraction: (a - b) mod 2^ELL.  a, b must be in Z_{2^ELL}."
    );

    m.def(
        "ring_mul",
        [](uint64_t ell, uint64_t a, uint64_t b) -> uint64_t {
            if (ell < 1 || ell > 31)
                throw std::invalid_argument(
                    "ring_mul: ell must be in [1, 31], got " + std::to_string(ell));
            uint64_t mask = (UINT64_C(1) << ell) - 1;
            if (a > mask)
                throw std::invalid_argument(
                    "ring_mul: a=" + std::to_string(a)
                    + " out of range for Z_{2^" + std::to_string(ell) + "}");
            if (b > mask)
                throw std::invalid_argument(
                    "ring_mul: b=" + std::to_string(b)
                    + " out of range for Z_{2^" + std::to_string(ell) + "}");
            return (a * b) & mask;
        },
        "ell"_a, "a"_a, "b"_a,
        "Ring multiplication: (a * b) mod 2^ELL.  a, b must be in Z_{2^ELL}."
    );

    m.def(
        "ring_mod",
        [](uint64_t ell, uint64_t val, uint64_t mod) -> uint64_t {
            if (ell < 1 || ell > 31)
                throw std::invalid_argument(
                    "ring_mod: ell must be in [1, 31], got " + std::to_string(ell));
            uint64_t mask = (UINT64_C(1) << ell) - 1;
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
    //  NetIO instances are managed via std::shared_ptr behind an opaque
    //  uintptr_t handle.  Python Channel objects hold the handle; C++
    //  protocol wrappers call netio_acquire(handle) to share ownership.

    m.def(
        "NetIO_connect",
        [](const char* host, int port) -> uintptr_t {
            auto sp = std::make_shared<emp::NetIO>(host, port, /*quiet=*/true);
            return bind::netio_register(std::move(sp));
        },
        nb::call_guard<nb::gil_scoped_release>(),
        "host"_a, "port"_a,
        "Create a NetIO client connected to *host:port*.  "
        "Returns an opaque handle for lifecycle management."
    );

    m.def(
        "NetIO_listen",
        [](int port) -> uintptr_t {
            auto sp = std::make_shared<emp::NetIO>(nullptr, port, /*quiet=*/true);
            return bind::netio_register(std::move(sp));
        },
        nb::call_guard<nb::gil_scoped_release>(),
        "port"_a,
        "Create a NetIO server listening on *port*.  "
        "Returns an opaque handle for lifecycle management."
    );

    m.def(
        "_netio_delete",  // kept for backwards compat — now just releases
        [](uintptr_t handle) {
            bind::netio_release(handle);
        },
        "handle"_a,
        "Release the NetIO handle.  Actual deletion occurs when all "
        "protocol objects sharing this NetIO are also destroyed."
    );

    m.def(
        "_netio_release",
        [](uintptr_t handle) {
            bind::netio_release(handle);
        },
        "handle"_a,
        "Release the NetIO handle (alias for _netio_delete)."
    );

    m.def(
        "_netio_flush",
        [](uintptr_t handle) {
            _lookup(handle)->flush();
        },
        nb::call_guard<nb::gil_scoped_release>(),
        "handle"_a,
        "Flush the NetIO send buffer."
    );

    m.def(
        "_netio_send",
        [](uintptr_t handle, nb::object data) {
            // Extract C-string from Python object BEFORE releasing the GIL
            // (nb::object destructor needs the GIL; manually scope the release).
            auto [buf, len] = _getByteBuffer(data);
            auto p = _lookup(handle);
            {
                nb::gil_scoped_release release;
                p->send_data(buf, static_cast<size_t>(len));
                p->flush();
            }
        },
        "handle"_a, "data"_a,
        "Send raw bytes over the NetIO (with flush)."
    );

    m.def(
        "_netio_recv",
        [](uintptr_t handle, nb::object buf) {
            // Extract bytearray buffer BEFORE releasing the GIL.
            PyObject* py_buf = buf.ptr();
            if (!PyByteArray_Check(py_buf))
                throw std::invalid_argument("_netio_recv: buf must be a bytearray");
            char* data = PyByteArray_AsString(py_buf);
            Py_ssize_t len = PyByteArray_Size(py_buf);
            auto p = _lookup(handle);
            {
                nb::gil_scoped_release release;
                p->recv_data(data, static_cast<size_t>(len));
            }
        },
        "handle"_a, "buf"_a,
        "Receive raw bytes from the NetIO into a pre-allocated bytearray."
    );

    m.def(
        "_netio_clear_counters",
        [](uintptr_t handle) {
            auto sp = _lookup(handle);
            sp->send_counter = 0;
            sp->recv_counter = 0;
        },
        "handle"_a,
        "Reset send/recv byte counters to zero."
    );

    m.def(
        "NetIO_from_socket",
        [](int sock_fd) -> uintptr_t {
            auto sp = std::make_shared<emp::NetIO>(sock_fd, /*quiet=*/true);
            return bind::netio_register(std::move(sp));
        },
        nb::call_guard<nb::gil_scoped_release>(),
        "sock_fd"_a,
        "Wrap an existing connected socket fd into a NetIO instance. "
        "NetIO takes ownership of the fd and closes it on destruction."
    );

    m.def(
        "_netio_as_iochannel",
        [](uintptr_t handle) -> uintptr_t {
            return handle;  // handle is now the universal identifier
        },
        "handle"_a,
        "Return the IOChannel handle (identity for protocol construction)."
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