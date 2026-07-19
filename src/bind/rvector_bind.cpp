//  @author  mincy
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>

#include <sodium/core.h>
#include <cstdint>
#include <stdexcept>

#include "common.hpp"
#include "bind_common.hpp"

namespace nb = nanobind;
using namespace scucse::crypto;

//  Bind RVECTOR<ELL>
template <uint64_t ELL> static void bind_rvector(nb::module_& m, const char* name)
{
    using namespace nb::literals;
    using Rv = bind::RVECTOR<ELL>;

    nb::class_<Rv>(m, name)

        //  constructors
        .def(nb::init<size_t>(), "size"_a, "Allocate a vector of given size (uninitialised)")
        .def(
            "__init__",
            [](Rv* self, size_t size, uint8_t val)
            {
                new (self) Rv(size);
                self->fill(val);
            },
            "size"_a,
            "val"_a,
            "Allocate a vector of given size, filled with val"
        )

        //  Python protocol
        .def("__len__", &Rv::size)
        .def(
            "__getitem__",
            [](const Rv& v, size_t i) -> uint8_t
            {
                if (i >= v.size())
                {
                    throw std::out_of_range("index out of range");
                }
                return v.get(i);
            }
        )
        .def(
            "__setitem__",
            [](Rv& v, size_t i, uint8_t val)
            {
                if (i >= v.size())
                {
                    throw std::out_of_range("index out of range");
                }
                v.set(i, val);
            }
        )
        .def(
            "__repr__",
            [cls_name = std::string(name)](const Rv& v)
            {
                std::string s(cls_name);
                s += "(size=" + std::to_string(v.size()) + ", [";
                size_t n = std::min<size_t>(v.size(), size_t{8});
                for (size_t i = 0; i < n; ++i)
                {
                    if (i)
                    {
                        s += ", ";
                    }
                    s += std::to_string(v.get(i));
                }
                if (v.size() > n)
                {
                    s += ", ...";
                }
                s += "])";
                return s;
            }
        )

        .def(
            "__eq__",
            [](const Rv& a, const Rv& b)
            {
                if (a.size() != b.size())
                {
                    return false;
                }
                for (size_t i = 0; i < a.size(); ++i)
                {
                    if (a.get(i) != b.get(i))
                    {
                        return false;
                    }
                }
                return true;
            }
        )

        //  properties
        .def_prop_ro("size", &Rv::size)

        //  fill
        .def("fill", &Rv::fill, "val"_a = uint8_t{0})
        .def("rand_fill", &Rv::randFill)

        //  batch set / get  (zero-copy via buffer protocol, n = len(indices) / 8)
        .def(
            "batch_set",
            [](Rv& v, nb::object indices, uint8_t val) {
                Py_buffer buf;
                if (PyObject_GetBuffer(indices.ptr(), &buf, PyBUF_SIMPLE) != 0)
                    throw nb::python_error();
                if (buf.itemsize != sizeof(uint64_t))
                {
                    PyBuffer_Release(&buf);
                    throw std::invalid_argument("batch_set: indices must be uint64_t array");
                }
                size_t n = buf.len / sizeof(uint64_t);
                v.batchSet(static_cast<const uint64_t*>(buf.buf), n, val);
                PyBuffer_Release(&buf);
            },
            "indices"_a, "val"_a,
            "Set v[indices[i]] = val for all i.  indices must be a uint64_t buffer (e.g. array('Q'))."
        )
        .def(
            "batch_get",
            [](const Rv& v, nb::object indices, Rv& out) {
                Py_buffer idxBuf;
                if (PyObject_GetBuffer(indices.ptr(), &idxBuf, PyBUF_SIMPLE) != 0)
                    throw nb::python_error();
                if (idxBuf.itemsize != sizeof(uint64_t))
                {
                    PyBuffer_Release(&idxBuf);
                    throw std::invalid_argument("batch_get: indices must be uint64_t array");
                }
                size_t n = idxBuf.len / sizeof(uint64_t);
                if (out.size() < n)
                {
                    PyBuffer_Release(&idxBuf);
                    throw std::invalid_argument("batch_get: out.size() must be >= n");
                }
                v.batchGet(static_cast<const uint64_t*>(idxBuf.buf), n, out);
                PyBuffer_Release(&idxBuf);
            },
            "indices"_a, "out"_a,
            "Write v[indices[i]] → out[i] for all i.  indices must be a uint64_t buffer."
        )

        //  serialization — raw storage bytes
        .def(
            "to_bytes",
            [](const Rv& v)
            { return nb::bytes(reinterpret_cast<const char*>(v.bytes()), v.bytesSize()); }
        )
        .def(
            "from_bytes",
            [](Rv& v, const nb::bytes& b)
            {
                size_t n = std::min<size_t>(b.size(), v.bytesSize());
                std::memcpy(v.data(), b.c_str(), n);
            }
        )

        //  file I/O  (auxBuf = RvectorPack — caller-allocated buffer)
        .def("save", &Rv::save, nb::call_guard<nb::gil_scoped_release>(), "path"_a, "auxBuf"_a,
             "Save to file (compact format: [ELL, n, payload]), uses caller-provided buffer")
        .def("load", &Rv::load, nb::call_guard<nb::gil_scoped_release>(), "path"_a, "auxBuf"_a,
             "Load from file; checks that on-disk ELL and n match")

        //  static arithmetic  (vector-vector)
        .def_static(
            "add",
            [](const Rv& a, const Rv& b, Rv& out) { Rv::add(a, b, out); },
            "a"_a,
            "b"_a,
            "out"_a,
            "out = a + b  (mod 2^ELL)"
        )
        .def_static(
            "sub",
            [](const Rv& a, const Rv& b, Rv& out) { Rv::sub(a, b, out); },
            "a"_a,
            "b"_a,
            "out"_a,
            "out = a - b  (mod 2^ELL)"
        )
        .def_static(
            "hadamard",
            [](const Rv& a, const Rv& b, Rv& out) { Rv::hadamard(a, b, out); },
            "a"_a,
            "b"_a,
            "out"_a,
            "out[i] = a[i] * b[i]  (mod 2^ELL)"
        )

        //  static arithmetic  (vector-scalar)
        .def_static(
            "add_scalar",
            [](const Rv& a, uint8_t s, Rv& out) { Rv::addScalar(a, s, out); },
            "a"_a,
            "scalar"_a,
            "out"_a,
            "out = a + scalar  (mod 2^ELL)"
        )
        .def_static(
            "sub_scalar",
            [](const Rv& a, uint8_t s, Rv& out) { Rv::subScalar(a, s, out); },
            "a"_a,
            "scalar"_a,
            "out"_a,
            "out = a - scalar  (mod 2^ELL)"
        )
        .def_static(
            "mul_scalar",
            [](const Rv& a, uint8_t s, Rv& out) { Rv::mulScalar(a, s, out); },
            "a"_a,
            "scalar"_a,
            "out"_a,
            "out = a * scalar  (mod 2^ELL)"
        )

        //  static reductions
        .def_static(
            "dot",
            [](const Rv& a, const Rv& b) { return Rv::dot(a, b); },
            "a"_a,
            "b"_a,
            "Σ a[i]·b[i]  mod 2^ELL"
        )
        .def_static(
            "reduce", [](const Rv& a) { return Rv::reduce(a); }, "a"_a, "Σ a[i]  mod 2^ELL"
        );
}

// ———  RingTransport  ———  ring element send/recv over a Channel                           ———
//                                                                                             —
//   ELL 1–8 :  vector-capable   send_vector / recv_vector  (packed Rvector)                   —
//   ELL 9–31:  scalar-only      send_scalar / recv_scalar  (raw ring element)                 —
// —————————————————————————————————————————————————————————————————————————————————————————————

template <uint64_t ELL> struct RingVectorTransport
{
    static_assert(ELL >= 1 && ELL <= 8, "ELL 1-8 for vector transport");
    emp::IOChannel* io_;
    RingVectorTransport(emp::IOChannel* io) : io_(io) {}

    void send_vector(const math::Rvector<ELL>& vec, math::RvectorPack& auxBuf)
    {
        packRvec(vec, auxBuf);
        io_->send_data(auxBuf.data(), auxBuf.size());
    }
    void recv_vector(math::Rvector<ELL>& vec, math::RvectorPack& auxBuf)
    {
        io_->recv_data(auxBuf.data(), auxBuf.size());
        unpackRvec(auxBuf, vec);
    }
};

template <uint64_t ELL> struct RingScalarTransport
{
    static_assert(ELL >= 1 && ELL <= 64, "ELL 1-64 for scalar transport");
    emp::IOChannel* io_;
    RingScalarTransport(emp::IOChannel* io) : io_(io) {}
    static constexpr size_t BYTES = (ELL + 7) / 8;

    void send_scalar(uint64_t val)
    {
        if constexpr (ELL < 64) {
            uint64_t mask = (UINT64_C(1) << ELL) - 1;
            if (val > mask)
                throw std::invalid_argument(
                    "send_scalar(" + std::to_string(val)
                    + "): value out of range for Z_{2^" + std::to_string(ELL) + "}");
        }
        uint8_t buf[BYTES];
        for (size_t i = 0; i < BYTES; ++i) buf[i] = static_cast<uint8_t>(val >> (8 * i));
        io_->send_data(buf, BYTES);
        io_->flush();
    }
    uint64_t recv_scalar()
    {
        uint8_t buf[BYTES] = {};
        io_->recv_data(buf, BYTES);
        uint64_t val = 0;
        for (size_t i = 0; i < BYTES; ++i) val |= static_cast<uint64_t>(buf[i]) << (8 * i);
        if constexpr (ELL < 64) { val &= (UINT64_C(1) << ELL) - 1; }
        return val;
    }
};

static constexpr int RING_TRANSPORT_MAX = 31;

template <uint64_t ELL> static void bind_ring_transport(nb::module_& m, const char* name)
{
    using namespace nb::literals;

    if constexpr (ELL <= 8)
    {
        using T = RingVectorTransport<ELL>;
        nb::class_<T>(m, name)
            .def("__init__",
                 [](T* self, nb::object channel) {
                     auto* io = reinterpret_cast<emp::IOChannel*>(
                         nb::cast<uintptr_t>(channel.attr("acquire")()));
                     new (self) T(io);
                 },
                 "channel"_a, "RingTransport(ell)(channel) — vector send/recv.")
            .def_prop_ro("ell", [](const T&) { return ELL; })
            .def("send_vector", &T::send_vector, nb::call_guard<nb::gil_scoped_release>(),
                 "vec"_a, "auxBuf"_a, "Pack vec into auxBuf and send over the channel.")
            .def("recv_vector", &T::recv_vector, nb::call_guard<nb::gil_scoped_release>(),
                 "vec"_a, "auxBuf"_a, "Receive into auxBuf and unpack into vec.");
    }
    else
    {
        using T = RingScalarTransport<ELL>;
        nb::class_<T>(m, name)
            .def("__init__",
                 [](T* self, nb::object channel) {
                     auto* io = reinterpret_cast<emp::IOChannel*>(
                         nb::cast<uintptr_t>(channel.attr("acquire")()));
                     new (self) T(io);
                 },
                 "channel"_a, "RingTransport(ell)(channel) — scalar send/recv.")
            .def_prop_ro("ell", [](const T&) { return ELL; })
            .def("send_scalar", &T::send_scalar, nb::call_guard<nb::gil_scoped_release>(),
                 "val"_a, "Send a scalar ring element (ELL bits).")
            .def("recv_scalar", &T::recv_scalar, nb::call_guard<nb::gil_scoped_release>(),
                 "Receive a scalar ring element (ELL bits).");
    }
}

//  Module
NB_MODULE(_mpmt, m)
{
    if (sodium_init() < 0)
    {
        throw std::runtime_error("libsodium init failed");
    }

    using namespace nb::literals;
    m.doc() = "MPMT — Multiparty Private Membership Test Python bindings";

    //  _RvectorPack  — internal C++ type (used via RvectorPack factory)
    nb::class_<math::RvectorPack>(m, "_RvectorPack")
        .def(nb::init<uint64_t, size_t>(), "ell"_a, "n"_a)
        .def_prop_ro("size", &math::RvectorPack::size)
        .def_prop_ro("ell", &math::RvectorPack::ell)
        .def_prop_ro("n_elements", &math::RvectorPack::nElements)
        .def("to_bytes",
             [](const math::RvectorPack& p) {
                 return nb::bytes(reinterpret_cast<const char*>(p.data()), p.size());
             });

    //  RvectorPack(ell) → factory; factory(n) → _RvectorPack(ell, n)
    m.def(
        "RvectorPack",
        [](uint64_t ell) -> nb::object {
            return nb::cpp_function(
                [ell](size_t n) -> math::RvectorPack {
                    return math::RvectorPack(ell, n);
                },
                nb::arg("n"));
        },
        "ell"_a,
        "Return a factory: RvectorPack(ell)(n) allocates a buffer for n elements in Z_{2^ell}."
    );

    //  RvectorEll1 .. RvectorEll8  —  compile-time iteration
    static nb::object rv_types[bind::RVECTOR_ELL_MAX + 1];

    bind::for_range<bind::RVECTOR_ELL_MIN, bind::RVECTOR_ELL_MAX>(
        [&]<uint64_t ELL>()
        {
            char name[32];
            std::snprintf(name, sizeof(name), "RvectorEll%lu", ELL);
            bind_rvector<ELL>(m, name);
            rv_types[ELL] = m.attr(name);
        }
    );

    //  alias: Rvector(ell) → RvectorEll<ell>  class
    m.def(
        "Rvector",
        [](uint64_t ell) -> nb::object
        {
            if (ell < bind::RVECTOR_ELL_MIN || ell > bind::RVECTOR_ELL_MAX)
            {
                throw std::invalid_argument("ell out of range");
            }
            return rv_types[ell];
        },
        "ell"_a,
        "Return the Rvector class for the given ell (template alias)."
    );

    //  rvector_pack / rvector_unpack — module-level (not class-bound)
    m.def(
        "rvector_pack",
        [](nb::object src, math::RvectorPack& auxBuf) {
            bool found = false;
            bind::for_range<bind::RVECTOR_ELL_MIN, bind::RVECTOR_ELL_MAX>(
                [&]<uint64_t ELL>() {
                    if (found) return;
                    math::Rvector<ELL>* v = nullptr;
                    if (nb::try_cast(src, v)) { packRvec(*v, auxBuf); found = true; }
                });
            if (!found)
                throw nb::type_error("rvector_pack: src must be an Rvector instance");
        },
        "src"_a, "auxBuf"_a,
        "Pack an Rvector into a pre-allocated RvectorPack buffer."
    );
    m.def(
        "rvector_unpack",
        [](const math::RvectorPack& auxBuf, nb::object dst) {
            bool found = false;
            bind::for_range<bind::RVECTOR_ELL_MIN, bind::RVECTOR_ELL_MAX>(
                [&]<uint64_t ELL>() {
                    if (found) return;
                    math::Rvector<ELL>* v = nullptr;
                    if (nb::try_cast(dst, v)) { unpackRvec(auxBuf, *v); found = true; }
                });
            if (!found)
                throw nb::type_error("rvector_unpack: dst must be an Rvector instance");
        },
        "auxBuf"_a, "dst"_a,
        "Unpack an RvectorPack buffer into a pre-allocated Rvector."
    );

    //  RingTransportEll1 .. RingTransportEll31  —  per-ELL classes
    static nb::object ring_transport_types[RING_TRANSPORT_MAX + 1];

    bind::for_range<1, RING_TRANSPORT_MAX>(
        [&]<uint64_t ELL>()
        {
            char name[64];
            std::snprintf(name, sizeof(name), "RingTransportEll%lu", ELL);
            bind_ring_transport<ELL>(m, name);
            ring_transport_types[ELL] = m.attr(name);
        }
    );

    //  Factory: RingTransport(ell) → class; class(channel) → instance
    m.def(
        "RingTransport",
        [](uint64_t ell) -> nb::object
        {
            if (ell < 1 || ell > RING_TRANSPORT_MAX)
                throw std::invalid_argument("ell out of range [1, 31]");
            return ring_transport_types[ell];
        },
        "ell"_a,
        "RingTransport(ell)(channel) — ring element transport over a Channel.\n"
        "  ELL 1–8  →  send_vector / recv_vector  (packed Rvector)\n"
        "  ELL 9–31 →  send_scalar / recv_scalar  (raw ring element)"
    );

    void bind_util(nb::module_&);
    bind_util(m);

    void bind_dpf(nb::module_&);
    bind_dpf(m);

    void bind_shr_rss3(nb::module_&);
    bind_shr_rss3(m);

    void bind_shr_add2(nb::module_&);
    bind_shr_add2(m);
}
