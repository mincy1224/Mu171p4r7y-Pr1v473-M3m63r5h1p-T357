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
                // RAII guard — released even if batchSet throws
                auto guard = [](Py_buffer* b) { PyBuffer_Release(b); };
                std::unique_ptr<Py_buffer, decltype(guard)> _(&buf, guard);
                if (buf.itemsize != sizeof(uint64_t))
                    throw std::invalid_argument("batch_set: indices must be uint64_t array");
                size_t n = buf.len / sizeof(uint64_t);
                v.batchSet(static_cast<const uint64_t*>(buf.buf), n, val);
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
                auto guard = [](Py_buffer* b) { PyBuffer_Release(b); };
                std::unique_ptr<Py_buffer, decltype(guard)> _(&idxBuf, guard);
                if (idxBuf.itemsize != sizeof(uint64_t))
                    throw std::invalid_argument("batch_get: indices must be uint64_t array");
                size_t n = idxBuf.len / sizeof(uint64_t);
                if (out.size() < n)
                    throw std::invalid_argument("batch_get: out.size() must be >= n");
                v.batchGet(static_cast<const uint64_t*>(idxBuf.buf), n, out);
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
                size_t expected = v.bytesSize();
                if (b.size() != expected)
                    throw std::invalid_argument(
                        "from_bytes: expected " + std::to_string(expected)
                        + " bytes, got " + std::to_string(b.size()));
                std::memcpy(v.data(), b.c_str(), expected);
                v.canonicalize();
            }
        )

        //  file I/O  (aux_buf = RvectorPack — caller-allocated buffer)
        .def("save", &Rv::save, nb::call_guard<nb::gil_scoped_release>(), "path"_a, "aux_buf"_a,
             "Save to file (compact format: [ELL, n, payload]), uses caller-provided buffer")
        .def("load", &Rv::load, nb::call_guard<nb::gil_scoped_release>(), "path"_a, "aux_buf"_a,
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
                throw std::invalid_argument(
                    "ell out of range [" + std::to_string(bind::RVECTOR_ELL_MIN)
                    + ", " + std::to_string(bind::RVECTOR_ELL_MAX) + "]");
            }
            return rv_types[ell];
        },
        "ell"_a,
        "Return the Rvector class for the given ell (template alias)."
    );

    //  rvector_pack / rvector_unpack — module-level (not class-bound)
    m.def(
        "rvector_pack",
        [](nb::object src, math::RvectorPack& aux_buf) {
            bool found = false;
            bind::for_range<bind::RVECTOR_ELL_MIN, bind::RVECTOR_ELL_MAX>(
                [&]<uint64_t ELL>() {
                    if (found) return;
                    math::Rvector<ELL>* v = nullptr;
                    if (nb::try_cast(src, v)) { packRvec(*v, aux_buf); found = true; }
                });
            if (!found)
                throw nb::type_error("rvector_pack: src must be an Rvector instance");
        },
        "src"_a, "aux_buf"_a,
        "Pack an Rvector into a pre-allocated RvectorPack buffer."
    );
    m.def(
        "rvector_unpack",
        [](const math::RvectorPack& aux_buf, nb::object dst) {
            bool found = false;
            bind::for_range<bind::RVECTOR_ELL_MIN, bind::RVECTOR_ELL_MAX>(
                [&]<uint64_t ELL>() {
                    if (found) return;
                    math::Rvector<ELL>* v = nullptr;
                    if (nb::try_cast(dst, v)) { unpackRvec(aux_buf, *v); found = true; }
                });
            if (!found)
                throw nb::type_error("rvector_unpack: dst must be an Rvector instance");
        },
        "aux_buf"_a, "dst"_a,
        "Unpack an RvectorPack buffer into a pre-allocated Rvector."
    );

    void bind_transport(nb::module_&);
    bind_transport(m);

    void bind_util(nb::module_&);
    bind_util(m);

    void bind_dpf(nb::module_&);
    bind_dpf(m);

    void bind_shr_rss3(nb::module_&);
    bind_shr_rss3(m);

    void bind_shr_add2(nb::module_&);
    bind_shr_add2(m);
}
